"""マルチアセット戦略の検証テスト。

単一資産版と同じく、「未来を見ていないこと」と「コストを本当に引いていること」を
信じるのではなく assert する。
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from wsp import metrics, multiasset  # noqa: E402
from wsp import portfolio as pf  # noqa: E402
from wsp.multiasset import MONTHS  # noqa: E402


@pytest.fixture(scope="module")
def data():
    return multiasset.load()


def test_bond_pricing_at_par():
    """利回りと同じクーポンなら、価格はほぼ額面になる。"""
    assert multiasset.bond_price(0.05, 10.0, 0.05) == pytest.approx(1.0, abs=1e-9)
    assert multiasset.bond_price(0.03, 10.0, 0.03) == pytest.approx(1.0, abs=1e-9)


def test_bond_price_falls_when_yields_rise():
    high = multiasset.bond_price(0.06, 10.0, 0.05)
    low = multiasset.bond_price(0.04, 10.0, 0.05)
    assert high < 1.0 < low


def test_bond_return_with_flat_yields_is_the_coupon():
    """利回りが動かない月のリターンは、およそ クーポン÷12。

    経過利息を二重計上していると、この値は倍近くになる。
    """
    yields = pd.Series(
        [5.0] * 6, index=pd.date_range("2000-01-31", periods=6, freq="ME")
    )
    returns = multiasset.bond_total_return(yields).dropna()
    assert returns.values == pytest.approx(0.05 / 12, rel=0.02)


def test_bond_series_is_plausible(data):
    """10年国債の長期リターンとボラティリティが現実的な範囲にあること。"""
    bond = data.returns["bond"]
    assert 0.03 < metrics.cagr(bond, MONTHS) < 0.08
    assert 0.03 < metrics.volatility(bond, MONTHS) < 0.10


def test_future_prices_cannot_change_todays_weight(data):
    """将来のリターンを改竄しても、それ以前のウェイトは1つも動かないこと。"""
    strategy = pf.MultiAssetStrategy()
    baseline = strategy.weights(data)

    cutoff = len(data) // 2
    tampered_returns = data.returns.copy()
    tampered_returns.iloc[cutoff:] += 0.05
    tampered = multiasset.MultiAssetData(
        returns=tampered_returns, cash=data.cash, equity_index=data.equity_index
    )

    pd.testing.assert_frame_equal(
        baseline.iloc[:cutoff], strategy.weights(tampered).iloc[:cutoff]
    )


def test_execution_lag_is_applied(data):
    """月末に決めたウェイトは、翌月のリターンしか受け取れない。"""
    weights = pd.DataFrame(0.0, index=data.index, columns=data.assets)
    weights.iloc[10, weights.columns.get_loc("equity")] = 1.0

    result = pf.run(data, weights, pf.PortfolioCosts(cost_per_turnover=0.0, borrow_spread=0.0))
    assert result.weights.iloc[10].sum() == 0.0
    assert result.weights.iloc[11]["equity"] == 1.0
    assert result.returns.iloc[11] == pytest.approx(data.returns["equity"].iloc[11])


def test_walk_forward_never_uses_its_test_year(data):
    """サンプルを途中で切っても、それ以前の判断は変わらないこと。"""
    strategy = pf.MultiAssetStrategy()
    full, _ = pf.walk_forward(data, strategy, min_train_years=12)
    truncated, _ = pf.walk_forward(
        data.slice(None, "2000-12-31"), strategy, min_train_years=12
    )
    overlap = truncated.returns.index
    pd.testing.assert_series_equal(
        full.returns.loc[overlap], truncated.returns, check_names=False
    )


def test_gross_exposure_cap_is_enforced(data):
    weights = pd.DataFrame(5.0, index=data.index, columns=data.assets)
    result = pf.run(data, weights, pf.PortfolioCosts(max_gross=2.0))
    assert result.gross.max() <= 2.0 + 1e-9


def test_cap_preserves_relative_asset_mix(data):
    """上限で縮めるとき、資産間の比率は変えないこと。"""
    weights = pd.DataFrame(
        {"equity": 2.0, "bond": 4.0, "gold": 2.0}, index=data.index
    )
    result = pf.run(data, weights, pf.PortfolioCosts(max_gross=2.0))
    held = result.weights.iloc[5]
    assert held["bond"] / held["equity"] == pytest.approx(2.0)


def test_risk_parity_weights_sum_to_one(data):
    weights = pf.MultiAssetStrategy().risk_parity(data).dropna()
    assert weights.sum(axis=1).values == pytest.approx(1.0)
    # 低ボラ資産（債券）は高ボラ資産（株）より重くなる。
    assert weights["bond"].mean() > weights["equity"].mean()


def test_trend_is_bounded_and_warms_up(data):
    strategy = pf.MultiAssetStrategy(trend_windows=(6, 10, 12))
    trend = strategy.trend(data)
    assert trend.dropna().values.min() >= 0.0
    assert trend.dropna().values.max() <= 1.0
    assert trend.iloc[:12].isna().all().all()


def test_costs_and_financing_reduce_returns(data):
    weights = pf.MultiAssetStrategy(leverage=2.0).weights(data)
    previous = np.inf
    for bps in (0, 10, 30, 60):
        result = pf.run(data, weights, pf.PortfolioCosts(cost_per_turnover=bps / 10_000))
        current = metrics.cagr(result.returns, MONTHS)
        assert current < previous
        previous = current


def test_borrow_spread_is_charged(data):
    """総エクスポージャー2倍なら、借入1倍分の金利が引かれる。"""
    weights = pd.DataFrame(
        {"equity": 2.0, "bond": 0.0, "gold": 0.0}, index=data.index
    )
    costs = pf.PortfolioCosts(cost_per_turnover=0.0, borrow_spread=0.024, max_gross=2.0)
    result = pf.run(data, weights, costs)

    unfinanced = 2.0 * data.returns["equity"] - data.cash
    drag = (unfinanced - result.returns).iloc[2:].mean() * MONTHS
    assert drag == pytest.approx(0.024, abs=1e-6)


def test_buy_and_hold_matches_the_index(data):
    result = pf.buy_and_hold_equity(
        data, pf.PortfolioCosts(cost_per_turnover=0.0, borrow_spread=0.0)
    )
    np.testing.assert_allclose(
        result.returns.iloc[1:], data.returns["equity"].iloc[1:], rtol=1e-12
    )


def test_diversification_actually_lowers_volatility(data):
    """分散の前提そのものを検証する。相関が高ければこの戦略は成立しない。"""
    correlations = data.returns.corr()
    off_diagonal = correlations.values[np.triu_indices(3, k=1)]
    assert (np.abs(off_diagonal) < 0.4).all(), "資産間の相関が高すぎる"

    equal_weight = data.returns.mean(axis=1)
    assert metrics.volatility(equal_weight, MONTHS) < metrics.volatility(
        data.returns["equity"], MONTHS
    )


def test_bundled_multiasset_dataset_is_sane(data):
    assert len(data) > 800
    assert data.index[0].year <= 1954
    assert 0.08 < metrics.cagr(data.returns["equity"], MONTHS) < 0.14
