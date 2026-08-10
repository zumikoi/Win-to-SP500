"""Beat the S&P 500 - a trend + volatility-targeted strategy, honestly evaluated.

Quick start::

    from wsp import data, strategy, backtest, walkforward

    market = data.load()                       # daily total returns since 1928
    result = walkforward.walk_forward(market)  # parameters chosen out of sample
    print(result.summary())
"""

from . import backtest, data, metrics, report, signals, strategy, walkforward

__all__ = [
    "backtest",
    "data",
    "metrics",
    "report",
    "signals",
    "strategy",
    "walkforward",
]

__version__ = "1.0.0"
