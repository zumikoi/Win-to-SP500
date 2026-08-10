"""Signal primitives.

Every function here returns a series whose value at date ``t`` uses only data up
to and including ``t``. The execution lag that turns a signal into a tradeable
position is applied once, centrally, in :mod:`wsp.backtest`.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def trend_ensemble(index: pd.Series, windows: Sequence[int]) -> pd.Series:
    """Fraction of moving-average filters currently signalling 'uptrend'.

    A single moving-average crossover is a coin-flip around its own threshold:
    the same market gets 0% or 100% exposure depending on a window choice nobody
    can justify ex ante. Averaging several windows turns that cliff into a ramp,
    which both removes the parameter sensitivity and cuts whipsaw trading.
    """
    if not windows:
        raise ValueError("windows must be non-empty")
    votes = [
        (index > index.rolling(int(w), min_periods=int(w)).mean()).astype(float)
        for w in windows
    ]
    signal = sum(votes) / len(votes)
    # Before the longest window has filled, we genuinely know nothing.
    warmup = max(int(w) for w in windows)
    signal.iloc[:warmup] = np.nan
    return signal


def realised_volatility(returns: pd.Series, window: int) -> pd.Series:
    """Annualised trailing standard deviation of daily returns."""
    return returns.rolling(int(window), min_periods=int(window)).std(
        ddof=1
    ) * np.sqrt(TRADING_DAYS)


def volatility_scalar(
    returns: pd.Series, target_vol: float, window: int, cap: float
) -> pd.Series:
    """Exposure that targets a constant portfolio volatility.

    Volatility is strongly autocorrelated - today's turbulence predicts
    tomorrow's - while returns are not. Scaling exposure down when realised
    volatility is high therefore removes risk that is not being paid for.
    """
    vol = realised_volatility(returns, window)
    scalar = target_vol / vol.replace(0.0, np.nan)
    return scalar.clip(upper=cap)


def apply_no_trade_band(target: pd.Series, width: float) -> pd.Series:
    """Hold the current weight until the target drifts more than ``width`` away.

    A continuously varying target would be rebalanced every single day, and the
    resulting turnover is what kills volatility-targeted strategies in practice.
    The band keeps the position economically identical while only trading when
    the gap is worth paying a spread to close.
    """
    if width <= 0:
        return target.fillna(0.0)

    values = np.nan_to_num(target.to_numpy(dtype=float), nan=0.0)
    held = np.empty_like(values)
    current = 0.0
    for i, want in enumerate(values):
        if abs(want - current) > width:
            current = want
        held[i] = current
    return pd.Series(held, index=target.index, name=target.name)
