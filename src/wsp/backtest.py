"""Backtest engine.

One place decides how a target weight becomes a realised return, so the cost,
financing and lookahead assumptions are stated once and tested once.

Accounting for a target equity weight ``w`` on day ``t``::

    r_portfolio = w * r_equity            # exposure to the index
               + (1 - w) * r_cash         # unused capital earns the bill rate
               - max(w - 1, 0) * spread   # leverage is borrowed above the bill rate
               - |w - w_prev| * cost      # spread + commission on what we traded

``w`` is *lagged* before it is used, so a weight derived from day ``t`` data can
only earn day ``t+1`` returns.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from . import metrics
from .data import MarketData

TRADING_DAYS = 252


@dataclasses.dataclass(frozen=True)
class CostModel:
    """Trading frictions, all charged against the strategy.

    ``cost_per_turnover`` is one-way: moving the weight from 0.4 to 0.9 trades
    0.5 units of notional and pays ``0.5 * cost_per_turnover``.
    """

    cost_per_turnover: float = 0.0010  # 10bp - conservative for index futures/ETFs
    borrow_spread: float = 0.005  # annual premium over the bill rate on leverage
    max_leverage: float = 3.0
    execution_lag: int = 1  # trading days between signal and fill

    def __post_init__(self) -> None:
        if self.execution_lag < 1:
            raise ValueError("execution_lag must be >= 1; 0 would be lookahead")
        if self.cost_per_turnover < 0 or self.borrow_spread < 0:
            raise ValueError("costs cannot be negative")


@dataclasses.dataclass(frozen=True)
class BacktestResult:
    returns: pd.Series  # daily net portfolio returns
    weights: pd.Series  # weights actually held (post-lag)
    turnover: pd.Series  # |change in weight| per day
    benchmark: pd.Series  # buy-and-hold returns over the same dates
    rf: pd.Series  # daily cash returns over the same dates

    @property
    def equity(self) -> pd.Series:
        return metrics.equity_curve(self.returns)

    @property
    def benchmark_equity(self) -> pd.Series:
        return metrics.equity_curve(self.benchmark)

    @property
    def annual_turnover(self) -> float:
        years = len(self.turnover) / TRADING_DAYS
        return float(self.turnover.sum() / years) if years > 0 else np.nan

    def summary(self, relative: bool = True) -> dict[str, float]:
        """Metric block. ``relative=False`` drops alpha/beta/excess columns,
        which are noise when the 'strategy' being summarised is the benchmark.
        """
        return metrics.summarise(
            self.returns,
            rf=self.rf,
            benchmark=self.benchmark,
            turnover=self.annual_turnover,
            relative=relative,
        )

    def annual_returns(self) -> pd.DataFrame:
        """Calendar-year returns for the strategy and the benchmark."""
        grow = lambda s: (1 + s).prod() - 1  # noqa: E731
        return pd.DataFrame(
            {
                "strategy": self.returns.groupby(self.returns.index.year).apply(grow),
                "benchmark": self.benchmark.groupby(
                    self.benchmark.index.year
                ).apply(grow),
            }
        ).assign(excess=lambda d: d["strategy"] - d["benchmark"])


def run(
    data: MarketData,
    target_weight: pd.Series,
    costs: CostModel | None = None,
) -> BacktestResult:
    """Simulate holding ``target_weight`` of the equity index, financed with cash."""
    costs = costs or CostModel()

    equity_returns = data.returns
    cash_returns = data.rf_returns

    held = (
        target_weight.reindex(data.index)
        .shift(costs.execution_lag)
        .fillna(0.0)
        .clip(lower=0.0, upper=costs.max_leverage)
    )

    turnover = held.diff().abs().fillna(held.abs())
    daily_spread = costs.borrow_spread / TRADING_DAYS

    portfolio = (
        held * equity_returns
        + (1.0 - held) * cash_returns
        - np.maximum(held - 1.0, 0.0) * daily_spread
        - turnover * costs.cost_per_turnover
    )

    return BacktestResult(
        returns=portfolio,
        weights=held,
        turnover=turnover,
        benchmark=equity_returns,
        rf=cash_returns,
    )


def buy_and_hold(data: MarketData, costs: CostModel | None = None) -> BacktestResult:
    """The benchmark: 100% invested, every day."""
    return run(data, pd.Series(1.0, index=data.index), costs)
