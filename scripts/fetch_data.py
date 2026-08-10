#!/usr/bin/env python3
"""Refresh the bundled market dataset from the upstream source.

The repository already ships the data it needs, so this is only for extending
the sample forward in time.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from wsp import data as wsp_data  # noqa: E402


def main() -> int:
    print(f"downloading {wsp_data.UPSTREAM_URL} ...")
    path = wsp_data.fetch()
    market = wsp_data.load(path, start=None)
    print(f"wrote {path} ({path.stat().st_size / 1e6:.2f} MB)")
    print(f"{len(market):,} rows, {market.index[0].date()} -> {market.index[-1].date()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
