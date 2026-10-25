"""Power market statistics from timestamp-indexed pandas data or CSVs.

Requires: pip install pandas numpy
CLI example:
python power_market_report.py --load demo_load.csv --generation demo_generation.csv \
    --zone DE_LU --timezone Europe/Berlin --output report
Optional: --prices prices.csv --price-column price --start 2026-10-10 --end 2026-10-22
End is exclusive; naive CLI boundaries are interpreted in --timezone.
Input timestamp strings without offsets require --input-timezone.
All power inputs must be interval-average MW, prices EUR/MWh. No API keys needed.
"""
from pathlib import Path
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
              'coverage_pct': 100 * len(valid) / len(series) if len(series) else np.nan}
    if valid.empty:
        result.update(dict.fromkeys(['min', 'min_at', 'max', 'max_at', 'mean', 'peak_mean'], np.nan))
        return result
    p = series.loc[peak_mask(series.index, timezone, peak, weekdays_only)]
    result.update(min=valid.min(), min_at=valid.idxmin().tz_convert(timezone).isoformat(),
                  max=valid.max(), max_at=valid.idxmax().tz_convert(timezone).isoformat(),
                  mean=valid.mean(), peak_mean=p.mean(),
                  peak_coverage_pct=100 * p.notna().mean() if len(p) else np.nan,
                  p05=valid.quantile(.05), p95=valid.quantile(.95),
                  std=valid.std(),
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
                  absent_renewables_are_zero=False):
    """Return statistics, generation ranking, aligned residual-load data and metadata.

    Pass Series/DataFrames with delivery-start indexes. Absent renewable types are
    unknown unless absent_renewables_are_zero=True is an informed assumption.
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
        renewable = pd.Series(0., index=gen.index)
    else:
        renewable = pd.Series(np.nan, index=gen.index)
    # Compare interval averages at the coarser native resolution. Incomplete bins
    # remain missing; never interpolate actual generation or load.
    common = max(dl, dg)
    def aggregate_complete(s, native):
        ratio = common / native
        if ratio != int(ratio):
            raise ValueError('Load/generation intervals must be integer multiples.')
        groups = s.resample(common, origin=first, closed='left', label='left')
        return groups.mean().where(groups.count() == int(ratio))
    aligned = pd.concat([aggregate_complete(ls, dl),
                         aggregate_complete(renewable, dg).rename('wind_solar_mw')], axis=1)
    aligned['residual_load_mw'] = aligned['load_mw'] - aligned['wind_solar_mw']
    aligned.index.name = 'delivery_start_utc'
    stats = {'load_mw': series_stats(ls, dl, timezone, peak, weekdays_only),
             'residual_load_mw': series_stats(aligned.residual_load_mw, common, timezone, peak, weekdays_only)}
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
    metadata = dict(zone=zone, start_utc=first.isoformat(), end_exclusive_utc=last.isoformat(),
                    display_timezone=timezone, peak_window=f'{peak[0]}–{peak[1]} (end exclusive)',
                    peak_days='weekdays' if weekdays_only else 'all days',
                    renewable_types_present=present, renewable_types_absent=absent,
                    absent_types_assumed_zero=absent_renewables_are_zero)
    return {'statistics': pd.DataFrame(stats).T, 'generation_ranking': ranking,
            'residual_load': aligned, 'metadata': metadata}


def save_report(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for key in ['statistics', 'generation_ranking', 'residual_load']:
        result[key].to_csv(directory / f'{key}.csv')
    def table(frame):
        return '```text\n' + frame.to_string(float_format=lambda x: f'{x:,.2f}') + '\n```'
    text = '# Power market summary\n\n'
    text += '\n'.join(f'- {k}: {v}' for k, v in result['metadata'].items())
    text += '\n\n## Load, residual load and DA prices\n\n' + table(result['statistics'])
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


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--load', required=True)
    p.add_argument('--generation', required=True)
    p.add_argument('--prices')
    p.add_argument('--zone', required=True)
    p.add_argument('--timezone', default='Europe/Berlin')
    p.add_argument('--input-timezone')
    p.add_argument('--start')
    p.add_argument('--end')
    p.add_argument('--peak-start', default='17:00')
    p.add_argument('--peak-end', default='21:00')
    p.add_argument('--weekdays-only', action='store_true')
    p.add_argument('--load-column', default='Actual Load')
    p.add_argument('--price-column')
    p.add_argument('--flat-generation-header', action='store_true')
    p.add_argument('--absent-renewables-are-zero', action='store_true')
    for name in ['load', 'generation', 'price']:
        p.add_argument(f'--{name}-minutes', type=float)
    p.add_argument('--output', default='market_report')
    a = p.parse_args()
    result = report_market(
        pd.read_csv(a.load, index_col=0),
        pd.read_csv(a.generation, index_col=0, header=0 if a.flat_generation_header else [0, 1]),
        pd.read_csv(a.prices, index_col=0) if a.prices else None,
        zone=a.zone, timezone=a.timezone, start=a.start, end=a.end,
        peak=(a.peak_start, a.peak_end), weekdays_only=a.weekdays_only,
        load_column=a.load_column, price_column=a.price_column,
        input_timezone=a.input_timezone, load_minutes=a.load_minutes,
        generation_minutes=a.generation_minutes, price_minutes=a.price_minutes,
        absent_renewables_are_zero=a.absent_renewables_are_zero)
    save_report(result, a.output)
    print(result['statistics'].to_string())
    print('\nTop three generation types:\n', result['generation_ranking'].head(3).to_string())
    print(f'\nReport written to {a.output}')


if __name__ == '__main__':
    main()
