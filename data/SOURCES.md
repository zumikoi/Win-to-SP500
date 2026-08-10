# Data sources

## `us_market_daily.csv.gz`

Daily US large-cap equity series with a matching risk-free series,
**1885-03-20 to 2025-12-19** (35,254 rows).

| Column | Meaning |
|---|---|
| `date` | trading date |
| `close` | price-only index (no dividends) |
| `total_return` | **total-return index** - dividends reinvested, net of a 0.0945% expense ratio |
| `rf_rate` | annualised risk-free rate, percent |
| `rf_index` | risk-free total-return index (what cash actually earns) |
| `cpi` | Consumer Price Index, all urban consumers |

Derived from [`SteelCerberus/us-market-data`](https://github.com/SteelCerberus/us-market-data),
which assembles the series from Robert Shiller's dataset, FRED (`CPIAUCNS`,
Treasury-bill rates) and Yahoo Finance SPY adjusted closes for recent dates.
`scripts/fetch_data.py` regenerates this file; the copy here is committed so the
backtests are reproducible offline and pinned to the numbers in the report.

## Caveats that affect interpretation

These are the reasons the reports default to **1928 onwards** (`wsp.data.SP500_START`):

- **Pre-1928 is not the S&P 500.** Data from 1885-03-20 to 1927-12-30 is a Dow
  composite portfolio. Use it as an extra robustness sample, not as the headline.
- **Dividends before 1928 are annual**, not quarterly, so the total-return series
  is coarser in that era.
- **The risk-free series changes definition.** Before July 1926 it is crudely
  approximated as the 10-year yield minus 1%; from 1926 to 1953 it is 1-month
  bills; 3-month bills thereafter. This matters for Sharpe ratios in early
  samples, not for the return comparison.
- **Saturday trading before 1952** is dropped from the file, though its returns
  are folded into the adjacent sessions.
- **CPI is monthly, interpolated to daily**, so the most recent month's
  inflation-adjusted columns are provisional. Nothing in this repo uses them.
- **The index is not directly investable.** Real-world tracking of the S&P 500
  costs an ETF expense ratio; the total-return column already nets 0.0945%, which
  is charged to the benchmark and the strategy alike.

## Cross-check

The bundled total-return series reproduces well-known figures, which is the
reason to trust it:

| Window | CAGR from this file | Widely quoted |
|---|---|---|
| 1993-01 - 2025-12 (SPY era) | 10.67% | ~10.5% |
| 1950-01 - 2025-12 | 11.59% | ~11.5% |
| 2000-01 - 2025-12 | 8.03% | ~8% |

`tests/test_no_lookahead.py::test_bundled_dataset_is_sane` pins the long-run
number so a bad refresh fails loudly.
