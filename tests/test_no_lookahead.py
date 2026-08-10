"""The tests that matter: a backtest is only worth reading if it cannot cheat.

Every result in this repo rests on three claims - signals never see the future,
costs are actually charged, and the walk-forward never trains on its own test
data. Each is asserted here rather than trusted.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from wsp import data as wsp_data  # noqa: E402
from wsp import metrics, signals  # noqa: E402
from wsp.backtest import CostModel, buy_and_hold, run  # noqa: E402
from wsp.strategy import TrendVolStrategy, default_grid  # noqa: E402
from wsp.walkforward import walk_forward  # noqa: E402


@pytest.fixture(scope="module")
def market():
    return wsp_data.load(start="1970-01-01", end="2000-12-31")


def test_future_returns_cannot_change_todays_weight(market):
    """Corrupt the tail of the price history; earlier weights must not move."""
    strategy = TrendVolStrategy()
    baseline = strategy.weights(market)

    cutoff = len(market) // 2
    tampered_index = market.total_return.copy()
    tampered_index.iloc[cutoff:] *= 3.0
    tampered = wsp_data.MarketData(
        total_return=tampered_index,
        price=market.price,
        rf_index=market.rf_index,
        rf_rate=market.rf_rate,
    )

    altered = strategy.weights(tampered)
    pd.testing.assert_series_equal(
        baseline.iloc[:cutoff], altered.iloc[:cutoff], check_names=False
    )


def test_engine_applies_execution_lag(market):
    """A weight computed on day t may only earn day t+1's return."""
    weights = pd.Series(0.0, index=market.index)
    weights.iloc[10] = 1.0

    result = run(market, weights, CostModel(cost_per_turnover=0.0, borrow_spread=0.0))

    assert result.weights.iloc[10] == 0.0, "same-day fill would be lookahead"
    assert result.weights.iloc[11] == 1.0
    assert result.returns.iloc[11] == pytest.approx(market.returns.iloc[11])


def test_zero_lag_is_rejected():
    with pytest.raises(ValueError, match="lookahead"):
        CostModel(execution_lag=0)


def test_buy_and_hold_reproduces_the_index(market):
    """With no frictions, full exposure must equal the index itself."""
    result = buy_and_hold(market, CostModel(cost_per_turnover=0.0, borrow_spread=0.0))
    # Day 0 is unavoidably flat: the position is established with a one-day lag.
    np.testing.assert_allclose(
        result.returns.iloc[1:], market.returns.iloc[1:], rtol=1e-12
    )


def test_costs_reduce_returns_monotonically(market):
    weights = TrendVolStrategy().weights(market)
    previous = np.inf
    for bps in (0, 5, 10, 25, 50):
        result = run(market, weights, CostModel(cost_per_turnover=bps / 10_000))
        current = metrics.cagr(result.returns)
        assert current < previous
        previous = current


def test_leverage_is_charged_financing(market):
    """2x exposure must underperform 2x the index by roughly the borrow cost."""
    costs = CostModel(cost_per_turnover=0.0, borrow_spread=0.02, max_leverage=2.0)
    levered = run(market, pd.Series(2.0, index=market.index), costs)

    unfinanced = 2.0 * market.returns - market.rf_returns
    drag = (unfinanced - levered.returns).iloc[2:].mean() * 252
    assert drag == pytest.approx(0.02, abs=1e-6)


def test_max_leverage_is_enforced(market):
    costs = CostModel(max_leverage=1.5)
    result = run(market, pd.Series(10.0, index=market.index), costs)
    assert result.weights.max() <= 1.5


def test_no_trade_band_reduces_turnover(market):
    raw = signals.volatility_scalar(market.returns, 0.15, 60, 2.0).fillna(0.0)
    banded = signals.apply_no_trade_band(raw, 0.15)

    assert banded.diff().abs().sum() < raw.diff().abs().sum()
    # Every held weight is one the target actually took, never an invention.
    assert set(np.round(banded.unique(), 10)).issubset(
        set(np.round(raw.unique(), 10)) | {0.0}
    )


def test_trend_ensemble_is_bounded_and_warms_up(market):
    trend = signals.trend_ensemble(market.total_return, (50, 200))
    assert trend.dropna().between(0.0, 1.0).all()
    assert trend.iloc[:200].isna().all(), "must not signal before the window fills"


def test_walk_forward_never_trains_on_its_test_year(market):
    """Rerunning with the test period's data destroyed must not change the past.

    If any out-of-sample year were selected using its own returns, truncating the
    sample there would change the earlier decisions. It must not.
    """
    grid = default_grid()[:6]
    full = walk_forward(market, grid=grid, min_train_years=10)

    truncated_market = market.slice(None, "1995-12-31")
    truncated = walk_forward(truncated_market, grid=grid, min_train_years=10)

    overlap = truncated.returns.index
    pd.testing.assert_series_equal(
        full.returns.loc[overlap], truncated.returns, check_names=False
    )


def test_walk_forward_selection_log_is_causal(market):
    result = walk_forward(market, grid=default_grid()[:6], min_train_years=10)
    years = result.selections.index
    assert years.min() >= market.index[0].year + 10
    assert result.returns.index[0].year == years.min()


def test_metrics_agree_with_hand_computation():
    returns = pd.Series(
        [0.01, -0.02, 0.015, 0.0, 0.005] * 60,
        index=pd.bdate_range("2000-01-03", periods=300),
    )
    curve = metrics.equity_curve(returns)
    assert curve.iloc[-1] == pytest.approx(float((1 + returns).prod()))

    expected = float((1 + returns).prod()) ** (252 / len(returns)) - 1
    assert metrics.cagr(returns) == pytest.approx(expected)
    assert metrics.max_drawdown(returns) <= 0.0
    assert metrics.sharpe(returns) == pytest.approx(
        returns.mean() / returns.std(ddof=1) * np.sqrt(252)
    )


def test_alpha_beta_recovers_a_known_relationship():
    rng = np.random.default_rng(0)
    index = pd.bdate_range("2000-01-03", periods=4000)
    bench = pd.Series(rng.normal(0.0003, 0.01, len(index)), index=index)
    strat = 0.5 * bench + 0.0002

    alpha, beta = metrics.alpha_beta(strat, bench)
    assert beta == pytest.approx(0.5, abs=0.02)
    assert alpha == pytest.approx(0.0002 * 252, abs=0.01)


def test_data_validation_rejects_corrupt_series(market):
    broken = wsp_data.MarketData(
        total_return=market.total_return.iloc[::-1],
        price=market.price,
        rf_index=market.rf_index,
        rf_rate=market.rf_rate,
    )
    with pytest.raises(ValueError):
        wsp_data.validate(broken)


def test_bundled_dataset_is_sane():
    full = wsp_data.load(start=None)
    assert len(full) > 30_000
    assert full.index[0].year <= 1900
    # Long-run equity total return should land in a plausible band.
    assert 0.06 < metrics.cagr(full.returns) < 0.13
