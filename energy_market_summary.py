"""Integrated power market summary: ENTSO-E online queries and offline CSV reporting.
Install dependencies from requirements.txt. Run with --help for command-line options.
Notebook users can call load_offline, load_online, run_analysis and save_report.
"""
from pathlib import Path
import json
import os
import csv
import argparse
import numpy as np
import pandas as pd

RENEWABLES = ['Wind Offshore', 'Wind Onshore', 'Solar']


def utc_index(obj, naive_timezone=None):
    obj = obj.copy()
    # Require an explicit interpretation for naive timestamps.
    parsed = [pd.Timestamp(x) for x in obj.index]
    if any(pd.isna(x) for x in parsed):
        raise ValueError('Missing timestamp in index.')
    if any(x.tzinfo is None for x in parsed):
        if not all(x.tzinfo is None for x in parsed) or not naive_timezone:
            raise ValueError('Naive/mixed timestamps: supply input_timezone explicitly.')
        obj.index = pd.DatetimeIndex(parsed).tz_localize(naive_timezone).tz_convert('UTC')
    else:
        obj.index = pd.to_datetime(obj.index, utc=True)
    if obj.index.has_duplicates:
        raise ValueError('Duplicate timestamps: resolve duplicates before reporting.')
    return obj.sort_index()


def cadence(index, minutes=None):
    if minutes is not None:
        step = pd.Timedelta(minutes=minutes)
    else:
        diffs = index.to_series().diff().dropna()
        if diffs.empty:
            raise ValueError('At least two timestamps or an explicit interval are required.')
        step = diffs.mode().iloc[0]
    if step <= pd.Timedelta(0):
        raise ValueError('Interval must be positive.')
    return step


def generation_columns(frame):
    """Select actual generation, excluding pumped-storage consumption."""
    if isinstance(frame.columns, pd.MultiIndex):
        selected = frame.loc[:, frame.columns.get_level_values(1) == 'Actual Aggregated'].copy()
        selected.columns = selected.columns.get_level_values(0)
    else:
        selected = frame.copy()  # Flat input must contain generation types only.
    if selected.columns.has_duplicates:
        raise ValueError('Duplicate generation types after selecting Actual Aggregated.')
    return selected.apply(pd.to_numeric, errors='raise')


def regularise(obj, start, end, step):
    grid = pd.date_range(start, end, freq=step, inclusive='left')
    inside = obj.loc[(obj.index >= start) & (obj.index < end)]
    if not inside.index.isin(grid).all():
        raise ValueError('Timestamps do not align with the reporting boundary/interval.')
    return inside.reindex(grid)


def peak_mask(index, timezone, window, weekdays_only):
    local = index.tz_convert(timezone)
    def minute(t):
        h, m = map(int, t.split(':'))
        return h * 60 + m
    a, b = map(minute, window)
    t = local.hour * 60 + local.minute
    mask = ((t >= a) & (t < b)) if a < b else ((t >= a) | (t < b))
    if a == b:
        mask = np.ones(len(index), dtype=bool)
    if weekdays_only:
        mask = mask & (local.dayofweek < 5)
    return mask


def series_stats(series, step, timezone, peak, weekdays_only, energy=True):
    valid = series.dropna()
    result = {'expected_intervals': len(series), 'valid_intervals': len(valid),
              'coverage_pct': 100 * len(valid) / len(series) if len(series) else np.nan,
              'interval_minutes': step / pd.Timedelta(minutes=1),
              'std': valid.std(ddof=1),
              'change_volatility': series.diff().std(ddof=1)}
    if valid.empty:
        result.update(dict.fromkeys(['min', 'min_at', 'max', 'max_at', 'mean', 'peak_mean'], np.nan))
        return result
    p = series.loc[peak_mask(series.index, timezone, peak, weekdays_only)]
    result.update(min=valid.min(), min_at=valid.idxmin().tz_convert(timezone).isoformat(),
                  max=valid.max(), max_at=valid.idxmax().tz_convert(timezone).isoformat(),
                  mean=valid.mean(), peak_mean=p.mean(),
                  peak_coverage_pct=100 * p.notna().mean() if len(p) else np.nan,
                  p05=valid.quantile(.05), p95=valid.quantile(.95),
                  max_abs_ramp_per_hour=series.diff().abs().max() / (step / pd.Timedelta(hours=1)))
    if energy:
        result['observed_energy_mwh'] = valid.sum() * (step / pd.Timedelta(hours=1))
    else:
        result['negative_price_hours'] = (valid < 0).sum() * step / pd.Timedelta(hours=1)
        result['peak_min'] = p.min()
        result['peak_max'] = p.max()
    return result


def report_market(load, generation, prices=None, *, zone='ZONE', timezone='Europe/Berlin',
                  start=None, end=None, peak=('17:00', '21:00'), weekdays_only=False,
                  load_column='Actual Load', price_column=None, input_timezone=None,
                  load_minutes=None, generation_minutes=None, price_minutes=None,
                  absent_renewables_are_zero=True):
    """Return statistics, generation ranking, aligned residual-load data and metadata.

    Pass Series/DataFrames with delivery-start indexes. Absent renewable types
    default to zero; set absent_renewables_are_zero=False for strict handling.
    Existing NaNs are never replaced by zero. Totals cover observed intervals only.
    Explicit interval arguments override inference when gaps make cadence ambiguous.
    """
    load = utc_index(load, input_timezone)
    generation = utc_index(generation, input_timezone)
    ls = load if isinstance(load, pd.Series) else load[load_column]
    ls = pd.to_numeric(ls, errors='raise').rename('load_mw')
    gen = generation_columns(generation)
    dl, dg = cadence(ls.index, load_minutes), cadence(gen.index, generation_minutes)
    def boundary(value, fallback):
        if value is None:
            return fallback
        x = pd.Timestamp(value)
        return (x.tz_localize(timezone) if x.tzinfo is None else x).tz_convert('UTC')
    first = boundary(start, ls.index.min())
    last = boundary(end, ls.index.max() + dl)
    if last <= first:
        raise ValueError('end must be after start.')
    ls = regularise(ls, first, last, dl)
    gen = regularise(gen, first, last, dg)
    present = [c for c in RENEWABLES if c in gen.columns]
    absent = [c for c in RENEWABLES if c not in gen.columns]
    if present and (not absent or absent_renewables_are_zero):
        renewable = gen[present].sum(axis=1, min_count=len(present))
    elif not present and absent_renewables_are_zero:
        renewable = pd.Series(0., index=ls.index)
    else:
        renewable = pd.Series(np.nan, index=gen.index)
    # Compare interval averages at the coarser native resolution. Incomplete bins
    # remain missing; never interpolate actual generation or load.
    renewable_step = dl if not present and absent_renewables_are_zero else dg
    common = max(dl, renewable_step)
    def aggregate_complete(s, native):
        ratio = common / native
        if ratio != int(ratio):
            raise ValueError('Load/generation intervals must be integer multiples.')
        groups = s.resample(common, origin=first, closed='left', label='left')
        return groups.mean().where(groups.count() == int(ratio))
    aligned = pd.concat([aggregate_complete(ls, dl),
                         aggregate_complete(renewable, renewable_step).rename('wind_solar_mw')], axis=1)
    aligned['residual_load_mw'] = aligned['load_mw'] - aligned['wind_solar_mw']
    aligned.index.name = 'delivery_start_utc'
    stats = {'load_mw': series_stats(ls, dl, timezone, peak, weekdays_only),
             'residual_load_mw': series_stats(aligned.residual_load_mw, common, timezone, peak, weekdays_only)}
    total_generation = gen.sum(axis=1, min_count=len(gen.columns)).rename('total_generation_mw') if len(gen.columns) else pd.Series(np.nan, index=gen.index, name='total_generation_mw')
    amounts = gen.sum(min_count=1) * (dg / pd.Timedelta(hours=1))
    ranking = amounts.rename('observed_mwh').to_frame()
    ranking['coverage_pct'] = gen.notna().mean() * 100
    ranking['share_of_observed_generation_pct'] = 100 * amounts / amounts.sum(min_count=1) if amounts.sum(min_count=1) != 0 else np.nan
    ranking = ranking.sort_values('observed_mwh', ascending=False)
    ranking.index.name = 'generation_type'
    if prices is not None:
        prices = utc_index(prices, input_timezone)
        if isinstance(prices, pd.DataFrame):
            if price_column is None and len(prices.columns) != 1:
                raise ValueError('Set price_column when the price file has multiple columns.')
            prices = prices[price_column or prices.columns[0]]
        dp = cadence(prices.index, price_minutes)
        prices = regularise(pd.to_numeric(prices, errors='raise'), first, last, dp)
        stats['da_price_eur_mwh'] = series_stats(prices, dp, timezone, peak, weekdays_only, energy=False)
    period_days = (last.tz_convert(timezone).tz_localize(None) - first.tz_convert(timezone).tz_localize(None)) / pd.Timedelta(days=1)
    metadata = dict(zone=zone, start_utc=first.isoformat(), end_exclusive_utc=last.isoformat(),
                    period_days=period_days, elapsed_hours=(last - first) / pd.Timedelta(hours=1),
                    display_timezone=timezone, peak_window=f'{peak[0]}–{peak[1]} (end exclusive)',
                    peak_days='weekdays' if weekdays_only else 'all days',
                    renewable_types_present=present, renewable_types_absent=absent,
                    absent_types_assumed_zero=absent_renewables_are_zero)
    return {'statistics': pd.DataFrame(stats).T, 'generation_ranking': ranking,
            'residual_load': aligned, 'total_generation': total_generation, 'metadata': metadata}


def save_report(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for key in ['statistics', 'generation_ranking', 'residual_load', 'summary', 'total_generation']:
        result[key].to_csv(directory / f'{key}.csv')
    def table(frame):
        return '```text\n' + frame.to_string(float_format=lambda x: f'{x:,.2f}') + '\n```'
    text = '# Power market summary\n\n'
    text += '\n'.join(f'- {k}: {v}' for k, v in result['metadata'].items())
    text += '\n\n## Load, residual load and DA prices\n\n' + table(result['summary'])
    text += '\n\nStd dev measures dispersion of levels; change volatility is the sample standard deviation of adjacent interval changes, in the row units, without annualisation. Compare only matching observation intervals. Detailed metrics and coverage: see statistics.csv.'
    text += '\n\n## Top three generation types\n\n' + table(result['generation_ranking'].head(3))
    text += ('\n\nNotes: MW for load/residual-load levels; EUR/MWh for prices. Energy is MW × '
             'interval hours. Equal-duration intervals make the mean time-weighted. Missing '
             'intervals remain missing; energy totals are observed totals, not estimates of '
             'missing production. Rankings/shares may be biased when coverage differs. '
             'Extrema timestamps show the first occurrence. Ramps are changes between adjacent '
             'interval averages, expressed per hour. Residual load = load − wind − solar; '
             'it excludes other generation and does not directly measure imports or fossil demand. '
             'Pumped-storage consumption is excluded from generation rankings. No DA prices '
             'are reported unless a price dataset is supplied.\n')
    (directory / 'summary.md').write_text(text, encoding='utf-8')



# Integrated data loading: consistent keys for online and offline modes.

def zone_catalog():
    """Codes supported by the installed entsoe-py neighbour mapping, not an API-coverage guarantee."""
    from entsoe.mappings import NEIGHBOURS, lookup_area
    codes = sorted(set(NEIGHBOURS) | {c for values in NEIGHBOURS.values() for c in values})
    return {c: f'{c} — {lookup_area(c).meaning}' for c in codes}


def neighbour_codes(zone):
    from entsoe.mappings import NEIGHBOURS
    return sorted(NEIGHBOURS.get(zone, []))


def zone_label(zone):
    if not zone:
        return 'Neighbour not specified'
    names = {'DE_LU': 'Germany–Luxembourg (DE_LU)', 'DK_1': 'Western Denmark (DK_1)',
             'DK_2': 'Eastern Denmark (DK_2)', 'SE_3': 'Sweden SE3 (SE_3)',
             'SE_4': 'Sweden SE4 (SE_4)', 'NO_1': 'Norway NO1 (NO_1)',
             'NO_2': 'Norway NO2 (NO_2)'}
    if zone in names:
        return names[zone]
    try:
        from entsoe.mappings import lookup_area
        return f'{lookup_area(zone).meaning} ({zone})'
    except (ImportError, KeyError, ValueError):
        return str(zone)


def normalise_offline_flows(data, signed=False):
    """Single-direction files remain directional unless the user confirms signed net exports.

    Optional demo_crossborder_flows_reverse.csv stores neighbour -> origin, in MW.
    Missing reverse observations are NOT assumed zero.
    """
    if 'crossborder_flows' not in data:
        return data
    if signed:
        data['crossborder_flows'].attrs['flow_kind'] = 'net_export'
    elif 'crossborder_flows_reverse' in data:
        forward, reverse = data['crossborder_flows'], data['crossborder_flows_reverse']
        data['crossborder_exports'] = forward.copy()
        data['crossborder_imports'] = reverse.copy()
        data['crossborder_flows'] = forward - reverse
        data['crossborder_flows'].attrs['flow_kind'] = 'net_export'
    else:
        if (data['crossborder_flows'] < 0).any():
            raise ValueError('Negative values in directional flow file: confirm the CSV is signed net export using the control/CLI flag.')
        data['crossborder_flows'].attrs['flow_kind'] = 'directional_export'
    return data


def compact_summary(statistics):
    """Compact overview including level dispersion and interval-change volatility."""
    rows = []
    labels = {'load_mw': 'Load (MW)', 'residual_load_mw': 'Residual load (MW)',
              'da_price_eur_mwh': 'DA price (EUR/MWh)'}
    def extreme(row, key):
        value, at = row.get(key), row.get(key + '_at')
        if pd.isna(value):
            return 'Unavailable'
        date = pd.Timestamp(at).strftime('%d %b %Y %H:%M %z') if pd.notna(at) else ''
        return f'{float(value):,.2f} @ {date}'
    for name, row in statistics.iterrows():
        rows.append({'Metric': labels.get(name, name), 'Minimum (local time)': extreme(row, 'min'),
                     'Maximum (local time)': extreme(row, 'max'),
                     'Mean': row.get('mean', np.nan), 'Peak mean': row.get('peak_mean', np.nan),
                     'Std dev': row.get('std', np.nan),
                     'Change volatility': row.get('change_volatility', np.nan),
                     'Interval (min)': row.get('interval_minutes', np.nan)})
    result = pd.DataFrame(rows).set_index('Metric')
    for col in ['Mean', 'Peak mean', 'Std dev', 'Change volatility', 'Interval (min)']:
        result[col] = pd.to_numeric(result[col], errors='coerce')
    return result


def dataframe_html(frame):
    # No Jinja2 dependency. Underlying DataFrame remains numeric/full precision.
    return frame.to_html(float_format=lambda v: f'{v:,.2f}', na_rep='—', border=0)


def read_generation_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = csv.reader(f)
        next(rows)
        second = next(rows)
    multi = any(v in ('Actual Aggregated', 'Actual Consumption') for v in second[1:])
    return pd.read_csv(path, index_col=0, header=[0, 1] if multi else 0)


def read_single_csv(path, name):
    data = pd.read_csv(path, index_col=0)
    if data.shape[1] != 1:
        raise ValueError(f'{path}: expected exactly one value column.')
    return data.iloc[:, 0].rename(name)


def load_offline(data_dir='.', input_timezone=None, signed_flows=False):
    directory = Path(data_dir)
    data = {
        'load': read_single_csv(directory / 'demo_load.csv', 'Actual Load'),
        'generation': read_generation_csv(directory / 'demo_generation.csv'),
    }
    optional = {'DA_prices': ('demo_DA_prices.csv', 'DA_prices_EUR/MWh'),
                'net_position': ('demo_net_position.csv', 'Net_position_MW'),
                'crossborder_flows': ('demo_crossborder_flows.csv', 'Crossborder_flows_MW'),
                'crossborder_flows_reverse': ('demo_crossborder_flows_reverse.csv', 'Reverse_flow_MW')}
    for key, (filename, name) in optional.items():
        if (directory / filename).exists():
            data[key] = read_single_csv(directory / filename, name)
    data = {key: utc_index(value, input_timezone) for key, value in data.items()}
    return normalise_offline_flows(data, signed=signed_flows)


def get_api_token(api_file=None):
    token = os.environ.get('ENTSOE_API_KEY', '').strip()
    if token:
        return token
    # Compatible with your original notebook's file locations.
    paths = [Path(api_file)] if api_file else [Path('entsoe-api.txt'), Path.cwd().parent.parent / 'entsoe-api.txt']
    for path in paths:
        if path.is_file():
            token = path.read_text(encoding='utf-8').strip()
            if token and '<YOUR' not in token:
                return token
    raise ValueError('Set ENTSOE_API_KEY or provide an entsoe-api.txt file.')


def load_online(zone, destination, start, end, timezone, api_token=None):
    try:
        from entsoe import EntsoePandasClient
        from entsoe.mappings import lookup_area, NEIGHBOURS
    except ImportError as exc:
        raise ImportError('Online mode requires: pip install entsoe-py') from exc
    lookup_area(zone)
    if destination:
        lookup_area(destination)
        if destination not in NEIGHBOURS.get(zone, []):
            raise ValueError('Destination must be a neighbour listed by entsoe-py for the origin.')
    def stamp(value):
        t = pd.Timestamp(value)
        return t.tz_localize(timezone) if t.tzinfo is None else t
    first, last = stamp(start), stamp(end)
    if last <= first:
        raise ValueError('End must be after start (exclusive).')
    client = EntsoePandasClient(api_key=api_token or get_api_token())
    queries = {
        'load': (client.query_load, (zone,), {}),
        'generation': (client.query_generation, (zone,), {'psr_type': None}),
        'DA_prices': (client.query_day_ahead_prices, (zone,), {}),
        'net_position': (client.query_net_position, (zone,), {'dayahead': True}),
    }
    if destination:
        queries['crossborder_exports'] = (client.query_crossborder_flows, (zone, destination), {})
        queries['crossborder_imports'] = (client.query_crossborder_flows, (destination, zone), {})
    data, messages = {}, []
    for key, (func, args, kwargs) in queries.items():
        try:
            data[key] = utc_index(func(*args, start=first, end=last, **kwargs))
        except Exception as exc:
            # Do not echo request URLs, which could contain an API token.
            messages.append(f'{key}: unavailable ({type(exc).__name__})')
    if 'load' not in data or 'generation' not in data:
        raise ValueError('Required load/generation query failed. ' + '; '.join(messages))
    if 'crossborder_exports' in data and 'crossborder_imports' in data:
        data['crossborder_flows'] = data['crossborder_exports'] - data['crossborder_imports']
        data['crossborder_flows'].attrs['flow_kind'] = 'net_export'
    elif 'crossborder_exports' in data:
        data['crossborder_flows'] = data['crossborder_exports'].copy()
        data['crossborder_flows'].attrs['flow_kind'] = 'directional_export'
        messages.append('Reverse flow unavailable: showing directional exports, not net flow.')
    return data, messages



def hourly_statistics(series, timezone):
    """Descriptive statistics by local delivery-start hour, across all selected days.

    Prices/load must have regular native intervals before grouping. A missing
    interval remains NaN; it is excluded from the mean and counted in coverage.
    Both repeated daylight-saving hours belong to the same local-hour group.
    """
    if isinstance(series, pd.DataFrame):
        if series.shape[1] != 1:
            raise ValueError('Hourly profile requires one value column.')
        series = series.iloc[:, 0]
    local = series.tz_convert(timezone)
    grouped = local.groupby(local.index.hour)
    profile = grouped.agg(mean='mean', minimum='min', maximum='max', observations='count', expected_intervals='size')
    profile['median'] = grouped.median()
    profile['q25'] = grouped.quantile(.25)
    profile['q75'] = grouped.quantile(.75)
    profile = profile.reindex(range(24))
    for col in ['observations', 'expected_intervals']:
        profile[col] = profile[col].fillna(0).astype(int)
    profile['coverage_pct'] = 100 * profile['observations'] / profile['expected_intervals'].replace(0, np.nan)
    profile.index.name = 'local_hour'
    return profile


def hourly_boxplot(series, title, unit, timezone, period_days=None):
    """One box per local hour, using raw interval observations across selected days."""
    import plotly.graph_objects as go
    if isinstance(series, pd.DataFrame):
        if series.shape[1] != 1:
            raise ValueError('Hourly boxplot requires one value column.')
        series = series.iloc[:, 0]
    local = series.tz_convert(timezone)
    labels = [f'{h}h' for h in range(24)]
    fig = go.Figure()
    for hour, label in enumerate(labels):
        values = local.loc[local.index.hour == hour].dropna()
        if values.empty:
            continue
        fig.add_trace(go.Box(
            y=values.to_numpy(), name=label, boxpoints='outliers', boxmean=True,
            quartilemethod='linear', marker_color='#367ab3', line_color='#367ab3',
            hovertext=[t.isoformat() for t in values.index],
            hoverinfo='y+name+text', showlegend=False))
    fig.update_layout(
        title=title + (f' — {period_days:g}-day period' if period_days is not None else '') + '<br><sup>Box: 25th–75th percentile; line: median; dashed line: mean; points: outliers</sup>',
        xaxis_title=f'Delivery start hour — {timezone}', yaxis_title=unit,
        template='plotly_white', margin=dict(t=95),
        xaxis=dict(type='category', categoryorder='array', categoryarray=labels,
                   tickmode='array', tickvals=labels, range=[-.5, 23.5]))
    fig.add_hline(y=0, line_color='gray', line_width=1)
    return fig


def create_figures(data, result, timezone):
    """Interactive Plotly figures; imported only when requested."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    first = pd.Timestamp(result['metadata']['start_utc'])
    last = pd.Timestamp(result['metadata']['end_exclusive_utc'])
    gen = generation_columns(utc_index(data['generation']))
    step = cadence(gen.index)
    gen = regularise(gen, first, last, step)
    local = gen.tz_convert(timezone)
    # Local calendar-day energy; missing observations remain missing, not zero.
    daily = local.mul(step / pd.Timedelta(hours=1)).resample('D').sum(min_count=1)
    fig_gen = go.Figure()
    for col in daily:
        fig_gen.add_bar(x=daily.index, y=daily[col], name=str(col))
    fig_gen.update_layout(title='Daily observed generation by type', barmode='stack',
                          yaxis_title='MWh', template='plotly_white', hovermode='x unified')
    # Shared timeline, independent MW and EUR/MWh axes. No forward-fill or
    # artificial price/power rescaling; each trace retains its own resolution.
    fig_load = make_subplots(specs=[[{'secondary_y': True}]])
    labels = {'load_mw': 'Load', 'wind_solar_mw': 'Wind + solar',
              'residual_load_mw': 'Residual load'}
    colors = {'load_mw': '#2468b4', 'wind_solar_mw': '#2b9a66',
              'residual_load_mw': '#e88c24'}
    power_values = []
    for col in ['load_mw', 'wind_solar_mw', 'residual_load_mw']:
        series = result['residual_load'][col]
        power_values.extend(series.dropna().tolist())
        fig_load.add_trace(go.Scatter(
            x=series.index.tz_convert(timezone), y=series, name=labels[col],
            mode='lines', connectgaps=False, line=dict(color=colors[col]),
            hovertemplate='%{y:,.2f} MW<extra>%{fullData.name}</extra>'), secondary_y=False)
    total = result['total_generation']
    power_values.extend(total.dropna().tolist())
    fig_load.add_trace(go.Scatter(
        x=total.index.tz_convert(timezone), y=total, name='Total generation',
        mode='lines', connectgaps=False, line=dict(color='#7356a5', width=2),
        hovertemplate='%{y:,.2f} MW<extra>%{fullData.name}</extra>'), secondary_y=False)
    def padded_range(values):
        if not len(values):
            return None
        low, high = min(0., float(min(values))), max(0., float(max(values)))
        padding = .05 * (high - low) if high > low else 1.
        return [low - padding, high + padding]
    fig_load.update_yaxes(title_text='Power (MW)', secondary_y=False,
                          range=padded_range(power_values), zeroline=True)
    if 'DA_prices' in data:
        combined_price = data['DA_prices']
        if isinstance(combined_price, pd.DataFrame):
            if combined_price.shape[1] != 1:
                raise ValueError('Expected a single DA price column.')
            combined_price = combined_price.iloc[:, 0]
        combined_price = regularise(combined_price, first, last, cadence(combined_price.index))
        fig_load.add_trace(go.Scatter(
            x=combined_price.index.tz_convert(timezone), y=combined_price,
            name='DA price (right axis)', mode='lines', connectgaps=False,
            line=dict(color='#c22b70', dash='dash', width=2),
            hovertemplate='%{y:,.2f} EUR/MWh<extra>%{fullData.name}</extra>'), secondary_y=True)
        fig_load.update_yaxes(title_text='DA price (EUR/MWh)', secondary_y=True,
                              range=padded_range(combined_price.dropna().tolist()),
                              showgrid=False, zeroline=False)
    else:
        fig_load.update_yaxes(visible=False, secondary_y=True)
    fig_load.update_layout(
        title='Load, generation, residual load and DA price' if 'DA_prices' in data else 'Load, generation and residual load',
        template='plotly_white', hovermode='x unified',
        xaxis_title=f'Delivery start — {timezone}',
        legend=dict(orientation='h', yanchor='bottom', y=1.02), margin=dict(t=110, r=95))
    figs = {'generation': fig_gen, 'load': fig_load}
    ranked = result['generation_ranking'].dropna(subset=['observed_mwh']).sort_values('observed_mwh')
    amounts = go.Figure(go.Bar(
        x=ranked['observed_mwh'], y=ranked.index.astype(str), orientation='h',
        customdata=ranked[['share_of_observed_generation_pct', 'coverage_pct']].to_numpy(),
        marker_color='#367ab3',
        hovertemplate=('%{y}<br>%{x:,.2f} MWh<br>Share %{customdata[0]:.2f}%'
                       '<br>Data coverage %{customdata[1]:.1f}%<extra></extra>')))
    amounts.update_layout(title='Total observed generation by type', xaxis_title='Energy (MWh)',
                          yaxis_title='Generation type', template='plotly_white',
                          height=max(400, 28 * len(ranked)), margin=dict(l=190))
    figs['generation_by_type'] = amounts
    load_series = data['load']
    if isinstance(load_series, pd.DataFrame):
        load_series = load_series['Actual Load'] if 'Actual Load' in load_series else load_series.iloc[:, 0]
    load_step = cadence(load_series.index)
    load_series = regularise(load_series, first, last, load_step)
    profile_load = hourly_statistics(load_series, timezone)
    result.setdefault('hourly_profiles', {})['load'] = profile_load
    figs['load_by_hour'] = hourly_boxplot(load_series, 'Load distribution by hour of day', 'MW', timezone, result['metadata']['period_days'])

    if 'DA_prices' in data:
        prices = data['DA_prices']
        if isinstance(prices, pd.DataFrame):
            if prices.shape[1] != 1:
                raise ValueError('Expected a single DA price column.')
            prices = prices.iloc[:, 0]
        prices = prices.loc[(prices.index >= first) & (prices.index < last)]
        peak = result['metadata']['peak_window'].split(' ')[0].split('–')
        mask = peak_mask(prices.index, timezone, peak, result['metadata']['peak_days'] == 'weekdays')
        fig = go.Figure()
        fig.add_scatter(x=prices.index.tz_convert(timezone), y=prices, name='DA price', mode='lines', connectgaps=False)
        fig.add_scatter(x=prices.index[mask].tz_convert(timezone), y=prices.loc[mask],
                        name='Peak-window prices', mode='markers', marker={'size': 4})
        fig.update_layout(title='Day-ahead prices and selected peak window',
                          yaxis_title='EUR/MWh', template='plotly_white', hovermode='x unified')
        figs['prices'] = fig
        price_step = cadence(data['DA_prices'].index)
        price_series = regularise(prices, first, last, price_step)
        profile_price = hourly_statistics(price_series, timezone)
        result.setdefault('hourly_profiles', {})['prices'] = profile_price
        figs['prices_by_hour'] = hourly_boxplot(price_series, 'Day-ahead price distribution by hour of day', 'EUR/MWh', timezone, result['metadata']['period_days'])
    origin = zone_label(result['metadata']['zone'])
    destination = result['metadata'].get('destination', '')
    neighbour = zone_label(destination)
    for key in ['net_position', 'crossborder_flows']:
        if key not in data:
            continue
        values = data[key]
        kind = values.attrs.get('flow_kind', 'directional_export')
        if isinstance(values, pd.DataFrame):
            if values.shape[1] != 1:
                continue
            values = values.iloc[:, 0]
        values = values.loc[(values.index >= first) & (values.index < last)]
        if key == 'net_position':
            title = f'DA net position — {origin}'
            direction = 'Positive = net export to all coupled zones; negative = net import'
            name, axis = 'Net export (+) / net import (−)', 'MW (+ export / − import)'
        elif kind == 'net_export':
            title = f'Net physical flow — {origin} ↔ {neighbour}'
            direction = (f'Positive = export {result["metadata"]["zone"]} → {destination or "selected neighbour"}; '
                         f'negative = import from {destination or "selected neighbour"}')
            name = f'Net export to {destination or "neighbour"}'
            axis = 'MW (+ export / − import)'
        else:
            title = f'Physical exports — {origin} → {neighbour}'
            direction = 'Positive = export to selected neighbour; reverse direction not included'
            name, axis = f'Exports to {destination or "neighbour"}', 'MW exported'
        fig = go.Figure(go.Scatter(x=values.index.tz_convert(timezone), y=values,
                                  mode='lines', name=name, connectgaps=False,
                                  hovertemplate='%{x}<br>%{y:,.2f} MW<extra>%{fullData.name}</extra>'))
        fig.add_hline(y=0, line_color='gray', line_dash='dash')
        fig.update_layout(title=dict(text=title + '<br><sup>' + direction + '</sup>'),
                          yaxis_title=axis, template='plotly_white', hovermode='x unified',
                          margin=dict(t=100))
        if key == 'crossborder_flows' and values.notna().any():
            low, high = float(values.min()), float(values.max())
            fig.add_hline(y=high, line_dash='dot', line_color='#a55a22',
                          annotation_text=f'Observed max: {high:,.2f} MW', annotation_position='top left')
            if low != high:
                fig.add_hline(y=low, line_dash='dot', line_color='#557a99',
                              annotation_text=f'Observed min: {low:,.2f} MW', annotation_position='bottom left')
        figs[key] = fig
    return figs



def exchange_statistics(data, metadata, timezone):
    """Observed net-position time shares and bilateral physical-flow extrema.

    Missing intervals are excluded from sign-share denominators; coverage is
    reported separately. Native equal-duration bins make shares time-weighted.
    Flow extrema are observations, not transmission capacity limits.
    """
    first, last = pd.Timestamp(metadata['start_utc']), pd.Timestamp(metadata['end_exclusive_utc'])
    origin = metadata['zone']
    neighbour = metadata.get('destination') or 'unspecified neighbour'
    net_rows, flow_rows = [], []
    for key in ['net_position', 'crossborder_flows']:
        if key not in data:
            continue
        series = data[key]
        kind = series.attrs.get('flow_kind', 'directional_export')
        if isinstance(series, pd.DataFrame):
            if series.shape[1] != 1:
                raise ValueError(f'{key} needs exactly one value column.')
            series = series.iloc[:, 0]
        series = utc_index(series)
        step = cadence(series.index)
        series = regularise(pd.to_numeric(series, errors='raise'), first, last, step)
        valid = series.dropna()
        coverage = 100 * len(valid) / len(series) if len(series) else np.nan
        if key == 'net_position':
            if valid.empty:
                pos = neg = zero = np.nan
                pattern = 'Unavailable'
                balance = 'Unavailable'
                net_mwh = np.nan
            else:
                pos, neg, zero = [100 * mask.mean() for mask in [valid > 0, valid < 0, valid == 0]]
                if zero > max(pos, neg):
                    pattern = 'Zero position most often'
                elif pos > neg:
                    pattern = 'Exporter more often'
                elif neg > pos:
                    pattern = 'Importer more often'
                else:
                    pattern = 'Equal export/import time'
                net_mwh = valid.sum() * (step / pd.Timedelta(hours=1))
                balance = 'Net exporter' if net_mwh > 0 else 'Net importer' if net_mwh < 0 else 'Balanced'
            net_rows.append({'Zone': origin, 'Export time (%)': pos, 'Import time (%)': neg,
                             'Zero time (%)': zero, 'Observed pattern': pattern,
                             'Net exported energy (MWh)': net_mwh, 'Energy balance': balance,
                             'Coverage (%)': coverage})
        else:
            flow_rows.append({'Connection': f'{origin} → {neighbour}',
                              'Series': 'Net physical export' if kind == 'net_export' else 'Directional exports only',
                              'Observed min (MW)': valid.min(),
                              'Min at (local)': valid.idxmin().tz_convert(timezone).isoformat() if len(valid) else 'Unavailable',
                              'Observed max (MW)': valid.max(),
                              'Max at (local)': valid.idxmax().tz_convert(timezone).isoformat() if len(valid) else 'Unavailable',
                              'Mean (MW)': valid.mean(), 'Coverage (%)': coverage})
    return {'net_position_summary': pd.DataFrame(net_rows).set_index('Zone') if net_rows else pd.DataFrame(),
            'flow_summary': pd.DataFrame(flow_rows).set_index('Connection') if flow_rows else pd.DataFrame()}

def run_analysis(data, *, zone, destination='', timezone='Europe/Berlin', start=None, end=None,
                 peak=('17:00', '21:00'), weekdays_only=False,
                 absent_renewables_are_zero=True, make_plots=True):
    result = report_market(data['load'], data['generation'], data.get('DA_prices'),
                           zone=zone, timezone=timezone, start=start, end=end, peak=peak,
                           weekdays_only=weekdays_only,
                           absent_renewables_are_zero=absent_renewables_are_zero)
    result['metadata']['destination'] = destination
    result['summary'] = compact_summary(result['statistics'])
    result.update(exchange_statistics(data, result['metadata'], timezone))
    result['figures'] = create_figures(data, result, timezone) if make_plots else {}
    return result


_base_save_report = save_report

def save_report(result, directory):
    _base_save_report(result, directory)
    path = Path(directory)
    extra_text = ''
    for key, title in [('net_position_summary', 'Net-position direction'), ('flow_summary', 'Observed bilateral flows')]:
        table = result.get(key, pd.DataFrame())
        if not table.empty:
            table.to_csv(path / f'{key}.csv')
            extra_text += '\n\n## ' + title + '\n\n```text\n' + table.to_string(float_format=lambda v: f'{v:,.2f}') + '\n```'
    if extra_text:
        extra_text += ('\n\nDirection shares use observed time only; missing intervals are excluded and coverage is shown. '
                       'Export frequency and net exported energy describe different aspects of the zone. '
                       'Flow extrema are observed values, not transmission capacity limits. '
                       'Capacity utilisation needs direction-specific available capacity for the same intervals.')
        with (path / 'summary.md').open('a', encoding='utf-8') as output:
            output.write(extra_text)
    for name, profile in result.get('hourly_profiles', {}).items():
        profile.to_csv(path / f'{name}_by_hour.csv')
    for name, fig in result.get('figures', {}).items():
        fig.write_html(str(path / f'{name}.html'), include_plotlyjs=True, full_html=True)
    # Retain available auxiliary flow series in the notebook's data_results;
    # they are not used as DA forecasting inputs or proof of causality.


def main():
    parser = argparse.ArgumentParser(description='Integrated ENTSO-E market summary: offline CSV or online API.')
    parser.add_argument('--mode', choices=['offline', 'online'], default='offline')
    parser.add_argument('--data-dir', default='.')
    parser.add_argument('--zone', default='DE_LU', help='Reporting label offline; ENTSO-E bidding-zone code online.')
    parser.add_argument('--destination', default='')
    parser.add_argument('--list-zones', action='store_true')
    parser.add_argument('--list-neighbours', action='store_true')
    parser.add_argument('--interactive', action='store_true', help='Show zone choices and prompt for origin and neighbour.')
    parser.add_argument('--signed-flows', action='store_true', help='Confirm offline flow CSV is signed net export (positive = export).')
    parser.add_argument('--timezone', default='Europe/Berlin')
    parser.add_argument('--input-timezone', help='Required for offline timestamps without offsets.')
    parser.add_argument('--start', help='Local start date; omit offline to infer from load.')
    parser.add_argument('--end', help='Local end date, exclusive; omit offline to infer from load.')
    parser.add_argument('--peak-start', default='17:00')
    parser.add_argument('--peak-end', default='21:00')
    parser.add_argument('--weekdays-only', action='store_true')
    parser.add_argument('--absent-renewables-are-zero', action=argparse.BooleanOptionalAction, default=True,
                        help='Treat absent renewable types as zero (default). Use --no-absent-renewables-are-zero for strict handling.')
    parser.add_argument('--no-plots', action='store_true')
    parser.add_argument('--output', default='market_report')
    args = parser.parse_args()
    if args.list_zones or args.interactive:
        print('Supported zone codes (coverage depends on date/dataset):')
        for label in zone_catalog().values():
            print('  ' + label)
        if args.list_zones and not args.interactive:
            return
    if args.interactive:
        args.zone = input(f'Origin zone [{args.zone}]: ').strip().upper() or args.zone
    if args.list_neighbours or args.interactive:
        choices = neighbour_codes(args.zone)
        print(f'Neighbours of {args.zone}: ' + ', '.join(choices))
        if args.list_neighbours and not args.interactive:
            return
        args.destination = input('Neighbour (blank = skip): ').strip().upper()
        if args.destination and args.destination not in choices:
            parser.error('Choose a neighbour from the displayed list.')
    if args.mode == 'online':
        if not args.start or not args.end:
            parser.error('Online mode requires --start and --end.')
        data, messages = load_online(args.zone, args.destination, args.start, args.end, args.timezone)
        for message in messages:
            print(message)
    else:
        data = load_offline(args.data_dir, args.input_timezone, signed_flows=args.signed_flows)
    result = run_analysis(data, zone=args.zone, destination=args.destination, timezone=args.timezone, start=args.start, end=args.end,
                          peak=(args.peak_start, args.peak_end), weekdays_only=args.weekdays_only,
                          absent_renewables_are_zero=args.absent_renewables_are_zero,
                          make_plots=not args.no_plots)
    save_report(result, args.output)
    print(result['summary'].to_string(float_format=lambda v: f'{v:,.2f}'))
    for key in ['net_position_summary', 'flow_summary']:
        if not result[key].empty:
            print('\n' + key + ':\n' + result[key].to_string(float_format=lambda v: f'{v:,.2f}'))
    print('\nTop three generation types:\n' + result['generation_ranking'].head(3).to_string())
    if 'DA_prices' not in data:
        print('\nDA prices unavailable: price statistics/plot omitted.')
    print(f'\nReport saved to {args.output}')


if __name__ == '__main__':
    main()
