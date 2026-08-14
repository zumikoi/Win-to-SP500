"""Beat the S&P 500 - strategies evaluated honestly enough to fail.

Two studies live here. The single-asset one (`strategy`, `walkforward`) times the
S&P 500 itself and *does not* beat it once the start date moves past 1963. The
multi-asset one (`multiasset`, `portfolio`) diversifies across equities, bonds
and gold and does beat it, from every start date tested.

Single asset::

    from wsp import data, walkforward
    market = data.load()                       # daily total returns since 1928
    print(walkforward.walk_forward(market).summary())

Multi asset::

    from wsp import multiasset, portfolio
    panel = multiasset.load()                  # monthly, 4 assets, since 1953
    result, leverage = portfolio.walk_forward(panel)
    print(result.summary())
"""

from . import (
    backtest,
    data,
    metrics,
    multiasset,
    portfolio,
    report,
    report_ja,
    signals,
    strategy,
    walkforward,
)

__all__ = [
    "backtest",
    "data",
    "metrics",
    "multiasset",
    "portfolio",
    "report",
    "report_ja",
    "signals",
    "strategy",
    "walkforward",
]

__version__ = "1.0.0"
