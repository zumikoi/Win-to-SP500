"""The strategy: a trend filter multiplied by a volatility target.

Two effects do the work, and both are properties of risk rather than forecasts
of return - which is why they survive out of sample:

1. **Trend.** Equity drawdowns are not i.i.d. draws; they cluster into long
   declines. Standing aside while the index is below its own moving averages
   removes the left tail that compounding never recovers from.
2. **Volatility targeting.** Realised volatility is highly persistent, expected
   return is not. Holding *constant risk* rather than constant capital means
   taking less exposure exactly when exposure is least well compensated.

Neither leg tries to predict returns. Together they roughly double the Sharpe
ratio of buy-and-hold, and leverage is what converts that extra Sharpe into the
extra *return* the benchmark is judged on.
"""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Iterator, Sequence

import pandas as pd

from . import signals
from .data import MarketData

#: Named moving-average families searched by the walk-forward.
TREND_FAMILIES: dict[str, tuple[int, ...]] = {
    "fast": (50, 100, 150),
    "mid": (100, 150, 200, 250, 300),
    "slow": (200, 250, 300),
}


@dataclasses.dataclass(frozen=True)
class TrendVolStrategy:
    """Target weight = trend agreement x volatility scalar x leverage."""

    trend_family: str = "mid"
    target_vol: float = 0.15
    max_exposure: float = 2.0
    vol_window: int = 60
    band: float = 0.15
    leverage: float = 1.0

    def __post_init__(self) -> None:
        if self.trend_family not in TREND_FAMILIES:
            raise ValueError(
                f"unknown trend_family {self.trend_family!r}; "
                f"expected one of {sorted(TREND_FAMILIES)}"
            )
        if self.target_vol <= 0 or self.max_exposure <= 0 or self.vol_window < 2:
            raise ValueError("target_vol, max_exposure and vol_window must be positive")

    @property
    def trend_windows(self) -> tuple[int, ...]:
        return TREND_FAMILIES[self.trend_family]

    @property
    def name(self) -> str:
        return (
            f"{self.trend_family}/vt{self.target_vol:.2f}"
            f"/cap{self.max_exposure:g}/vw{self.vol_window}/band{self.band:g}"
        )

    def raw_weights(self, data: MarketData) -> pd.Series:
        """Desired exposure before the no-trade band is applied.

        Kept separate because the band is path-dependent: banding a weight and
        *then* scaling it multiplies every step size, and therefore the
        turnover, by the scale factor. Anything that rescales or blends
        exposures must do so here and band the result once, at the scale it
        will actually be traded.
        """
        trend = signals.trend_ensemble(data.total_return, self.trend_windows)
        scalar = signals.volatility_scalar(
            data.returns, self.target_vol, self.vol_window, self.max_exposure
        )
        return (trend * scalar * self.leverage).rename(self.name)

    def weights(self, data: MarketData) -> pd.Series:
        """Target weight for each date, using only data up to that date.

        The engine applies the execution lag, so this series is deliberately
        *not* shifted here.
        """
        raw = self.raw_weights(data).clip(upper=self.max_exposure)
        return signals.apply_no_trade_band(raw, self.band).rename(self.name)

    def with_leverage(self, leverage: float) -> "TrendVolStrategy":
        """Copy of this strategy scaled to a different risk level."""
        return dataclasses.replace(
            self,
            leverage=leverage,
            max_exposure=self.max_exposure * max(leverage, 1.0),
        )


def default_grid() -> list[TrendVolStrategy]:
    """Configurations the walk-forward chooses between.

    Deliberately coarse. A dense grid would not make the strategy better, it
    would only make the selection-bias correction harsher.
    """
    return list(
        build_grid(
            families=tuple(TREND_FAMILIES),
            target_vols=(0.12, 0.15, 0.17, 0.20),
            max_exposures=(1.5, 2.0, 2.5),
            vol_windows=(40, 60, 120),
            bands=(0.10, 0.20),
        )
    )


def build_grid(
    families: Sequence[str],
    target_vols: Sequence[float],
    max_exposures: Sequence[float],
    vol_windows: Sequence[int],
    bands: Sequence[float],
) -> Iterator[TrendVolStrategy]:
    for family, vol, cap, window, band in itertools.product(
        families, target_vols, max_exposures, vol_windows, bands
    ):
        yield TrendVolStrategy(
            trend_family=family,
            target_vol=vol,
            max_exposure=cap,
            vol_window=window,
            band=band,
        )
