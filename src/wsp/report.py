"""Tables and charts for the backtest report."""

from __future__ import annotations

import pathlib

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import metrics  # noqa: E402
from .backtest import BacktestResult  # noqa: E402

TRADING_DAYS = 252

# Validated categorical slots 1 and 2, plus the diverging pair and chrome ink.
STRATEGY = "#2a78d6"
BENCHMARK = "#eb6834"
POSITIVE = "#2a78d6"
NEGATIVE = "#e34948"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.family": "sans-serif",
        "font.size": 10,
        "text.color": INK,
        "axes.labelcolor": INK_MUTED,
        "axes.edgecolor": AXIS,
        "xtick.color": INK_MUTED,
        "ytick.color": INK_MUTED,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.7,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)

ERAS: dict[str, tuple[str, str]] = {
    "1928-1949 Crash & war": ("1928-01-01", "1949-12-31"),
    "1950-1972 Post-war boom": ("1950-01-01", "1972-12-31"),
    "1973-1982 Stagflation": ("1973-01-01", "1982-12-31"),
    "1983-1999 Great bull": ("1983-01-01", "1999-12-31"),
    "2000-2012 Lost decade": ("2000-01-01", "2012-12-31"),
    "2013-2025 QE & AI bull": ("2013-01-01", "2025-12-31"),
}


def period_table(result: BacktestResult, periods: dict[str, tuple[str, str]]) -> pd.DataFrame:
    """Strategy vs benchmark, broken out by market regime.

    A single full-sample number hides everything that matters: the honest
    question is not whether the average is better but *when* it is worse.
    """
    rows = []
    for label, (start, end) in periods.items():
        window = slice(start, end)
        strat = result.returns.loc[window]
        bench = result.benchmark.loc[window]
        if len(strat) < 60:
            continue
        rows.append(
            {
                "period": label,
                "strategy CAGR %": metrics.cagr(strat) * 100,
                "benchmark CAGR %": metrics.cagr(bench) * 100,
                "excess %": (metrics.cagr(strat) - metrics.cagr(bench)) * 100,
                "strategy vol %": metrics.volatility(strat) * 100,
                "benchmark vol %": metrics.volatility(bench) * 100,
                "strategy MaxDD %": metrics.max_drawdown(strat) * 100,
                "benchmark MaxDD %": metrics.max_drawdown(bench) * 100,
            }
        )
    return pd.DataFrame(rows).set_index("period")


def decade_table(result: BacktestResult) -> pd.DataFrame:
    first = int(result.returns.index[0].year) // 10 * 10
    last = int(result.returns.index[-1].year)
    periods = {
        f"{d}s": (f"{d}-01-01", f"{d + 9}-12-31") for d in range(first, last + 1, 10)
    }
    return period_table(result, periods)


def _style_axis(ax: plt.Axes, title: str, ylabel: str = "") -> None:
    ax.set_title(title, color=INK, fontsize=11, fontweight="bold", loc="left", pad=10)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.set_axisbelow(True)
    ax.grid(axis="x", visible=False)


def plot_overview(result: BacktestResult, path: pathlib.Path, title: str) -> pathlib.Path:
    """Four stacked panels on one shared time axis.

    Stacked small multiples rather than twin y-axes: growth, drawdown, relative
    performance and exposure are different units and must not share a scale.
    """
    strat_eq = result.equity
    bench_eq = result.benchmark_equity
    strat_dd = metrics.drawdown(result.returns) * 100
    bench_dd = metrics.drawdown(result.benchmark) * 100
    rolling = metrics.rolling_outperformance(result.returns, result.benchmark, 10) * 100

    fig, axes = plt.subplots(4, 1, figsize=(11, 13), sharex=True, height_ratios=[3, 2, 2, 1.6])
    fig.suptitle(title, color=INK, fontsize=14, fontweight="bold", x=0.06, ha="left", y=0.98)

    ax = axes[0]
    ax.plot(bench_eq.index, bench_eq, color=BENCHMARK, linewidth=2, label="S&P 500 buy & hold")
    ax.plot(strat_eq.index, strat_eq, color=STRATEGY, linewidth=2, label="Strategy")
    ax.set_yscale("log")
    _style_axis(ax, "Growth of $1 (log scale, dividends reinvested)")
    ax.legend(frameon=False, loc="upper left", labelcolor=INK)
    for series, color in ((strat_eq, STRATEGY), (bench_eq, BENCHMARK)):
        ax.annotate(
            f"${series.iloc[-1]:,.0f}",
            xy=(series.index[-1], series.iloc[-1]),
            xytext=(6, 0),
            textcoords="offset points",
            color=color,
            fontsize=9,
            fontweight="bold",
            va="center",
        )

    ax = axes[1]
    ax.plot(bench_dd.index, bench_dd, color=BENCHMARK, linewidth=1.6, label="S&P 500 buy & hold")
    ax.fill_between(bench_dd.index, bench_dd, 0, color=BENCHMARK, alpha=0.13, linewidth=0)
    ax.plot(strat_dd.index, strat_dd, color=STRATEGY, linewidth=1.6, label="Strategy")
    ax.fill_between(strat_dd.index, strat_dd, 0, color=STRATEGY, alpha=0.16, linewidth=0)
    _style_axis(ax, "Drawdown from previous peak", "%")
    ax.legend(frameon=False, loc="lower left", labelcolor=INK)

    ax = axes[2]
    ax.axhline(0, color=AXIS, linewidth=1)
    ax.fill_between(
        rolling.index, rolling, 0, where=rolling >= 0, color=POSITIVE, alpha=0.75, linewidth=0
    )
    ax.fill_between(
        rolling.index, rolling, 0, where=rolling < 0, color=NEGATIVE, alpha=0.75, linewidth=0
    )
    _style_axis(ax, "Rolling 10-year outperformance vs S&P 500 (above 0 = strategy ahead)", "%")

    ax = axes[3]
    ax.plot(result.weights.index, result.weights, color=STRATEGY, linewidth=1.0)
    ax.fill_between(result.weights.index, result.weights, 0, color=STRATEGY, alpha=0.16, linewidth=0)
    ax.axhline(1.0, color=BENCHMARK, linewidth=1.2, linestyle="--")
    ax.annotate(
        "100% = benchmark exposure",
        xy=(result.weights.index[int(len(result.weights) * 0.02)], 1.0),
        xytext=(0, 6),
        textcoords="offset points",
        color=BENCHMARK,
        fontsize=9,
    )
    _style_axis(ax, "Equity exposure actually held", "x capital")
    ax.set_xlabel("")

    fig.tight_layout(rect=(0, 0, 1, 0.965))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_annual_excess(result: BacktestResult, path: pathlib.Path) -> pathlib.Path:
    """Per-calendar-year win/loss against the benchmark."""
    annual = result.annual_returns()
    excess = annual["excess"] * 100
    colors = [POSITIVE if v >= 0 else NEGATIVE for v in excess]

    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.bar(excess.index, excess, color=colors, width=0.75, linewidth=0)
    ax.axhline(0, color=AXIS, linewidth=1)
    _style_axis(
        ax,
        f"Calendar-year return minus S&P 500  ({(excess >= 0).mean() * 100:.0f}% of years ahead)",
        "percentage points",
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def start_date_sensitivity(result: BacktestResult, starts: tuple[int, ...]) -> pd.DataFrame:
    """The same strategy judged from progressively later starting points.

    This is the single most revealing table in the repo, and the one most
    backtests omit. A genuine edge degrades gracefully as the start date moves
    forward. An edge that collapses - or reverses - once an early window is
    excluded was never an edge; it was one lucky stretch carrying a long
    average, and no investor starting later would have experienced it.
    """
    end = int(result.returns.index[-1].year)
    rows = []
    for start in starts:
        window = slice(f"{start}-01-01", None)
        strat = result.returns.loc[window]
        bench = result.benchmark.loc[window]
        if len(strat) < 3 * TRADING_DAYS:
            continue
        rows.append(
            {
                "from": f"{start}-{end}",
                "years": round(len(strat) / TRADING_DAYS),
                "strategy CAGR %": metrics.cagr(strat) * 100,
                "S&P 500 CAGR %": metrics.cagr(bench) * 100,
                "excess %": (metrics.cagr(strat) - metrics.cagr(bench)) * 100,
                "strategy Sharpe": metrics.sharpe(strat, result.rf.loc[window]),
                "S&P 500 Sharpe": metrics.sharpe(bench, result.rf.loc[window]),
                "strategy MaxDD %": metrics.max_drawdown(strat) * 100,
                "S&P 500 MaxDD %": metrics.max_drawdown(bench) * 100,
            }
        )
    return pd.DataFrame(rows).set_index("from")


def cost_sensitivity(
    result: BacktestResult, base_cost: float, levels_bps: tuple[float, ...] = (0, 5, 10, 20, 30, 50)
) -> pd.DataFrame:
    """Re-price the same trades under different friction assumptions.

    Turnover enters the return linearly, so the whole curve can be recovered
    from one backtest without re-running it. The question this answers is the
    one that decides whether a strategy is real: how expensive would trading
    have to get before the edge disappears?
    """
    bench_cagr = metrics.cagr(result.benchmark) * 100
    rows = []
    for bps in levels_bps:
        adjusted = result.returns + result.turnover * (base_cost - bps / 10_000)
        rows.append(
            {
                "cost bps": bps,
                "CAGR %": metrics.cagr(adjusted) * 100,
                "Sharpe": metrics.sharpe(adjusted, result.rf),
                "excess vs S&P %": metrics.cagr(adjusted) * 100 - bench_cagr,
            }
        )
    return pd.DataFrame(rows).set_index("cost bps")


def breakeven_cost_bps(result: BacktestResult, base_cost: float) -> float:
    """Trading cost at which the strategy's CAGR equals the benchmark's."""
    target = metrics.cagr(result.benchmark)
    lo, hi = 0.0, 500.0
    for _ in range(60):
        mid = (lo + hi) / 2
        adjusted = result.returns + result.turnover * (base_cost - mid / 10_000)
        if metrics.cagr(adjusted) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def format_table(frame: pd.DataFrame, decimals: int = 2) -> str:
    return frame.round(decimals).to_markdown()


def summary_frame(blocks: dict[str, dict[str, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(blocks).T
    return frame.replace([np.inf, -np.inf], np.nan)
