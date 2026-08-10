"""Market data loading.

The bundled dataset is a daily US large-cap equity total-return series with a
matching risk-free series, so every backtest in this repo runs offline and
reproducibly. See ``data/SOURCES.md`` for provenance and known caveats.
"""

from __future__ import annotations

import dataclasses
import pathlib

import numpy as np
import pandas as pd

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA_FILE = REPO_ROOT / "data" / "us_market_daily.csv.gz"

UPSTREAM_URL = (
    "https://raw.githubusercontent.com/SteelCerberus/us-market-data/main/"
    "data/us_market_data.csv"
)

#: The S&P 500 proper starts in 1928; earlier data is a Dow composite proxy.
SP500_START = "1928-01-01"

TRADING_DAYS = 252


@dataclasses.dataclass(frozen=True)
class MarketData:
    """Aligned daily series used by every strategy and backtest."""

    total_return: pd.Series  # equity total-return index (dividends reinvested)
    price: pd.Series  # price-only index, for reference/plots
    rf_index: pd.Series  # risk-free total-return index
    rf_rate: pd.Series  # annualised risk-free rate, percent

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.total_return.index

    @property
    def returns(self) -> pd.Series:
        """Daily simple total return of the equity index."""
        return self.total_return.pct_change().fillna(0.0)

    @property
    def rf_returns(self) -> pd.Series:
        """Daily simple return earned on cash."""
        return self.rf_index.pct_change().fillna(0.0)

    def slice(self, start: str | None = None, end: str | None = None) -> "MarketData":
        sl = slice(start, end)
        return MarketData(
            total_return=self.total_return.loc[sl],
            price=self.price.loc[sl],
            rf_index=self.rf_index.loc[sl],
            rf_rate=self.rf_rate.loc[sl],
        )

    def __len__(self) -> int:
        return len(self.total_return)


def load(
    path: pathlib.Path | str | None = None,
    start: str | None = SP500_START,
    end: str | None = None,
) -> MarketData:
    """Load the bundled daily dataset.

    ``start`` defaults to 1928 because that is when the underlying index becomes
    the S&P 500 rather than a Dow composite. Pass ``start=None`` for the full
    series back to 1885.
    """
    path = pathlib.Path(path) if path is not None else DATA_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `python scripts/fetch_data.py` to download it."
        )

    frame = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]

    data = MarketData(
        total_return=frame["total_return"].astype(float),
        price=frame["close"].astype(float),
        rf_index=frame["rf_index"].astype(float),
        rf_rate=frame["rf_rate"].astype(float),
    )
    validate(data)
    return data.slice(start, end)


def validate(data: MarketData) -> None:
    """Fail loudly on the data problems that would silently corrupt a backtest."""
    if not data.index.is_monotonic_increasing:
        raise ValueError("dates are not sorted ascending")
    if data.index.has_duplicates:
        raise ValueError("duplicate dates in the index")

    for name in ("total_return", "price", "rf_index"):
        series = getattr(data, name)
        if series.isna().any():
            raise ValueError(f"{name} contains NaNs")
        if (series <= 0).any():
            raise ValueError(f"{name} contains non-positive values")

    daily = data.returns
    extreme = daily.abs() > 0.5
    if extreme.any():
        dates = ", ".join(str(d.date()) for d in daily.index[extreme][:5])
        raise ValueError(f"implausible daily moves (>50%) on: {dates}")


def fetch(destination: pathlib.Path | str | None = None) -> pathlib.Path:
    """Re-download the upstream dataset and rewrite the bundled cache.

    Only needed to extend the history; the committed file already covers the
    full sample used by the reports.
    """
    destination = pathlib.Path(destination) if destination else DATA_FILE
    destination.parent.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(UPSTREAM_URL, parse_dates=["Date"])
    columns = {
        "Date": "date",
        "Close": "close",
        "Adjusted Close": "total_return",
        "Risk Free Rate": "rf_rate",
        "Risk Free Return": "rf_index",
        "CPI": "cpi",
    }
    trimmed = raw[list(columns)].rename(columns=columns)
    trimmed = trimmed.round(
        {"close": 6, "total_return": 6, "rf_rate": 4, "rf_index": 6, "cpi": 4}
    )
    trimmed.to_csv(destination, index=False, compression="gzip")
    return destination


def annualised_rf(data: MarketData) -> float:
    """Geometric mean annual risk-free return over the sample."""
    years = len(data) / TRADING_DAYS
    growth = data.rf_index.iloc[-1] / data.rf_index.iloc[0]
    return float(growth ** (1.0 / years) - 1.0) if years > 0 else np.nan
