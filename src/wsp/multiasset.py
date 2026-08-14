"""マルチアセット（株式・債券・金・現金）の月次データ。

単一資産のタイミング戦略では S&P 500 に勝てないことが分かったため
（`results/start_date_sensitivity.csv` 参照）、相関の低い資産へ分散する。
1953年5月以降の月次トータルリターンを4資産ぶん組み立てる。

出典と既知の注意点は `data/SOURCES.md` を参照。
"""

from __future__ import annotations

import dataclasses
import pathlib

import numpy as np
import pandas as pd

from . import data as daily_data

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA_FILE = REPO_ROOT / "data" / "multi_asset_monthly.csv.gz"

GOLD_URL = "https://raw.githubusercontent.com/datasets/gold-prices/main/data/monthly.csv"
YIELD_URL = (
    "https://raw.githubusercontent.com/datasets/bond-yields-us-10y/main/data/monthly.csv"
)

MONTHS = 12

#: 保有対象のリスク資産。現金は残余として扱うのでここには含めない。
RISK_ASSETS: dict[str, str] = {
    "equity": "米国株式",
    "bond": "米国10年国債",
    "gold": "金",
}


@dataclasses.dataclass(frozen=True)
class MultiAssetData:
    """月次のリスク資産リターンと、対応する現金リターン。"""

    returns: pd.DataFrame  # 列 = RISK_ASSETS のキー
    cash: pd.Series
    equity_index: pd.Series  # S&P 500 トータルリターン指数（ベンチマーク用）

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.returns.index

    @property
    def assets(self) -> list[str]:
        return list(self.returns.columns)

    @property
    def benchmark(self) -> pd.Series:
        """ベンチマーク＝S&P 500 の買い持ち。"""
        return self.returns["equity"]

    def slice(self, start: str | None = None, end: str | None = None) -> "MultiAssetData":
        sl = slice(start, end)
        return MultiAssetData(
            returns=self.returns.loc[sl],
            cash=self.cash.loc[sl],
            equity_index=self.equity_index.loc[sl],
        )

    def __len__(self) -> int:
        return len(self.returns)


def bond_price(yield_rate: float, maturity: float, coupon: float, freq: int = 2) -> float:
    """額面1の利付債の価格（経過利息込みのダーティプライス）。"""
    n = int(np.ceil(maturity * freq))
    times = maturity - np.arange(n - 1, -1, -1) / freq
    times = times[times > 1e-9]
    flows = np.full(len(times), coupon / freq)
    flows[-1] += 1.0
    return float(np.sum(flows / (1 + yield_rate / freq) ** (times * freq)))


def bond_total_return(yields_pct: pd.Series, maturity: float = 10.0) -> pd.Series:
    """10年国債を1か月保有したときのトータルリターン。

    毎月、その時点の利回りで発行されたパー債（クーポン＝利回り）を買い、
    1か月後に「残存 10年-1か月・新しい利回り」で値洗いする。
    ダーティプライスに経過利息が含まれるので、クーポンを別途足さない
    （足すと二重計上になる）。
    """
    y = yields_pct / 100.0
    out: list[float] = []
    for previous, current in zip(y.shift(1), y):
        if not (np.isfinite(previous) and np.isfinite(current)):
            out.append(np.nan)
            continue
        out.append(bond_price(current, maturity - 1 / MONTHS, coupon=previous) - 1.0)
    return pd.Series(out, index=y.index, name="bond")


def fetch(destination: pathlib.Path | str | None = None) -> pathlib.Path:
    """上流データを取得して月次パネルを作り直す。"""
    destination = pathlib.Path(destination) if destination else DATA_FILE
    destination.parent.mkdir(parents=True, exist_ok=True)

    gold = pd.read_csv(GOLD_URL)
    gold["date"] = pd.PeriodIndex(gold["Date"], freq="M").to_timestamp("M")
    gold = gold.set_index("date")["Price"].astype(float)

    yields = pd.read_csv(YIELD_URL, parse_dates=["Date"])
    yields["date"] = yields["Date"].dt.to_period("M").dt.to_timestamp("M")
    yields = yields.set_index("date")["Rate"].astype(float)

    daily = daily_data.load(start=None)
    panel = pd.concat(
        {
            "equity_index": daily.total_return.resample("ME").last(),
            "cash_index": daily.rf_index.resample("ME").last(),
            "gold_price": gold,
            "yield_10y": yields,
        },
        axis=1,
        sort=True,
    ).dropna()

    panel["equity"] = panel["equity_index"].pct_change()
    panel["cash"] = panel["cash_index"].pct_change()
    panel["gold"] = panel["gold_price"].pct_change()
    panel["bond"] = bond_total_return(panel["yield_10y"])

    columns = ["equity", "bond", "gold", "cash", "equity_index", "yield_10y", "gold_price"]
    panel.dropna()[columns].to_csv(destination, index_label="date", compression="gzip")
    return destination


def load(
    path: pathlib.Path | str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> MultiAssetData:
    path = pathlib.Path(path) if path is not None else DATA_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"{path} が見つかりません。`python scripts/fetch_data.py --multi-asset` を実行してください。"
        )
    frame = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    result = MultiAssetData(
        returns=frame[list(RISK_ASSETS)].astype(float),
        cash=frame["cash"].astype(float),
        equity_index=frame["equity_index"].astype(float),
    )
    validate(result)
    return result.slice(start, end)


def validate(data: MultiAssetData) -> None:
    """バックテストを静かに壊すデータ異常は、ここで例外にする。"""
    if not data.index.is_monotonic_increasing:
        raise ValueError("日付が昇順ではありません")
    if data.index.has_duplicates:
        raise ValueError("日付が重複しています")
    if data.returns.isna().any().any() or data.cash.isna().any():
        raise ValueError("リターン系列に欠損があります")
    extreme = data.returns.abs() > 0.6
    if extreme.any().any():
        where = data.returns[extreme.any(axis=1)].index[:3]
        raise ValueError(f"月次で±60%超の異常値: {[str(d.date()) for d in where]}")
