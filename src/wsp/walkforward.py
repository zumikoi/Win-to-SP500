"""Walk-forward evaluation.

A backtest that picks its parameters using the whole sample tells you what
*would have* worked. This module never does that: at the start of each calendar
year it re-selects a configuration using only history available on that date,
trades it for twelve months, then repeats. Every return in the output series was
produced by parameters chosen before it happened.

The second job here is **risk matching**. Selecting on Sharpe ratio reliably
picks a low-volatility configuration, which wins on risk-adjusted return but can
still lose a raw CAGR race against a fully invested benchmark. Since leverage is
a dial and Sharpe is the thing that is actually persistent, the strategy is
scaled - again using past data only - so that its volatility matches the
benchmark's. That makes the comparison apples-to-apples: same risk, and the
higher Sharpe shows up as higher return.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from . import metrics, signals
from .backtest import BacktestResult, CostModel, run
from .data import MarketData
from .strategy import TrendVolStrategy, default_grid

TRADING_DAYS = 252


@dataclasses.dataclass(frozen=True)
class WalkForwardResult:
    backtest: BacktestResult
    selections: pd.DataFrame
    n_trials: int
    #: In-sample Sharpe of every configuration at the final re-selection. Its
    #: spread is what sets the bar the winner must clear to be believable.
    trial_sharpes: pd.Series

    @property
    def returns(self) -> pd.Series:
        return self.backtest.returns

    @property
    def benchmark(self) -> pd.Series:
        return self.backtest.benchmark

    def summary(self) -> dict[str, float]:
        stats = self.backtest.summary()
        excess = self.returns - self.benchmark
        stats["DeflatedSharpe p"] = metrics.deflated_sharpe(
            observed_sharpe=metrics.sharpe(self.returns, self.backtest.rf),
            n_trials=self.n_trials,
            n_obs=len(self.returns),
            skew=float(pd.Series(self.returns).skew()),
            kurtosis=float(pd.Series(self.returns).kurtosis() + 3.0),
            trial_sharpe_std=float(self.trial_sharpes.std(ddof=1)),
        )
        stats["Rolling10yWin %"] = _rolling_win_rate(self.returns, self.benchmark)
        stats["t-stat"] = metrics.newey_west_tstat(excess)
        return stats


def _rolling_win_rate(returns: pd.Series, benchmark: pd.Series, years: int = 10) -> float:
    roll = metrics.rolling_outperformance(returns, benchmark, years).dropna()
    return float((roll > 0).mean() * 100) if len(roll) else np.nan


def walk_forward(
    data: MarketData,
    grid: list[TrendVolStrategy] | None = None,
    costs: CostModel | None = None,
    min_train_years: int = 20,
    vol_match: bool = True,
    match_window_days: int = 20 * TRADING_DAYS,
    max_leverage: float = 3.0,
    top_k: int = 5,
    final_band: float = 0.20,
) -> WalkForwardResult:
    """Re-select parameters every January using only prior data.

    Args:
        min_train_years: history required before the first out-of-sample year.
        vol_match: scale the selected strategy to the benchmark's trailing
            volatility, so it is compared at equal risk rather than equal capital.
        match_window_days: lookback for that volatility comparison. Long by
            design: the target is a slow-moving *long-run* risk ratio, and a
            five-year estimate of it is mostly noise, which shows up directly
            as leverage - and therefore turnover - lurching from year to year.
        max_leverage: hard cap on the resulting weight.
        top_k: average the weights of the ``k`` best in-sample configurations
            rather than betting on the single argmax. The best config in a
            training window is not reliably the best in the next year - the
            difference between ranks 1 and 5 is mostly noise - and averaging
            them both hedges that and stops the position lurching every January
            when the winner changes.
        final_band: no-trade band applied to the stitched weight series, so
            re-selection at a year boundary only trades when it actually moves
            the position.
    """
    grid = grid or default_grid()
    costs = costs or CostModel(max_leverage=max_leverage)

    # Precompute once: weights are causal, so slicing them later is safe.
    # The unbanded exposure is what gets blended and levered; the band is
    # applied once at the end, to the series actually traded.
    raw_weights = {s.name: s.raw_weights(data) for s in grid}
    caps = {s.name: s.max_exposure for s in grid}
    weights = {s.name: s.weights(data) for s in grid}
    net_returns = {s.name: run(data, weights[s.name], costs).returns for s in grid}
    by_name = {s.name: s for s in grid}

    benchmark_returns = data.returns
    first_year = int(data.index[0].year) + min_train_years
    last_year = int(data.index[-1].year)

    stitched = pd.Series(0.0, index=data.index)
    rows: list[dict[str, object]] = []
    last_scored: dict[str, float] = {}

    for year in range(first_year, last_year + 1):
        # Strictly everything before January of the test year - this slice is
        # the entire no-lookahead guarantee.
        train = slice(None, f"{year - 1}-12-31")
        test_dates = data.index[
            (data.index >= f"{year}-01-01") & (data.index <= f"{year}-12-31")
        ]
        if len(test_dates) == 0:
            continue

        scored = {
            name: metrics.sharpe(series.loc[train], data.rf_returns.loc[train])
            for name, series in net_returns.items()
        }
        scored = {k: v for k, v in scored.items() if np.isfinite(v)}
        if not scored:
            continue
        last_scored = scored
        ranked = sorted(scored, key=scored.__getitem__, reverse=True)[: max(top_k, 1)]

        blended = sum(
            raw_weights[name].loc[test_dates].clip(upper=caps[name]).fillna(0.0)
            for name in ranked
        ) / len(ranked)
        blended_train = sum(net_returns[name].loc[train] for name in ranked) / len(ranked)

        leverage = 1.0
        if vol_match:
            strat_hist = blended_train.tail(match_window_days)
            bench_hist = benchmark_returns.loc[train].tail(match_window_days)
            strat_sd = float(strat_hist.std(ddof=1))
            if strat_sd > 0 and len(strat_hist) >= TRADING_DAYS:
                leverage = float(
                    np.clip(float(bench_hist.std(ddof=1)) / strat_sd, 0.5, max_leverage)
                )

        stitched.loc[test_dates] = (blended * leverage).clip(upper=max_leverage)

        best = by_name[ranked[0]]
        rows.append(
            {
                "year": year,
                "trend_family": best.trend_family,
                "target_vol": best.target_vol,
                "max_exposure": best.max_exposure,
                "vol_window": best.vol_window,
                "band": best.band,
                "leverage": round(leverage, 3),
                "train_sharpe": round(scored[ranked[0]], 3),
            }
        )

    if not rows:
        raise ValueError(
            f"no out-of-sample years available: need more than {min_train_years} "
            "years of history"
        )

    oos_start = f"{rows[0]['year']}-01-01"
    oos_data = data.slice(oos_start, None)
    traded = signals.apply_no_trade_band(stitched.loc[oos_start:], final_band)
    result = run(oos_data, traded, costs)

    return WalkForwardResult(
        backtest=result,
        selections=pd.DataFrame(rows).set_index("year"),
        n_trials=len(grid),
        trial_sharpes=pd.Series(last_scored, name="train_sharpe"),
    )


def parameter_stability(result: WalkForwardResult) -> pd.DataFrame:
    """How often each parameter value was chosen.

    A strategy whose selected parameters jump around every year is fitting
    noise; one that keeps landing on the same neighbourhood is not.
    """
    frame = result.selections
    rows = []
    for column in ("trend_family", "target_vol", "max_exposure", "vol_window", "band"):
        counts = frame[column].value_counts(normalize=True)
        rows.append(
            {
                "parameter": column,
                "most_common": counts.index[0],
                "share %": round(float(counts.iloc[0]) * 100, 1),
                "distinct_values": int(frame[column].nunique()),
            }
        )
    return pd.DataFrame(rows).set_index("parameter")
