# Energy Market Summary

A Python project for exploring electricity-market fundamentals across European bidding zones, with a focus on the Nordic markets and Germany–Luxembourg.

The project combines load, generation, residual load, day-ahead prices and cross-border information into a concise report. It includes a command-line script and an interactive Jupyter notebook with zone selectors, configurable reporting periods and Plotly charts.

Developed by **Boonsita Kittiwirayanon** as part of a transition from energy engineering and numerical modelling towards energy-market analysis.

## What the project does

- Retrieves available data from the ENTSO-E Transparency Platform, or loads local CSV files.
- Lists supported zone codes and filters neighbouring-zone choices by the selected origin.
- Ranks generation types by observed energy production in MWh.
- Calculates residual load from load, wind and solar generation.
- Summarises load, residual load and day-ahead prices, including standard deviation and interval-change volatility.
- Shows hourly load and price boxplots labelled `0h`–`23h`, with the selected reporting span in days.
- Combines load, total generation, wind + solar, residual load and DA prices in a plot with separate power and price axes.
- Reports export/import/zero time shares and observed net exported energy.
- Summarises minimum and maximum bilateral physical flows with local timestamps and explicit direction labels.
- Exports tables, a Markdown summary and interactive HTML charts.

This is a retrospective analysis tool. It does not currently forecast prices, generate trading signals or execute trades.

## Files

| File | Purpose |
|---|---|
| `energy-market-summary.ipynb` | Self-contained interactive notebook with embedded reporting functions |
| `energy_market_summary.py` | Command-line script and reusable Python functions |
| `requirements.txt` | Python dependencies |
| `demo_load.csv` | Supplied sample load data |
| `demo_generation.csv` | Supplied sample generation data |
| `README.md` | Setup, usage and methodology |

The notebook and script contain the same reporting implementation. If you modify the implementation, keep both versions consistent.

## Installation

Use Python 3.10 or later. From the project directory:

```bash
python -m pip install -r requirements.txt
```

The dependencies are pandas, NumPy, Plotly, ipywidgets, entsoe-py and JupyterLab. The summary display does not require Jinja2.

## Quick start: interactive notebook

1. Keep the notebook and sample CSVs in the same directory.
2. Launch JupyterLab:

   ```bash
   jupyter lab
   ```

3. Open `energy-market-summary.ipynb` and select **Run All**.
4. Select **offline** mode to try the supplied files without an API key.
5. Choose the origin zone and, if analysing a flow file, its matching neighbour.
6. Leave the start and end dates blank to use the period covered by the load file.
7. Click **Run report** to see the main statistics, available exchange summaries and charts.
8. Expand **Detailed statistics, coverage and configuration** for additional metrics.
9. Click **Export report** to save the tables and charts.

After replacing the notebook with an updated version, restart the kernel and select **Run All** before generating another report.

In offline mode, selecting another zone changes the report label; it does **not** retrieve different data. Supply CSVs for the selected zone. The origin and neighbour must match the provenance and direction of any offline flow files.

### Notebook controls

| Control | Meaning |
|---|---|
| Mode | Read local CSVs or retrieve online data |
| Zone | Origin bidding zone; choices are shown before selection |
| Neighbour | Connected zone for the physical-flow query; optional |
| Timezone | Local reporting timezone; updated when the origin changes |
| CSV folder | Directory containing the offline input files |
| Naive TZ | Timezone of input timestamps that have no offset |
| Start / End | Local reporting boundaries; end is exclusive |
| Peak start / Peak end | Peak-summary window; default 17:00–21:00 |
| Peak weekdays only | Restrict peak-summary statistics to Monday–Friday |
| Treat absent renewable types as 0 MW (default) | Enabled by default: absent types count as 0 MW; uncheck for strict handling |
| Offline flow CSV is signed net export | Confirm that positive values are exports and negative values are imports |
| Output folder | Destination for exported reports |

The zone list comes from the installed entsoe-py mapping. A listed code does not guarantee that every dataset is available for the requested date; some codes relate to historical market configurations.

## Online data and API credentials

Online mode uses an ENTSO-E Transparency Platform API token. The project checks:

1. The `ENTSOE_API_KEY` environment variable.
2. An `entsoe-api.txt` file in the working directory.
3. The original notebook's fallback location, two directories above the working directory.

For a local setup, create `entsoe-api.txt` containing only your token. Add that filename to your repository's `.gitignore`; do not upload credentials or notebook outputs containing credentials to GitHub.

Online queries cover actual load, actual generation, day-ahead prices, day-ahead net position and, when a neighbour is selected, both physical-flow directions. Load and generation are required. Other datasets may be unavailable and are omitted with a message.

Data source: [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/). Python client: [entsoe-py](https://github.com/EnergieID/entsoe-py).

## Command-line examples

Run the supplied offline sample:

```bash
python energy_market_summary.py --mode offline --data-dir . --zone DE_LU --output market_report
```

Retrieve a full local delivery day for DK2 and its connection to SE4:

```bash
python energy_market_summary.py --mode online --zone DK_2 --destination SE_4 --timezone Europe/Copenhagen --start 2026-09-14 --end 2026-09-15 --output dk2_report
```

Show supported zones or neighbours:

```bash
python energy_market_summary.py --list-zones
python energy_market_summary.py --zone DE_LU --list-neighbours
```

Show choices and prompt for origin and neighbour:

```bash
python energy_market_summary.py --mode offline --interactive --data-dir . --output market_report
```

Change the peak window and restrict peak statistics to weekdays:

```bash
python energy_market_summary.py --mode offline --data-dir . --zone DE_LU --peak-start 08:00 --peak-end 20:00 --weekdays-only --output market_report
```

For an offline flow file that already contains signed net exports:

```bash
python energy_market_summary.py --mode offline --data-dir . --zone DK_2 --destination SE_4 --signed-flows --output dk2_report
```

To require all three renewable categories instead of treating absent categories as zero, add `--no-absent-renewables-are-zero`.

Use `--no-plots` to export tables without loading Plotly. Run `python energy_market_summary.py --help` for all command-line options.

## Offline input format

The first CSV column must contain delivery-start timestamps. Include timezone offsets where possible, for example `2026-10-10 00:15:00+02:00`.

| Filename | Required? | Values |
|---|---|---|
| `demo_load.csv` | Yes | One load column, MW |
| `demo_generation.csv` | Yes | Generation by type, MW |
| `demo_DA_prices.csv` | No | One day-ahead price column, EUR/MWh |
| `demo_net_position.csv` | No | One net-position column, MW; positive = export |
| `demo_crossborder_flows.csv` | No | Origin-to-neighbour flow, MW, or explicitly confirmed signed net export |
| `demo_crossborder_flows_reverse.csv` | No | Neighbour-to-origin flow, MW |

Generation files can have flat column headers or the two-level headers produced by ENTSO-E generation data. With two-level headers, the project selects `Actual Aggregated` and excludes `Actual Consumption`, including pumped-storage consumption.

Only sample load and generation files are included. Prices and flows are not fabricated. Verify the samples' bidding-zone provenance before using them in a published market analysis.

## Methodology

### Generation ranking

Energy is calculated from interval-average power:

```text
Generation energy (MWh) = generation (MW) × interval duration (hours)
```

For example, 100 MW during a 15-minute interval corresponds to 25 MWh. The report ranks all available generation types and displays the top three, their shares of observed generation and their data coverage.

Totals reflect observed intervals only. Unequal coverage across generation types can affect the ranking and shares.

### Residual load

```text
Residual load = load − Wind Offshore − Wind Onshore − Solar
```

All terms are in MW and refer to the same zone and aligned delivery intervals. Other generation, storage and net imports can meet residual load; it is not a direct measure of fossil-generation demand.

Absent renewable generation types count as **0 MW by default**. If offshore wind is absent, residual load uses onshore wind and solar; if all three types are absent, residual load equals load at its native resolution. Missing observations inside an existing type remain missing. For strict handling, uncheck the notebook option or pass `--no-absent-renewables-are-zero`. This default assumes absent columns represent generation types not present in the zone; verify source coverage if a column may instead be missing because of a data issue. Negative residual load means reported wind and solar generation exceeded reported load for that interval.

### Summary statistics

The concise table shows minimum and maximum with local timestamps, overall mean, peak-window mean, standard deviation, interval-change volatility and observation interval. Values display two decimals while calculations retain full precision.

| Main-table column | Interpretation |
|---|---|
| Minimum / Maximum (local time) | Observed extreme and the first local timestamp at which it occurs |
| Mean | Mean across valid observations in the selected period |
| Peak mean | Mean inside the configured local peak window |
| Std dev | Sample standard deviation of observed levels |
| Change volatility | Sample standard deviation of consecutive interval changes |
| Interval (min) | Observation interval used for that row |

Load and residual-load levels, standard deviations and change volatility are in MW. The corresponding price measures are in EUR/MWh. Missing values display as unavailable rather than zero.

Detailed statistics include data coverage, observed energy where applicable, standard deviation, 5th/95th percentiles, largest absolute interval-to-interval ramp expressed per hour, and negative-price hours. The first timestamp is reported when an extreme occurs more than once.

**Standard deviation** measures dispersion of the observed load or price levels around their mean. **Change volatility** is the standard deviation of consecutive interval changes: `series.diff().std(ddof=1)`. Both are sample estimates and use the row units (MW or EUR/MWh). Change volatility is not a percentage return, an annualised measure or a forecast. Missing intervals break change pairs, and insufficient observations produce an unavailable estimate. The table includes the interval in minutes because volatility at different frequencies is not directly comparable.

Percentiles describe historical observations in the selected period. They are not forecast confidence intervals. Price ramps are EUR/MWh per hour; load and residual-load ramps are MW per hour.

### Time alignment and missing data

- Internal timestamp indexes use UTC; dates, chart labels and hourly grouping use the selected local timezone.
- The start is inclusive and the end is exclusive.
- Timezone names such as `Europe/Berlin` handle daylight-saving changes.
- The native interval is inferred from the most common timestamp spacing. Sparse data may require explicit interval arguments when calling `report_market` directly.
- Missing intervals remain missing and reduce reported coverage.
- Load and generation are aggregated to the coarser resolution when their intervals are compatible integer multiples. Incomplete aggregation bins remain missing.
- Actual data is not interpolated to invent finer-resolution observations.

### Quarter-hour and hourly values

For four complete quarter-hours, hourly average MW = `(MW_00 + MW_15 + MW_30 + MW_45) / 4`. Hourly energy MWh = the sum of each quarter-hour MW multiplied by 0.25 hours. The script averages power only when coarser alignment is required; four equal-duration observations are needed for a complete hourly bin. The hourly boxplots instead group raw interval observations by local clock hour across the selected days.

The combined power/price chart uses separate axes with independent ranges: MW on the left and EUR/MWh on the right. Both ranges include zero, all observed values and padding; negative observations are retained. Native price timestamps are preserved. Curve intersections do not imply equivalent magnitudes because the units and scales differ.

Total generation sums available actual production categories, excludes pumped-storage consumption, and remains missing if any included category has a missing observation. It does not include net imports and is not guaranteed to equal reported load.

### Net position and physical flows

| Quantity | Positive | Negative |
|---|---|---|
| Day-ahead net position | Net export to coupled markets | Net import from coupled markets |
| Bilateral net physical flow | Net export from origin to selected neighbour | Net import from selected neighbour |

Online bilateral net flow is calculated as:

```text
Net physical export = origin → neighbour flow − neighbour → origin flow
```

Both zone names appear on the plot. Missing reverse-direction data is not assumed to be zero. If only the forward direction is available, the chart is labelled directional exports rather than net flow.

For offline data, provide both directional files to calculate net flow. Alternatively, select the signed-flow checkbox or use `--signed-flows` only if `demo_crossborder_flows.csv` already contains signed net exports.

The net-position summary reports the percentage of observed time with positive (export), negative (import) and zero positions. Missing intervals are excluded from the denominator, and coverage is shown. Because intervals have equal native durations, these are time shares. Net exported MWh is also reported: a zone can export more often but import more energy overall if its import intervals are larger.

For example, a zone that exports in 60% of observed intervals and imports in 40% exports more often. It can still be a net energy importer if its import volumes are much larger. The summary therefore reports both **Observed pattern** and **Energy balance**. These labels describe the selected period and available data, not a permanent characteristic of the zone.

The flow summary and dotted chart guides show observed minimum and maximum MW, with local timestamps, plus mean and coverage. These are **not transmission capacity limits**. Observed flow may be below available capacity; congestion may depend on wider network constraints. Assess utilisation only with matching direction-specific capacity data for the relevant timestamps.

Day-ahead net positions are commercial auction outcomes; physical flows are realised network quantities. They need not match, and neither identifies the marginal price-setting plant by itself.

## Visualisations

| Chart | Interpretation |
|---|---|
| Generation by type | Total observed MWh, ranked by type |
| Daily generation mix | Stacked daily observed energy by type |
| Load by hour | Distribution of load for each local delivery-start hour |
| Price by hour | Distribution of day-ahead prices for each local delivery-start hour |
| Combined power/price time series | Load, total generation, wind + solar and residual load on the left MW axis; DA price on the right EUR/MWh axis |
| Day-ahead price time series | Prices with selected peak-window intervals marked |
| Net position and bilateral flows | Export/import direction relative to the selected origin |

The hourly price and load charts are **boxplots of raw interval observations** across the selected days, with labels `0h`–`23h`. Chart titles state the reporting span in days. The span uses local calendar-time boundaries; elapsed hours are reported separately to account for daylight-saving changes. End dates remain exclusive. The box spans the 25th–75th percentiles; the solid internal line is the median and the dashed line is the mean. Whiskers extend to the most extreme observed values within 1.5 interquartile ranges of the box, with more extreme observations shown as outlier points. Hourly CSV tables include mean, median, quartiles, minimum, maximum, sample count and coverage. Generation-by-type charts remain bars showing total MWh.

Hourly profiles use all selected days, including weekends, even when peak-summary statistics are restricted to weekdays. On a daylight-saving clock change, both occurrences of a repeated local hour enter the same hourly group. Missing hours are left blank.

## Exported files

| Output | Contents |
|---|---|
| `summary.md` | Report settings, main statistics, top-three generation ranking and available exchange summaries |
| `summary.csv` | Concise load/residual-load/price table |
| `statistics.csv` | Detailed statistics and coverage |
| `generation_ranking.csv` | Full generation ranking |
| `residual_load.csv` | Aligned load, wind-plus-solar and residual load |
| `net_position_summary.csv` | Export/import/zero time shares, observed net exported energy and coverage, when available |
| `flow_summary.csv` | Observed minimum/maximum flow, timestamps, mean and coverage, when available |
| `total_generation.csv` | Sum of available actual production categories at the generation-data interval |
| `load_by_hour.csv` | Hourly load-profile statistics |
| `prices_by_hour.csv` | Hourly price-profile statistics, when prices are supplied |
| `*.html` | Standalone interactive charts |

Hourly-profile CSVs and HTML files are produced when plotting is enabled. Price and flow outputs depend on the corresponding datasets being available. Exported HTML includes Plotly for viewing without an internet connection.

## Validation and limitations

The reporting functions and notebook callbacks were checked against the supplied 1,152-interval load and generation samples. Additional checks covered hourly observation groups and boxplot traces, MW-to-MWh conversion, mixed-frequency alignment, absent renewable categories, missing values, daylight-saving period lengths, separate price/power axes, direction-share denominators, frequency versus energy balance, flow extrema, zone selectors, two-decimal formatting and exports. Synthetic prices and mocked API responses were used for relevant tests; they are not included as observed market data.

Live API retrieval has not been verified with the author's token in this development environment. A separate Jupyter-kernel execution was blocked by the environment; notebook code cells and widget callbacks were exercised directly instead.

The project does not currently provide previous-period comparisons, automatic outage analysis, causal attribution of price spikes, forecasting or a trading backtest. High prices, generation start-ups or price alignment across zones can motivate investigation, but do not independently establish the cause of an event.

## Author and AI assistance

**Boonsita Kittiwirayanon** developed the project idea, original notebook and energy-market analysis questions, and guided the subsequent revisions.

**ChatGPT by OpenAI** assisted with revising and extending the code, developing the ipywidgets controls, integrating reporting functions, creating Plotly visualisations, checking calculations and drafting this README. The project was developed collaboratively through iterative discussion and feedback.

## Data acknowledgement

Market data is accessed through the ENTSO-E Transparency Platform using the open-source entsoe-py client. Check the applicable data terms and attribution requirements before redistributing downloaded datasets. This project is an independent learning project and is not affiliated with or endorsed by ENTSO-E or OpenAI.
