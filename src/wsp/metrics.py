"""Performance and risk metrics.

Everything takes a series of daily simple returns. Excess-return statistics need
the matching daily risk-free series so that Sharpe ratios are comparable across
the 1980s (double-digit cash rates) and the 2010s (zero).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _align(returns: pd.Series, rf: pd.Series | None) -> pd.Series:
    if rf is None:
        return pd.Series(0.0, index=returns.index)
    return rf.reindex(returns.index).fillna(0.0)


def equity_curve(returns: pd.Series, initial: float = 1.0) -> pd.Series:
    return initial * (1.0 + returns).cumprod()


def cagr(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    years = len(returns) / periods
    if years <= 0:
        return np.nan
    growth = float((1.0 + returns).prod())
    if growth <= 0:
        return -1.0
    return growth ** (1.0 / years) - 1.0


def volatility(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    return float(returns.std(ddof=1) * np.sqrt(periods))


def sharpe(
    returns: pd.Series, rf: pd.Series | None = None, periods: int = TRADING_DAYS
) -> float:
    excess = returns - _align(returns, rf)
    sd = excess.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return np.nan
    return float(excess.mean() / sd * np.sqrt(periods))


def sortino(
    returns: pd.Series, rf: pd.Series | None = None, periods: int = TRADING_DAYS
) -> float:
    excess = returns - _align(returns, rf)
    downside = excess.clip(upper=0.0)
    dd = np.sqrt((downside**2).mean())
    if dd == 0 or np.isnan(dd):
        return np.nan
    return float(excess.mean() / dd * np.sqrt(periods))


def drawdown(returns: pd.Series) -> pd.Series:
    curve = equity_curve(returns)
    return curve / curve.cummax() - 1.0


def max_drawdown(returns: pd.Series) -> float:
    return float(drawdown(returns).min())


def calmar(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    mdd = abs(max_drawdown(returns))
    return float(cagr(returns, periods) / mdd) if mdd > 0 else np.nan


def ulcer_index(returns: pd.Series) -> float:
    """RMS drawdown - penalises long, deep underwater stretches."""
    return float(np.sqrt((drawdown(returns) ** 2).mean()))


def time_under_water(returns: pd.Series) -> float:
    """Fraction of days spent below a previous peak."""
    return float((drawdown(returns) < -1e-12).mean())


def alpha_beta(
    returns: pd.Series, benchmark: pd.Series, rf: pd.Series | None = None
) -> tuple[float, float]:
    """Annualised CAPM alpha and beta of ``returns`` against ``benchmark``."""
    cash = _align(returns, rf)
    y = (returns - cash).to_numpy()
    x = (benchmark.reindex(returns.index).fillna(0.0) - cash).to_numpy()
    var = x.var(ddof=1)
    if var == 0 or np.isnan(var):
        return np.nan, np.nan
    beta = float(np.cov(y, x, ddof=1)[0, 1] / var)
    alpha_daily = float(y.mean() - beta * x.mean())
    return alpha_daily * TRADING_DAYS, beta


def newey_west_tstat(returns: pd.Series, lags: int = 21) -> float:
    """t-statistic of the mean, robust to autocorrelation and heteroskedasticity.

    Used on a daily excess-return-over-benchmark series to ask whether the
    outperformance is distinguishable from luck.
    """
    x = returns.dropna().to_numpy()
    n = len(x)
    if n < 30:
        return np.nan
    demeaned = x - x.mean()
    variance = float(demeaned @ demeaned) / n
    for lag in range(1, min(lags, n - 1) + 1):
        weight = 1.0 - lag / (lags + 1.0)
        cov = float(demeaned[lag:] @ demeaned[:-lag]) / n
        variance += 2.0 * weight * cov
    if variance <= 0:
        return np.nan
    return float(x.mean() / np.sqrt(variance / n))


def expected_max_sharpe(n_trials: int, trial_sharpe_std: float) -> float:
    """Sharpe ratio the *luckiest* of ``n_trials`` worthless strategies would show.

    Bailey & Lopez de Prado (2014). The bar rises with both the number of
    configurations searched and how widely their results are spread: searching
    216 variants of a strategy guarantees the best one looks good, so this is
    the number the winner has to beat before it means anything.
    """
    if n_trials < 2 or not np.isfinite(trial_sharpe_std) or trial_sharpe_std <= 0:
        return 0.0
    euler = 0.5772156649015329
    z1 = _norm_ppf(1.0 - 1.0 / n_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (n_trials * np.e))
    return float(trial_sharpe_std * ((1.0 - euler) * z1 + euler * z2))


def deflated_sharpe(
    observed_sharpe: float,
    n_trials: int,
    n_obs: int,
    skew: float,
    kurtosis: float,
    trial_sharpe_std: float,
) -> float:
    """Probability the true Sharpe is positive once selection bias is removed.

    All Sharpe inputs are annualised; the arithmetic is done per-observation.
    Returns NaN rather than a number when the inputs cannot support one.
    """
    from math import erf, sqrt

    if n_obs < 2 or not np.isfinite(observed_sharpe):
        return np.nan

    scale = np.sqrt(TRADING_DAYS)
    sr = observed_sharpe / scale
    benchmark_sr = expected_max_sharpe(n_trials, trial_sharpe_std / scale)

    denom = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr**2
    if denom <= 0:
        return np.nan
    stat = (sr - benchmark_sr) * sqrt(n_obs - 1) / sqrt(denom)
    return 0.5 * (1.0 + erf(stat / sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    if not 0.0 < p < 1.0:
        return np.nan
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    lo, hi = 0.02425, 1 - 0.02425
    if p < lo:
        q = np.sqrt(-2 * np.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > hi:
        q = np.sqrt(-2 * np.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1
    )


def summarise(
    returns: pd.Series,
    rf: pd.Series | None = None,
    benchmark: pd.Series | None = None,
    turnover: float | None = None,
    relative: bool = True,
) -> dict[str, float]:
    """The metric block reported for every strategy."""
    out: dict[str, float] = {
        "CAGR %": cagr(returns) * 100,
        "Vol %": volatility(returns) * 100,
        "Sharpe": sharpe(returns, rf),
        "Sortino": sortino(returns, rf),
        "MaxDD %": max_drawdown(returns) * 100,
        "Calmar": calmar(returns),
        "Ulcer %": ulcer_index(returns) * 100,
        "TimeUnderWater %": time_under_water(returns) * 100,
    }
    if benchmark is not None and relative:
        bench = benchmark.reindex(returns.index).fillna(0.0)
        a, b = alpha_beta(returns, bench, rf)
        out["Alpha %"] = a * 100
        out["Beta"] = b
        out["ExcessCAGR %"] = (cagr(returns) - cagr(bench)) * 100
        out["t-stat"] = newey_west_tstat(returns - bench)
    if turnover is not None:
        out["Turnover x/yr"] = turnover
    return out


def summary_table(blocks: dict[str, dict[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(blocks).T


def rolling_outperformance(
    returns: pd.Series, benchmark: pd.Series, years: int = 10, periods: int = TRADING_DAYS
) -> pd.Series:
    """Rolling N-year total-return difference vs the benchmark."""
    window = years * periods
    s = equity_curve(returns)
    b = equity_curve(benchmark.reindex(returns.index).fillna(0.0))
    return (s / s.shift(window)) / (b / b.shift(window)) - 1.0
