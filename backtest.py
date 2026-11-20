#!/usr/bin/env python3
"""Backtest the WolvWealth portfolio optimizer over historical data.

Thin wrapper kept for backwards compatibility; the implementation lives in
the wolvwealth.backtest package and is also installed as `wolvwealth-backtest`.

    python backtest.py --help
"""

import sys

from wolvwealth.backtest.cli import main

if __name__ == "__main__":
    sys.exit(main())
