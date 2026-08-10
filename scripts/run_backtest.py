#!/usr/bin/env python3
"""Run the full evaluation and write results/ (tables, charts, REPORT.md).

    python scripts/run_backtest.py                # 1928-, walk-forward + statics
    python scripts/run_backtest.py --start 1885   # include the Dow-composite era
    python scripts/run_backtest.py --cost-bps 20  # stress the friction assumption
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import textwrap

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from wsp import data as wsp_data  # noqa: E402
from wsp import metrics, report  # noqa: E402
from wsp.backtest import CostModel, buy_and_hold, run  # noqa: E402
from wsp.strategy import TrendVolStrategy, default_grid  # noqa: E402
from wsp.walkforward import parameter_stability, walk_forward  # noqa: E402

RESULTS = pathlib.Path(__file__).resolve().parents[1] / "results"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default=wsp_data.SP500_START, help="first date (default: 1928, when the S&P 500 begins)")
    p.add_argument("--end", default=None, help="last date")
    p.add_argument("--cost-bps", type=float, default=10.0, help="one-way trading cost per unit turnover, bps")
    p.add_argument("--borrow-bps", type=float, default=50.0, help="annual borrowing premium over the bill rate, bps")
    p.add_argument("--max-leverage", type=float, default=3.0)
    p.add_argument("--min-train-years", type=int, default=20)
    p.add_argument("--outdir", type=pathlib.Path, default=RESULTS)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    market = wsp_data.load(start=args.start, end=args.end)
    costs = CostModel(
        cost_per_turnover=args.cost_bps / 10_000,
        borrow_spread=args.borrow_bps / 10_000,
        max_leverage=args.max_leverage,
    )
    print(f"sample: {market.index[0].date()} -> {market.index[-1].date()} ({len(market):,} days)")
    print(f"costs: {args.cost_bps:.0f}bp/turnover, {args.borrow_bps:.0f}bp borrow, max {args.max_leverage:g}x\n")

    # --- Reference points, all in-sample by construction -------------------
    bench = buy_and_hold(market, costs)
    # A huge target_vol makes the volatility scalar saturate at max_exposure,
    # leaving the trend filter alone: in or out, never scaled.
    trend_only = run(
        market,
        TrendVolStrategy(target_vol=99.0, max_exposure=1.0, band=0.0).weights(market),
        costs,
    )
    balanced = TrendVolStrategy(trend_family="mid", target_vol=0.17, max_exposure=2.0, vol_window=60, band=0.15)
    balanced_result = run(market, balanced.weights(market), costs)
    matched = balanced.with_leverage(1.3)
    matched_result = run(market, matched.weights(market), costs)

    # --- The honest number: parameters never see their own test data -------
    print(f"walk-forward over {len(default_grid())} configurations ...")
    common = dict(costs=costs, min_train_years=args.min_train_years, max_leverage=args.max_leverage)
    wf = walk_forward(market, vol_match=True, **common)
    wf_unlevered = walk_forward(market, vol_match=False, **common)

    oos_start = wf.returns.index[0]
    bench_oos = buy_and_hold(market.slice(str(oos_start.date()), None), costs)

    summary = report.summary_frame(
        {
            "S&P 500 buy & hold": bench.summary(relative=False),
            "Trend only (no vol target)": trend_only.summary(),
            "Trend x vol target (in-sample params)": balanced_result.summary(),
            "... levered to benchmark risk": matched_result.summary(),
            "WALK-FORWARD out-of-sample (unlevered)": wf_unlevered.summary(),
            "WALK-FORWARD out-of-sample (risk-matched)": wf.summary(),
            "S&P 500 over the same OOS window": bench_oos.summary(relative=False),
        }
    )

    print("\n=== headline ===")
    print(summary[["CAGR %", "Vol %", "Sharpe", "MaxDD %", "Calmar", "Turnover x/yr"]].round(2).to_string())

    decades = report.decade_table(wf.backtest)
    eras = report.period_table(wf.backtest, report.ERAS)
    stability = parameter_stability(wf)
    costs_table = report.cost_sensitivity(wf.backtest, costs.cost_per_turnover)
    breakeven = report.breakeven_cost_bps(wf.backtest, costs.cost_per_turnover)
    print(f"\nbreak-even trading cost: {breakeven:.0f} bps per unit turnover")

    starts = tuple(y for y in (1948, 1963, 1975, 1990, 2000, 2010) if y >= oos_start.year)
    robustness = report.start_date_sensitivity(wf.backtest, starts)
    robustness_unlevered = report.start_date_sensitivity(wf_unlevered.backtest, starts)
    print("\n=== does the edge survive a later start date? ===")
    print(robustness[["strategy CAGR %", "S&P 500 CAGR %", "excess %"]].round(2).to_string())

    summary.round(4).to_csv(args.outdir / "summary.csv")
    decades.round(3).to_csv(args.outdir / "oos_by_decade.csv")
    wf.selections.to_csv(args.outdir / "walkforward_selections.csv")
    wf.backtest.annual_returns().round(5).to_csv(args.outdir / "oos_annual_returns.csv")

    report.plot_overview(
        wf.backtest,
        args.outdir / "walkforward_overview.png",
        f"Walk-forward out-of-sample, {oos_start.year}-{wf.returns.index[-1].year}",
    )
    report.plot_overview(
        wf_unlevered.backtest,
        args.outdir / "walkforward_unlevered.png",
        f"Walk-forward out-of-sample, no leverage, {oos_start.year}-{wf.returns.index[-1].year}",
    )
    report.plot_annual_excess(wf.backtest, args.outdir / "annual_excess.png")
    report.plot_overview(
        matched_result,
        args.outdir / "insample_overview.png",
        f"Fixed parameters, full sample {market.index[0].year}-{market.index[-1].year}",
    )

    costs_table.round(3).to_csv(args.outdir / "cost_sensitivity.csv")
    robustness.round(3).to_csv(args.outdir / "start_date_sensitivity.csv")
    _write_report(
        args, market, wf, summary, decades, eras, stability, bench_oos,
        costs_table, breakeven, robustness, robustness_unlevered,
    )
    print(f"\nwrote {args.outdir}/REPORT.md and 3 charts")
    return 0


def _robustness_verdict(robustness) -> str:
    """State plainly whether the headline holds up from later starting points."""
    excess = robustness["excess %"]
    later = excess.iloc[1:]
    if len(later) == 0:
        return "Not enough out-of-sample history to vary the start date."
    if (later > 0).all():
        return (
            "**The edge holds from every starting point tested.** Excess return "
            "shrinks as the start moves forward but never turns negative."
        )
    if (later <= 0).all():
        return (
            f"**The headline does not survive.** The full-sample excess of "
            f"{excess.iloc[0]:+.2f} pts/yr comes entirely from the earliest window: "
            f"from {robustness.index[1].split('-')[0]} onward the strategy *underperforms* "
            f"the index on raw return at every starting point tested "
            f"({later.min():+.2f} to {later.max():+.2f} pts/yr). Read the CAGR "
            "comparison as a statement about one historical stretch, not a "
            "repeatable edge. What does survive is the risk side - see the Sharpe "
            "and drawdown columns, and the unlevered table below."
        )
    wins = int((later > 0).sum())
    return (
        f"**Mixed.** The strategy leads from {wins} of the {len(later)} later start "
        f"dates tested and trails from the rest ({later.min():+.2f} to "
        f"{later.max():+.2f} pts/yr), so the size of the edge depends heavily on "
        "when you begin."
    )


def _significance_note(tstat: float) -> str:
    if abs(tstat) >= 2.0:
        return "That clears the conventional 2.0 bar for significance."
    return (
        "That is **below** the conventional 2.0 bar, so the return difference "
        "alone is not statistically distinguishable from luck over this sample. "
        "The risk-side results - drawdown and Calmar - are the more robust claim."
    )


def _write_report(
    args, market, wf, summary, decades, eras, stability, bench_oos,
    costs_table, breakeven, robustness, robustness_unlevered,
) -> None:
    stats, bench_stats = wf.summary(), bench_oos.summary()
    oos = wf.returns
    edge = stats["CAGR %"] - bench_stats["CAGR %"]

    text = f"""\
# Results

Sample **{market.index[0].date()} - {market.index[-1].date()}**, daily total returns
(dividends reinvested). Costs: {args.cost_bps:.0f}bp per unit of turnover,
{args.borrow_bps:.0f}bp annual borrowing premium over the Treasury-bill rate,
one-day execution lag, leverage capped at {args.max_leverage:g}x.

## Headline

Out of sample, from **{oos.index[0].date()} to {oos.index[-1].date()}**, with every
parameter chosen before the returns it earned:

> Read this table together with the start-date section below it. The full-sample
> CAGR comparison is the least robust number on this page; the risk columns are
> the most robust.

| | Strategy | S&P 500 | Difference |
|---|---|---|---|
| Annual return (CAGR) | **{stats['CAGR %']:.2f}%** | {bench_stats['CAGR %']:.2f}% | **{edge:+.2f} pts** |
| Volatility | {stats['Vol %']:.2f}% | {bench_stats['Vol %']:.2f}% | {stats['Vol %'] - bench_stats['Vol %']:+.2f} pts |
| Sharpe ratio | **{stats['Sharpe']:.3f}** | {bench_stats['Sharpe']:.3f} | {stats['Sharpe'] - bench_stats['Sharpe']:+.3f} |
| Worst drawdown | {stats['MaxDD %']:.1f}% | {bench_stats['MaxDD %']:.1f}% | {stats['MaxDD %'] - bench_stats['MaxDD %']:+.1f} pts |
| Calmar | {stats['Calmar']:.3f} | {bench_stats['Calmar']:.3f} | {stats['Calmar'] - bench_stats['Calmar']:+.3f} |

Annualised alpha **{stats['Alpha %']:.2f}%** at beta **{stats['Beta']:.2f}**.
The deflated-Sharpe probability that the true Sharpe is positive, after correcting
for having searched {wf.n_trials} configurations, is **{stats['DeflatedSharpe p']:.3f}**.

Two numbers argue against reading too much into the headline, and both belong here:

- The Newey-West t-statistic on the daily return difference is **{stats['t-stat']:.2f}**.
  {_significance_note(stats['t-stat'])}
- The strategy was ahead in only **{stats['Rolling10yWin %']:.0f}%** of rolling
  ten-year windows. An edge that shows up in the full-sample CAGR but in fewer
  than half of ten-year windows is a *concentrated* edge, not a steady one - it
  is earned in a handful of turbulent stretches and given back slowly in calm
  ones. An investor who started at the wrong time would have waited a long while.

![Walk-forward overview](walkforward_overview.png)

## Does the edge survive a later start date?

{_robustness_verdict(robustness)}

Risk-matched (levered) walk-forward:

{report.format_table(robustness)}

Unlevered walk-forward, same out-of-sample years:

{report.format_table(robustness_unlevered)}

## All variants

{report.format_table(summary)}

## Out-of-sample by decade

The average hides the pattern that matters: this approach wins in choppy and
falling markets and gives ground in sustained melt-ups.

{report.format_table(decades)}

![Annual excess](annual_excess.png)

## By regime

{report.format_table(eras)}

## How much friction the edge can absorb

The strategy trades {wf.backtest.annual_turnover:.1f}x its capital per year, so
costs are the assumption most likely to overturn the result. The edge survives
up to **{breakeven:.0f} bps per unit of turnover**, against roughly 1-3 bps for
S&P 500 futures or a liquid ETF today.

{report.format_table(costs_table, 3)}

## Which parameters the walk-forward chose

Selection is stable - it keeps landing in the same neighbourhood rather than
chasing a different configuration every year, which is what fitting noise looks
like.

{report.format_table(stability)}

## Reproduce

```bash
pip install -r requirements.txt
python scripts/run_backtest.py --start {args.start}
```
"""
    (args.outdir / "REPORT.md").write_text(textwrap.dedent(text))


if __name__ == "__main__":
    raise SystemExit(main())
