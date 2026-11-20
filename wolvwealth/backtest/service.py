"""Application layer: run the optimizer and its benchmarks under one config.

This is the single entry point the CLI (and later the JSON API) call. It knows
nothing about transports; it takes a price table and a config and returns a
report that serializes to plain dicts.
"""

import dataclasses

import pandas as pd

from wolvwealth.backtest.config import BacktestConfig
from wolvwealth.backtest.engine import run_backtest
from wolvwealth.backtest.metrics import PerformanceMetrics, compute_metrics
from wolvwealth.backtest.progress import Progress, ProgressCallback
from wolvwealth.backtest.results import BacktestResult
from wolvwealth.backtest.strategies import BuyAndHoldStrategy, EqualWeightStrategy, OptimizerStrategy

EQUAL_WEIGHT_BENCHMARK = "equal_weight"
DEFAULT_BENCHMARK_TICKERS = ("SPY",)

SURVIVORSHIP_WARNING = (
    "Survivorship bias: the ticker universe reflects today's listings and market-cap ranking, "
    "so historical results are optimistic. Compare against the equal-weight benchmark, which "
    "draws from the same universe, rather than taking absolute returns at face value."
)


@dataclasses.dataclass(frozen=True)
class BacktestReport:
    """Optimizer result alongside its benchmarks, each with realized metrics."""

    config: BacktestConfig
    result: BacktestResult
    metrics: PerformanceMetrics
    benchmarks: dict[str, BacktestResult]
    benchmark_metrics: dict[str, PerformanceMetrics]
    warnings: list[str]

    def to_dict(self, include_equity: bool = True) -> dict:
        """JSON-friendly representation."""
        return {
            "config": self.config.to_dict(),
            "result": self.result.to_dict(include_equity),
            "metrics": self.metrics.to_dict(),
            "benchmarks": {
                name: {"result": result.to_dict(include_equity), "metrics": self.benchmark_metrics[name].to_dict()}
                for name, result in self.benchmarks.items()
            },
            "warnings": list(self.warnings),
        }


def run_report(
    prices: pd.DataFrame,
    config: BacktestConfig,
    benchmark_tickers: tuple[str, ...] = DEFAULT_BENCHMARK_TICKERS,
    on_progress: ProgressCallback | None = None,
) -> BacktestReport:
    """Backtest the optimizer (config.objective) plus equal-weight and buy-and-hold benchmarks.

    `on_progress` receives a Progress before each optimizer rebalance and
    after each benchmark run.
    """
    report_progress = on_progress or (lambda _progress: None)
    benchmark_runs = 1 + len(benchmark_tickers)  # equal weight + buy-and-hold tickers
    optimizer_steps = 0

    def on_optimizer_step(completed: int, total: int) -> None:
        nonlocal optimizer_steps
        optimizer_steps = total
        message = f"Optimizing rebalance {min(completed + 1, total)} of {total}"
        report_progress(Progress("optimizer", completed, total + benchmark_runs, message))

    strategy = OptimizerStrategy(config.constraints, risk_free_rate=config.risk_free_rate, objective=config.objective)
    result = run_backtest(prices, config, strategy, on_step=on_optimizer_step)

    def benchmark_done(index: int, name: str) -> None:
        total = optimizer_steps + benchmark_runs
        report_progress(Progress("benchmarks", optimizer_steps + index, total, f"Benchmark {name} done"))

    benchmarks: dict[str, BacktestResult] = {}
    warnings = [SURVIVORSHIP_WARNING]
    benchmarks[EQUAL_WEIGHT_BENCHMARK] = run_backtest(prices, config, EqualWeightStrategy())
    benchmark_done(1, EQUAL_WEIGHT_BENCHMARK)
    for index, ticker in enumerate(benchmark_tickers, start=2):
        if ticker not in prices.columns:
            warnings.append(f"Benchmark {ticker} skipped: no price data.")
        else:
            hold_config = dataclasses.replace(config, universe=(ticker,), constraints=type(config.constraints)())
            benchmarks[ticker] = run_backtest(prices, hold_config, BuyAndHoldStrategy({ticker: 1.0}))
        benchmark_done(index, ticker)

    return BacktestReport(
        config=config,
        result=result,
        metrics=compute_metrics(result.equity, result.rebalances, config.risk_free_rate),
        benchmarks=benchmarks,
        benchmark_metrics={
            name: compute_metrics(bench.equity, bench.rebalances, config.risk_free_rate)
            for name, bench in benchmarks.items()
        },
        warnings=warnings,
    )
