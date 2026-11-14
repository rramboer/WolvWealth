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
from wolvwealth.backtest.results import BacktestResult
from wolvwealth.backtest.strategies import BuyAndHoldStrategy, EqualWeightStrategy, MaxSharpeStrategy

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
) -> BacktestReport:
    """Backtest the max-Sharpe optimizer plus equal-weight and buy-and-hold benchmarks."""
    strategy = MaxSharpeStrategy(config.constraints, risk_free_rate=config.risk_free_rate)
    result = run_backtest(prices, config, strategy)

    benchmarks: dict[str, BacktestResult] = {}
    warnings = [SURVIVORSHIP_WARNING]
    benchmarks[EQUAL_WEIGHT_BENCHMARK] = run_backtest(prices, config, EqualWeightStrategy())
    for ticker in benchmark_tickers:
        if ticker not in prices.columns:
            warnings.append(f"Benchmark {ticker} skipped: no price data.")
            continue
        hold_config = dataclasses.replace(config, universe=(ticker,), constraints=type(config.constraints)())
        benchmarks[ticker] = run_backtest(prices, hold_config, BuyAndHoldStrategy({ticker: 1.0}))

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
