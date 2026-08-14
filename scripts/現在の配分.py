#!/usr/bin/env python3
"""いま持つべき配分を計算する（毎月の発注前に実行するツール）。

    python scripts/現在の配分.py
    python scripts/現在の配分.py --資産 1000000 --上限 2.0
    python scripts/現在の配分.py --更新            # 最新データを取得してから計算

バックテストと同じコードで目標ウェイトを出すので、
「検証したものと違うものを運用する」というズレが起きない。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from wsp import multiasset  # noqa: E402
from wsp import portfolio as pf  # noqa: E402
from wsp.multiasset import MONTHS, RISK_ASSETS  # noqa: E402

ETF = {
    "equity": "VOO / VTI / eMAXIS Slim 米国株式(S&P500)",
    "bond": "IEF（米国7-10年国債）",
    "gold": "GLDM / IAU / 純金上場信託(1540)",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--資産", type=float, default=1_000_000, help="運用資産額（円・ドルなど単位は任意）")
    p.add_argument("--上限", type=float, default=3.0, help="総エクスポージャーの上限（倍）")
    p.add_argument("--更新", action="store_true", help="最新データを取得してから計算する")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.更新:
        print("最新データを取得しています ...")
        multiasset.fetch()

    data = multiasset.load()
    costs = pf.PortfolioCosts(max_gross=args.上限)
    strategy = pf.MultiAssetStrategy()

    trend = strategy.trend(data)
    risk_parity = strategy.risk_parity(data)
    target = strategy.weights(data)

    # レバレッジ倍率はバックテストと同じ手順で、過去データのみから決める。
    window = 10 * MONTHS
    base_returns = pf.run(data, target, costs).returns
    strategy_sd = float(base_returns.tail(window).std(ddof=1))
    benchmark_sd = float(data.benchmark.tail(window).std(ddof=1))
    multiple = 1.0
    if strategy_sd > 0:
        multiple = float(min(max(benchmark_sd / strategy_sd, 0.5), args.上限))

    latest = target.index[-1]
    weights = (target.loc[latest] * multiple).clip(lower=0.0)
    gross = float(weights.sum())
    if gross > args.上限:
        weights = weights * (args.上限 / gross)
        gross = args.上限

    print(f"\nデータ最終月: {latest.date()}")
    print(f"リスク調整レバレッジ: {multiple:.2f}倍（上限 {args.上限:g}倍）")
    print(f"総エクスポージャー: {gross:.2f}倍\n")

    rows = []
    for asset in data.assets:
        rows.append(
            {
                "資産": RISK_ASSETS[asset],
                "トレンド": f"{trend.loc[latest, asset]:.0%}",
                "リスク配分": f"{risk_parity.loc[latest, asset]:.1%}",
                "目標ウェイト": f"{weights[asset]:.1%}",
                "金額": f"{weights[asset] * args.資産:,.0f}",
                "参考商品": ETF[asset],
            }
        )
    cash_weight = 1.0 - gross
    rows.append(
        {
            "資産": "現金（借入）" if cash_weight < 0 else "現金",
            "トレンド": "-",
            "リスク配分": "-",
            "目標ウェイト": f"{cash_weight:.1%}",
            "金額": f"{cash_weight * args.資産:,.0f}",
            "参考商品": "MMF / 短期国債" if cash_weight >= 0 else "証券担保ローン・先物",
        }
    )
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n【トレンドの読み方】100% = 全ての移動平均が上向き、0% = 全て下向き（→ 現金へ退避）")
    if cash_weight < 0:
        print(f"【注意】総エクスポージャーが100%を超えています。"
              f"{-cash_weight * args.資産:,.0f} 分の借入または先物が必要です。")
    stale = (pd.Timestamp.today().to_period("M") - latest.to_period("M")).n
    if stale > 1:
        print(f"\n【警告】データが {stale} か月古いです。--更新 を付けて再実行してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
