#!/usr/bin/env python3
"""マルチアセット戦略を検証し、results_multiasset/ に日本語レポートを出力する。

    python scripts/run_multiasset.py                  # 標準（総エクスポージャー上限3倍）
    python scripts/run_multiasset.py --max-gross 2.0  # 標準型
    python scripts/run_multiasset.py --max-gross 1.0  # レバレッジなし
    python scripts/run_multiasset.py --cost-bps 30    # コスト前提を厳しく
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from wsp import multiasset, report_ja  # noqa: E402
from wsp import portfolio as pf  # noqa: E402

OUTDIR = pathlib.Path(__file__).resolve().parents[1] / "results_multiasset"
START_YEARS = (1966, 1980, 1990, 2000, 2010)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--max-gross", type=float, default=3.0, help="総エクスポージャーの上限（倍）")
    p.add_argument("--cost-bps", type=float, default=10.0, help="売買代金あたり片道コスト(bp)")
    p.add_argument("--borrow-bps", type=float, default=50.0, help="借入の上乗せ金利(bp/年)")
    p.add_argument("--min-train-years", type=int, default=12)
    p.add_argument("--outdir", type=pathlib.Path, default=OUTDIR)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    data = multiasset.load()
    costs = pf.PortfolioCosts(
        cost_per_turnover=args.cost_bps / 10_000,
        borrow_spread=args.borrow_bps / 10_000,
        max_gross=args.max_gross,
    )
    print(f"期間: {data.index[0].date()} 〜 {data.index[-1].date()}（{len(data)}か月）")
    print(f"コスト: 片道{args.cost_bps:.0f}bp / 借入+{args.borrow_bps:.0f}bp / 上限{args.max_gross:g}倍\n")

    strategy = pf.MultiAssetStrategy()
    result, leverage = pf.walk_forward(
        data, strategy, costs, min_train_years=args.min_train_years
    )
    oos_start = str(result.returns.index[0].date())
    oos = data.slice(oos_start, None)

    benchmark = pf.buy_and_hold_equity(oos, costs)
    unlevered = pf.run(oos, strategy.weights(oos), costs)
    risk_parity_only = pf.run(oos, strategy.risk_parity(oos), costs)
    sixty_forty = pd.DataFrame(
        {"equity": 0.6, "bond": 0.4, "gold": 0.0}, index=oos.index
    )
    balanced = pf.run(oos, sixty_forty, costs)

    summary = pd.DataFrame(
        {
            "S&P500 買い持ち": benchmark.summary(relative=False),
            "60/40（株6・債4）": balanced.summary(),
            "リスクパリティのみ": risk_parity_only.summary(),
            "トレンド×RP（レバなし）": unlevered.summary(),
            "本戦略（リスク調整レバ）": result.summary(),
        }
    ).T

    print("=== 主要指標 ===")
    print(summary[["CAGR %", "Vol %", "Sharpe", "MaxDD %", "Calmar", "年間回転率"]].round(2).to_string())

    starts = pf.start_date_table(result, START_YEARS)
    print("\n=== 開始年を変えても勝てるか ===")
    print(starts[["戦略 CAGR %", "S&P500 CAGR %", "差 %"]].round(2).to_string())

    crises = report_ja.crisis_table(result)
    decades = report_ja.decade_table(result)
    annual = result.annual_returns()

    summary.round(4).to_csv(args.outdir / "主要指標.csv", encoding="utf-8-sig")
    starts.round(3).to_csv(args.outdir / "開始年別.csv", encoding="utf-8-sig")
    crises.round(2).to_csv(args.outdir / "危機局面.csv", encoding="utf-8-sig")
    decades.round(2).to_csv(args.outdir / "年代別.csv", encoding="utf-8-sig")
    (annual * 100).round(2).to_csv(args.outdir / "年次リターン.csv", encoding="utf-8-sig")
    leverage.to_csv(args.outdir / "レバレッジ倍率.csv", encoding="utf-8-sig")
    result.weights.round(4).to_csv(args.outdir / "保有ウェイト.csv", encoding="utf-8-sig")

    report_ja.plot_overview(
        result,
        args.outdir / "総括グラフ.png",
        f"マルチアセット戦略 アウトオブサンプル {result.returns.index[0].year}-{result.returns.index[-1].year}",
    )
    report_ja.plot_allocation(result, args.outdir / "資産配分.png")
    report_ja.plot_annual(result, args.outdir / "年次超過.png")

    _write_report(args, data, result, benchmark, summary, starts, crises, decades, annual, leverage)
    print(f"\n{args.outdir}/レポート.md とグラフ3枚を出力しました")
    return 0


def _recent_note(annual: pd.DataFrame) -> str:
    """直近の成績が一過性の当たり年に依存していないかを明示する。"""
    recent = annual.loc[2010:2024]
    excess = (recent["戦略"] - recent["S&P500"]).mean() * 100
    last = annual.index[-1]
    contribution = (annual.loc[last, "戦略"] - annual.loc[last, "S&P500"]) * 100
    return (
        f"直近の 2010-2024年（{last}年を除く15年）だけを取ると、平均の年次超過は "
        f"**{excess:+.2f} ポイント**です。{last}年は金が急騰した当たり年で、"
        f"単年で **{contribution:+.1f} ポイント**の超過を稼いでいます。"
        "つまり「ここ15年は勝てていない」というのが正確な理解であり、"
        "直近の数字だけを見て有効性を判断してはいけません。"
    )


def _write_report(args, data, result, benchmark, summary, starts, crises, decades, annual, leverage) -> None:
    stats = result.summary()
    bench_stats = benchmark.summary(relative=False)
    text = f"""\
# マルチアセット戦略 検証レポート

対象期間 **{data.index[0].date()} 〜 {data.index[-1].date()}**（月次・配当再投資）。
コスト前提: 売買代金あたり片道 {args.cost_bps:.0f}bp、借入は Tビル + {args.borrow_bps:.0f}bp、
執行は1か月ラグ、総エクスポージャー上限 {args.max_gross:g} 倍。

レバレッジ倍率は毎年1月に、**その時点までの実績だけ**を使って
「S&P500の実現ボラティリティ ÷ 戦略の実現ボラティリティ」で決め直しています。
将来のリターンは一切参照していません。

## 主要指標（{result.returns.index[0].year}年〜{result.returns.index[-1].year}年）

| | 本戦略 | S&P500 | 差 |
|---|---|---|---|
| 年率リターン | **{stats['CAGR %']:.2f}%** | {bench_stats['CAGR %']:.2f}% | **{stats['CAGR %'] - bench_stats['CAGR %']:+.2f} pt** |
| ボラティリティ | **{stats['Vol %']:.2f}%** | {bench_stats['Vol %']:.2f}% | {stats['Vol %'] - bench_stats['Vol %']:+.2f} pt |
| シャープレシオ | **{stats['Sharpe']:.3f}** | {bench_stats['Sharpe']:.3f} | {stats['Sharpe'] - bench_stats['Sharpe']:+.3f} |
| 最大ドローダウン | **{stats['MaxDD %']:.1f}%** | {bench_stats['MaxDD %']:.1f}% | {stats['MaxDD %'] - bench_stats['MaxDD %']:+.1f} pt |
| カルマーレシオ | **{stats['Calmar']:.3f}** | {bench_stats['Calmar']:.3f} | {stats['Calmar'] - bench_stats['Calmar']:+.3f} |
| 年間回転率 | {stats['年間回転率']:.1f}倍 | {bench_stats['年間回転率']:.2f}倍 | — |
| 平均総エクスポージャー | {stats['平均総エクスポージャー']:.2f}倍 | 1.00倍 | — |

![総括グラフ](総括グラフ.png)

## 開始年を変えても勝てるか

単一資産版（`results/REPORT.md`）はこの表で失格になりました。
マルチアセット版は、試したすべての開始年で S&P500 を上回ります。

{report_ja.format_table(starts)}

### ただし、直近15年は例外

{_recent_note(annual)}

## 戦略の比較

{report_ja.format_table(summary)}

「リスクパリティのみ」と「トレンド×RP」を比べると、トレンドフィルターが
何をしているかが分かります。リターンはさほど変わらず、下落だけが削れます。

## 危機局面での挙動

分散とトレンドが効くのはここです。

{report_ja.format_table(crises)}

## 年代別

{report_ja.format_table(decades)}

![年次超過](年次超過.png)

## 資産配分の推移

![資産配分](資産配分.png)

## 再現方法

```bash
pip install -r requirements.txt
python scripts/run_multiasset.py --max-gross {args.max_gross:g}
```
"""
    (args.outdir / "レポート.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
