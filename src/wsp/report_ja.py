"""マルチアセット戦略の日本語レポートとグラフ。"""

from __future__ import annotations

import pathlib

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from . import metrics  # noqa: E402
from .multiasset import MONTHS, RISK_ASSETS  # noqa: E402
from .portfolio import PortfolioResult  # noqa: E402

# 検証済みカテゴリカル配色のスロット1〜3、および発散配色とチャート用インク。
STRATEGY = "#2a78d6"
BENCHMARK = "#eb6834"
SERIES = {"equity": "#2a78d6", "bond": "#eb6834", "gold": "#1baf7a"}
POSITIVE = "#2a78d6"
NEGATIVE = "#e34948"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"


def use_japanese_font() -> None:
    plt.rcParams["font.family"] = ["IPAGothic", "IPAPGothic", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
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
use_japanese_font()


def _style(ax: plt.Axes, title: str, ylabel: str = "") -> None:
    ax.set_title(title, color=INK, fontsize=11.5, loc="left", pad=10)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.set_axisbelow(True)
    ax.grid(axis="x", visible=False)


def plot_overview(result: PortfolioResult, path: pathlib.Path, title: str) -> pathlib.Path:
    """資産曲線・ドローダウン・ローリング超過・エクスポージャーの4段。

    単位の異なる指標を同じ軸に重ねない（第2軸は使わない）。
    """
    strat_eq = metrics.equity_curve(result.returns)
    bench_eq = metrics.equity_curve(result.benchmark)
    strat_dd = metrics.drawdown(result.returns) * 100
    bench_dd = metrics.drawdown(result.benchmark) * 100
    rolling = (
        metrics.rolling_outperformance(result.returns, result.benchmark, 10, MONTHS) * 100
    )

    fig, axes = plt.subplots(4, 1, figsize=(11, 13), sharex=True, height_ratios=[3, 2, 2, 1.6])
    fig.suptitle(title, color=INK, fontsize=14, x=0.06, ha="left", y=0.985)

    ax = axes[0]
    ax.plot(bench_eq.index, bench_eq, color=BENCHMARK, linewidth=2, label="S&P500 買い持ち")
    ax.plot(strat_eq.index, strat_eq, color=STRATEGY, linewidth=2, label="本戦略")
    ax.set_yscale("log")
    _style(ax, "1ドルの成長（対数目盛・配当再投資）")
    ax.legend(frameon=False, loc="upper left", labelcolor=INK)
    for series, color in ((strat_eq, STRATEGY), (bench_eq, BENCHMARK)):
        ax.annotate(
            f"${series.iloc[-1]:,.0f}",
            xy=(series.index[-1], series.iloc[-1]),
            xytext=(6, 0), textcoords="offset points",
            color=color, fontsize=9, va="center",
        )

    ax = axes[1]
    ax.plot(bench_dd.index, bench_dd, color=BENCHMARK, linewidth=1.6, label="S&P500 買い持ち")
    ax.fill_between(bench_dd.index, bench_dd, 0, color=BENCHMARK, alpha=0.13, linewidth=0)
    ax.plot(strat_dd.index, strat_dd, color=STRATEGY, linewidth=1.6, label="本戦略")
    ax.fill_between(strat_dd.index, strat_dd, 0, color=STRATEGY, alpha=0.16, linewidth=0)
    _style(ax, "直近高値からの下落率", "%")
    ax.legend(frameon=False, loc="lower left", labelcolor=INK)

    ax = axes[2]
    ax.axhline(0, color=AXIS, linewidth=1)
    ax.fill_between(rolling.index, rolling, 0, where=rolling >= 0, color=POSITIVE, alpha=0.75, linewidth=0)
    ax.fill_between(rolling.index, rolling, 0, where=rolling < 0, color=NEGATIVE, alpha=0.75, linewidth=0)
    _style(ax, "10年ローリング超過リターン（0より上＝戦略が優勢）", "%")

    ax = axes[3]
    ax.plot(result.gross.index, result.gross, color=STRATEGY, linewidth=1.0)
    ax.fill_between(result.gross.index, result.gross, 0, color=STRATEGY, alpha=0.16, linewidth=0)
    ax.axhline(1.0, color=BENCHMARK, linewidth=1.2, linestyle="--")
    ax.annotate(
        "100% = S&P500と同じ投資額",
        xy=(result.gross.index[int(len(result.gross) * 0.02)], 1.0),
        xytext=(0, 6), textcoords="offset points", color=BENCHMARK, fontsize=9,
    )
    _style(ax, "実際に保有した総エクスポージャー", "倍")

    fig.tight_layout(rect=(0, 0, 1, 0.965))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_allocation(result: PortfolioResult, path: pathlib.Path) -> pathlib.Path:
    """資産配分の推移（積み上げ）。トレンドで外れた分は現金になる。"""
    weights = result.weights.resample("YE").mean()
    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.stackplot(
        weights.index,
        *[weights[a] for a in result.weights.columns],
        labels=[RISK_ASSETS[a] for a in result.weights.columns],
        colors=[SERIES[a] for a in result.weights.columns],
        edgecolor=SURFACE, linewidth=0.6,
    )
    ax.axhline(1.0, color=INK_MUTED, linewidth=1.0, linestyle="--")
    _style(ax, "資産配分の推移（年平均・残りは現金）", "倍")
    ax.legend(frameon=False, loc="upper left", ncols=3, labelcolor=INK)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_annual(result: PortfolioResult, path: pathlib.Path) -> pathlib.Path:
    annual = result.annual_returns()
    excess = annual["差"] * 100
    colors = [POSITIVE if v >= 0 else NEGATIVE for v in excess]
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.bar(excess.index, excess, color=colors, width=0.75, linewidth=0)
    ax.axhline(0, color=AXIS, linewidth=1)
    _style(ax, f"暦年リターン − S&P500（勝った年 {(excess >= 0).mean() * 100:.0f}%）", "ポイント")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def crisis_table(result: PortfolioResult) -> pd.DataFrame:
    """歴史的な暴落局面での比較。分散が効くのはここ。"""
    events = {
        "1973-74 オイルショック": ("1973-01-01", "1974-12-31"),
        "1980-82 ボルカー・ショック": ("1980-01-01", "1982-12-31"),
        "1987 ブラックマンデー": ("1987-08-01", "1987-12-31"),
        "2000-02 ITバブル崩壊": ("2000-01-01", "2002-12-31"),
        "2007-09 世界金融危機": ("2007-10-01", "2009-03-31"),
        "2020 コロナ・ショック": ("2020-01-01", "2020-04-30"),
        "2022 インフレ・利上げ": ("2022-01-01", "2022-12-31"),
    }
    rows = []
    for label, (start, end) in events.items():
        window = slice(start, end)
        strat, bench = result.returns.loc[window], result.benchmark.loc[window]
        if len(strat) < 2:
            continue
        rows.append(
            {
                "局面": label,
                "戦略 %": ((1 + strat).prod() - 1) * 100,
                "S&P500 %": ((1 + bench).prod() - 1) * 100,
                "差 %": ((1 + strat).prod() - (1 + bench).prod()) * 100,
            }
        )
    return pd.DataFrame(rows).set_index("局面")


def decade_table(result: PortfolioResult) -> pd.DataFrame:
    first = int(result.returns.index[0].year) // 10 * 10
    last = int(result.returns.index[-1].year)
    rows = []
    for decade in range(first, last + 1, 10):
        window = slice(f"{decade}-01-01", f"{decade + 9}-12-31")
        strat, bench = result.returns.loc[window], result.benchmark.loc[window]
        if len(strat) < 24:
            continue
        rows.append(
            {
                "年代": f"{decade}年代",
                "戦略 CAGR %": metrics.cagr(strat, MONTHS) * 100,
                "S&P500 CAGR %": metrics.cagr(bench, MONTHS) * 100,
                "差 %": (metrics.cagr(strat, MONTHS) - metrics.cagr(bench, MONTHS)) * 100,
                "戦略 最大DD %": metrics.max_drawdown(strat) * 100,
                "S&P500 最大DD %": metrics.max_drawdown(bench) * 100,
            }
        )
    return pd.DataFrame(rows).set_index("年代")


def format_table(frame: pd.DataFrame, decimals: int = 2) -> str:
    return frame.round(decimals).to_markdown()
