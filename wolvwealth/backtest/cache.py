"""In-memory LRU cache for backtest reports.

A report is a pure function of (config, benchmarks, price data), so repeated
requests, which the web UI will make whenever a user tweaks and reverts a
setting, can be answered without re-running the optimizer.
"""

import collections
import threading
from collections.abc import Callable

import pandas as pd

from wolvwealth.backtest.config import BacktestConfig
from wolvwealth.backtest.service import BacktestReport


class ReportCache:
    """Thread-safe least-recently-used cache keyed by config and price-data fingerprint."""

    def __init__(self, maxsize: int = 64) -> None:
        """Keep at most `maxsize` reports."""
        self.maxsize = maxsize
        self._entries: collections.OrderedDict[tuple, BacktestReport] = collections.OrderedDict()
        self._lock = threading.Lock()

    def get_or_compute(
        self,
        prices: pd.DataFrame,
        config: BacktestConfig,
        benchmark_tickers: tuple[str, ...],
        compute: Callable[[], BacktestReport],
    ) -> tuple[BacktestReport, bool]:
        """Return (report, was_cached)."""
        key = (config, benchmark_tickers, _fingerprint(prices))
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return self._entries[key], True
        report = compute()  # Outside the lock: a slow run must not block cache hits.
        with self._lock:
            self._entries[key] = report
            self._entries.move_to_end(key)
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)
        return report, False

    def clear(self) -> None:
        """Drop every cached report."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        """Number of cached reports."""
        return len(self._entries)


def _fingerprint(prices: pd.DataFrame) -> tuple:
    """Cheap identity for a price table: shape plus first and last date."""
    return (prices.shape, prices.index[0], prices.index[-1])
