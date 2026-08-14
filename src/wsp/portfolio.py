"""マルチアセット戦略：トレンドフィルター × リスクパリティ × レバレッジ。

単一資産版（`strategy.py`）が失敗した理由ははっきりしている。
S&P 500 だけを売り買いする戦略は、下げを避けた分だけ上げも取り逃がすので、
リスク当たりの効率が根本的には改善しない。

分散はそれを変える。米国株・米国債・金は相関がほぼゼロ（実測 0.03〜0.12）で、
3資産を各々のボラティリティの逆数で持つと、リターンをあまり落とさずに
ポートフォリオのボラティリティだけが大きく下がる。そこにトレンドフィルターを
かけて各資産の下落局面を外し、最後にレバレッジで元のリスク水準に戻す。

  1. トレンド … 各資産を複数の移動平均で判定し、賛成した割合をエクスポージャーにする
  2. リスクパリティ … ボラティリティの逆数で配分し、1資産にリスクを集中させない
  3. レバレッジ … 上がったシャープを「リターン」に変換する

1〜2はリターンの予測ではなくリスクの管理なので、期間を変えても生き残る。
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from . import metrics
from .multiasset import MONTHS, MultiAssetData


@dataclasses.dataclass(frozen=True)
class PortfolioCosts:
    """摩擦コスト。すべて戦略側の負担として控除する。"""

    cost_per_turnover: float = 0.0010  # 売買代金あたり片道10bp
    borrow_spread: float = 0.005  # レバレッジ部分の年率上乗せ金利
    max_gross: float = 3.0  # 総エクスポージャーの上限（倍）

    def __post_init__(self) -> None:
        if self.cost_per_turnover < 0 or self.borrow_spread < 0:
            raise ValueError("コストは負にできません")
        if self.max_gross <= 0:
            raise ValueError("max_gross は正の値が必要です")


@dataclasses.dataclass(frozen=True)
class MultiAssetStrategy:
    """月次リバランスの目標ウェイトを決める。"""

    trend_windows: tuple[int, ...] = (6, 10, 12)  # 月
    vol_window: int = 36  # 月
    leverage: float = 1.0

    def trend(self, data: MultiAssetData) -> pd.DataFrame:
        """各資産について、移動平均が上昇を示している割合（0〜1）。

        単一の移動平均は「窓の長さ」に賭ける行為でしかないので、
        複数の窓の多数決にして、0/1の崖をなだらかな坂に変える。
        """
        out = {}
        for asset in data.assets:
            level = (1 + data.returns[asset]).cumprod()
            votes = [
                (level > level.rolling(w, min_periods=w).mean()).astype(float)
                for w in self.trend_windows
            ]
            signal = sum(votes) / len(votes)
            signal.iloc[: max(self.trend_windows)] = np.nan
            out[asset] = signal
        return pd.DataFrame(out)

    def risk_parity(self, data: MultiAssetData) -> pd.DataFrame:
        """ボラティリティの逆数で正規化した配分。

        等金額だと株がリスクの大半を占めてしまい、名前だけの分散になる。
        """
        vol = data.returns.rolling(self.vol_window, min_periods=self.vol_window).std()
        inverse = 1.0 / vol.replace(0.0, np.nan)
        return inverse.div(inverse.sum(axis=1), axis=0)

    def weights(self, data: MultiAssetData) -> pd.DataFrame:
        """月末時点で判明している情報だけで決まる目標ウェイト。

        執行ラグは `run()` 側でかけるので、ここではシフトしない。
        """
        return (self.risk_parity(data) * self.trend(data) * self.leverage).fillna(0.0)


@dataclasses.dataclass(frozen=True)
class PortfolioResult:
    returns: pd.Series
    weights: pd.DataFrame  # 実際に保有したウェイト（ラグ適用後）
    turnover: pd.Series
    benchmark: pd.Series
    cash: pd.Series

    @property
    def gross(self) -> pd.Series:
        return self.weights.sum(axis=1)

    @property
    def annual_turnover(self) -> float:
        years = len(self.turnover) / MONTHS
        return float(self.turnover.sum() / years) if years > 0 else np.nan

    def summary(self, relative: bool = True) -> dict[str, float]:
        out = {
            "CAGR %": metrics.cagr(self.returns, MONTHS) * 100,
            "Vol %": metrics.volatility(self.returns, MONTHS) * 100,
            "Sharpe": metrics.sharpe(self.returns, self.cash, MONTHS),
            "Sortino": metrics.sortino(self.returns, self.cash, MONTHS),
            "MaxDD %": metrics.max_drawdown(self.returns) * 100,
            "Calmar": metrics.calmar(self.returns, MONTHS),
            "平均総エクスポージャー": float(self.gross.mean()),
            "年間回転率": self.annual_turnover,
        }
        if relative:
            bench = self.benchmark.reindex(self.returns.index).fillna(0.0)
            alpha, beta = metrics.alpha_beta(self.returns, bench, self.cash)
            out["超過CAGR %"] = (
                metrics.cagr(self.returns, MONTHS) - metrics.cagr(bench, MONTHS)
            ) * 100
            out["Beta"] = beta
        return out

    def annual_returns(self) -> pd.DataFrame:
        grow = lambda s: (1 + s).prod() - 1  # noqa: E731
        return pd.DataFrame(
            {
                "戦略": self.returns.groupby(self.returns.index.year).apply(grow),
                "S&P500": self.benchmark.groupby(self.benchmark.index.year).apply(grow),
            }
        ).assign(差=lambda d: d["戦略"] - d["S&P500"])


def run(
    data: MultiAssetData,
    target_weights: pd.DataFrame,
    costs: PortfolioCosts | None = None,
) -> PortfolioResult:
    """目標ウェイトを1か月遅れで執行したときの実現リターン。

        r = Σ wᵢ rᵢ + (1 - Σwᵢ)·現金 - max(Σwᵢ-1, 0)·借入スプレッド - 回転率·コスト
    """
    costs = costs or PortfolioCosts()

    target = target_weights.reindex(data.index).fillna(0.0).clip(lower=0.0)
    gross = target.sum(axis=1)
    # 総エクスポージャーが上限を超える月は、資産間の比率を保ったまま縮める。
    scale = (costs.max_gross / gross.replace(0.0, np.nan)).clip(upper=1.0).fillna(1.0)
    target = target.mul(scale, axis=0)

    held = target.shift(1).fillna(0.0)
    held_gross = held.sum(axis=1)
    turnover = held.diff().abs().sum(axis=1).fillna(held.abs().sum(axis=1))

    portfolio = (
        (held * data.returns).sum(axis=1)
        + (1.0 - held_gross) * data.cash
        - np.maximum(held_gross - 1.0, 0.0) * (costs.borrow_spread / MONTHS)
        - turnover * costs.cost_per_turnover
    )

    return PortfolioResult(
        returns=portfolio,
        weights=held,
        turnover=turnover,
        benchmark=data.benchmark,
        cash=data.cash,
    )


def buy_and_hold_equity(
    data: MultiAssetData, costs: PortfolioCosts | None = None
) -> PortfolioResult:
    """ベンチマーク：S&P 500 を100%持ち続ける。"""
    weights = pd.DataFrame(0.0, index=data.index, columns=data.assets)
    weights["equity"] = 1.0
    return run(data, weights, costs)


def walk_forward(
    data: MultiAssetData,
    strategy: MultiAssetStrategy | None = None,
    costs: PortfolioCosts | None = None,
    min_train_years: int = 12,
    match_window_years: int = 10,
) -> tuple[PortfolioResult, pd.DataFrame]:
    """毎年1月に、過去データだけでレバレッジ倍率を決め直す。

    倍率は「S&P 500 の実現ボラティリティ ÷ 戦略の実現ボラティリティ」。
    つまり同じリスク量に揃えるための倍率で、将来のリターンは一切見ていない。
    """
    strategy = strategy or MultiAssetStrategy()
    costs = costs or PortfolioCosts()

    base_weights = strategy.weights(data)
    base_returns = run(data, base_weights, costs).returns
    window = match_window_years * MONTHS

    first_year = int(data.index[0].year) + min_train_years
    last_year = int(data.index[-1].year)

    leverage = pd.Series(0.0, index=data.index)
    rows: list[dict[str, object]] = []

    for year in range(first_year, last_year + 1):
        train = slice(None, f"{year - 1}-12-31")
        test_dates = data.index[
            (data.index >= f"{year}-01-01") & (data.index <= f"{year}-12-31")
        ]
        if len(test_dates) == 0:
            continue

        strategy_sd = float(base_returns.loc[train].tail(window).std(ddof=1))
        benchmark_sd = float(data.benchmark.loc[train].tail(window).std(ddof=1))
        multiple = 1.0
        if strategy_sd > 0 and len(base_returns.loc[train]) >= MONTHS:
            multiple = float(np.clip(benchmark_sd / strategy_sd, 0.5, costs.max_gross))

        leverage.loc[test_dates] = multiple
        rows.append({"year": year, "leverage": round(multiple, 3)})

    if not rows:
        raise ValueError(f"{min_train_years}年分の学習期間が確保できません")

    start = f"{rows[0]['year']}-01-01"
    sliced = data.slice(start, None)
    weights = base_weights.loc[start:].mul(leverage.loc[start:], axis=0)
    return run(sliced, weights, costs), pd.DataFrame(rows).set_index("year")


def start_date_table(result: PortfolioResult, starts: tuple[int, ...]) -> pd.DataFrame:
    """開始年をずらしても勝てるか。単一資産版はこの表で失格になった。"""
    end = int(result.returns.index[-1].year)
    rows = []
    for start in starts:
        window = slice(f"{start}-01-01", None)
        strat = result.returns.loc[window]
        bench = result.benchmark.loc[window]
        if len(strat) < 3 * MONTHS:
            continue
        rows.append(
            {
                "期間": f"{start}-{end}",
                "年数": round(len(strat) / MONTHS),
                "戦略 CAGR %": metrics.cagr(strat, MONTHS) * 100,
                "S&P500 CAGR %": metrics.cagr(bench, MONTHS) * 100,
                "差 %": (metrics.cagr(strat, MONTHS) - metrics.cagr(bench, MONTHS)) * 100,
                "戦略 Sharpe": metrics.sharpe(strat, result.cash.loc[window], MONTHS),
                "S&P500 Sharpe": metrics.sharpe(bench, result.cash.loc[window], MONTHS),
                "戦略 最大DD %": metrics.max_drawdown(strat) * 100,
                "S&P500 最大DD %": metrics.max_drawdown(bench) * 100,
            }
        )
    return pd.DataFrame(rows).set_index("期間")
