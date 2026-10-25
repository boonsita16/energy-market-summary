# Power market summary

- zone: DK_2
- start_utc: 2026-09-30T22:00:00+00:00
- end_exclusive_utc: 2026-10-14T22:00:00+00:00
- period_days: 14.0
- elapsed_hours: 336.0
- display_timezone: Europe/Copenhagen
- peak_window: 17:00–21:00 (end exclusive)
- peak_days: weekdays
- renewable_types_present: ['Wind Offshore', 'Wind Onshore', 'Solar']
- renewable_types_absent: []
- absent_types_assumed_zero: True
- destination: SE_4

## Load, residual load and DA prices

```text
                                  Minimum (local time)                Maximum (local time)     Mean  Peak mean  Std dev  Change volatility  Interval (min)
Metric                                                                                                                                                    
Load (MW)           1,169.57 @ 04 Oct 2026 05:00 +0200  2,301.59 @ 05 Oct 2026 14:00 +0200 1,718.19   1,891.27   273.78              89.31           60.00
Residual load (MW)     22.72 @ 09 Oct 2026 05:00 +0200  1,833.87 @ 06 Oct 2026 18:00 +0200   895.22   1,124.57   466.06             117.96           60.00
DA price (EUR/MWh)      2.27 @ 10 Oct 2026 11:45 +0200    405.87 @ 06 Oct 2026 18:45 +0200   154.75     221.57    66.48              12.43           15.00
```

Std dev measures dispersion of levels; change volatility is the sample standard deviation of adjacent interval changes, in the row units, without annualisation. Compare only matching observation intervals. Detailed metrics and coverage: see statistics.csv.

## Top three generation types

```text
                 observed_mwh  coverage_pct  share_of_observed_generation_pct
generation_type                                                              
Wind Offshore      157,257.28        100.00                             34.38
Biomass            141,676.35        100.00                             30.97
Wind Onshore        59,840.36        100.00                             13.08
```

Notes: MW for load/residual-load levels; EUR/MWh for prices. Energy is MW × interval hours. Equal-duration intervals make the mean time-weighted. Missing intervals remain missing; energy totals are observed totals, not estimates of missing production. Rankings/shares may be biased when coverage differs. Extrema timestamps show the first occurrence. Ramps are changes between adjacent interval averages, expressed per hour. Residual load = load − wind − solar; it excludes other generation and does not directly measure imports or fossil demand. Pumped-storage consumption is excluded from generation rankings. No DA prices are reported unless a price dataset is supplied.


## Net-position direction

```text
      Export time (%)  Import time (%)  Zero time (%)     Observed pattern  Net exported energy (MWh) Energy balance  Coverage (%)
Zone                                                                                                                              
DK_2            16.67            83.26           0.07  Importer more often                -145,263.50   Net importer        100.00
```

## Observed bilateral flows

```text
                          Series  Observed min (MW)             Min at (local)  Observed max (MW)             Max at (local)  Mean (MW)  Coverage (%)
Connection                                                                                                                                           
DK_2 → SE_4  Net physical export            -507.52  2026-10-06T09:30:00+02:00             678.18  2026-10-10T14:00:00+02:00    -272.67        100.00
```

Direction shares use observed time only; missing intervals are excluded and coverage is shown. Export frequency and net exported energy describe different aspects of the zone. Flow extrema are observed values, not transmission capacity limits. Capacity utilisation needs direction-specific available capacity for the same intervals.
