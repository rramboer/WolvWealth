"""WolvWealth backtesting.

Layers (inner to outer):

* ``config`` / ``results`` / ``metrics``  - plain data types and pure math.
* ``schedule`` / ``strategies`` / ``engine`` - the simulation itself. The
  engine depends only on the ``Strategy`` protocol; the optimizer is one
  strategy, the benchmarks are others.
* ``preflight`` - pre-run checks (date range, rebalance limit, "could anything
  trade?") using the engine's own decision iterator, without the optimizer.
* ``progress`` - the Progress value reported while a backtest runs.
* ``service`` - runs the optimizer with its benchmarks and produces a report.
* ``request`` / ``cache`` / ``jobs`` - validate untrusted payloads against
  work limits, memoize reports, and run them on a background pool with
  pollable ``progress``. Used by the JSON API (``wolvwealth.api.backtest``).
* ``cli`` - command-line transport (``wolvwealth-backtest``).

Nothing in this package imports Flask or the application singleton.
"""

from wolvwealth.backtest.config import BacktestConfig, Frequency
from wolvwealth.backtest.engine import run_backtest
from wolvwealth.backtest.metrics import PerformanceMetrics, compute_metrics
from wolvwealth.backtest.results import BacktestResult, RebalanceEvent
from wolvwealth.backtest.service import BacktestReport, run_report
from wolvwealth.backtest.strategies import (
    BuyAndHoldStrategy,
    EqualWeightStrategy,
    MaxSharpeStrategy,
    OptimizerStrategy,
    Strategy,
    StrategyError,
)

__all__ = [
    "BacktestConfig",
    "BacktestReport",
    "BacktestResult",
    "BuyAndHoldStrategy",
    "EqualWeightStrategy",
    "Frequency",
    "MaxSharpeStrategy",
    "OptimizerStrategy",
    "PerformanceMetrics",
    "RebalanceEvent",
    "Strategy",
    "StrategyError",
    "compute_metrics",
    "run_backtest",
    "run_report",
]
