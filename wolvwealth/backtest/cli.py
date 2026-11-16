"""Command-line transport for the backtester.

wolvwealth-backtest --start 2019-01-01 --frequency monthly --universe top50
wolvwealth-backtest --start 2015-01-01 --universe AAPL MSFT SPY --json > out.json
"""

import argparse
import datetime
import json
import sys

import pandas as pd

from wolvwealth.backtest.config import BacktestConfig, Frequency
from wolvwealth.backtest.metrics import PerformanceMetrics
from wolvwealth.backtest.preflight import check_runnable
from wolvwealth.backtest.progress import Progress
from wolvwealth.backtest.service import BacktestReport, run_report
from wolvwealth.market_data import load_historical_prices, load_ticker_universe
from wolvwealth.optimizer_params import Constraints, InvalidOptimizationInput, resolve_universe


def build_parser() -> argparse.ArgumentParser:
    """CLI options."""
    parser = argparse.ArgumentParser(
        prog="wolvwealth-backtest",
        description="Backtest the WolvWealth max-Sharpe optimizer against equal-weight and buy-and-hold benchmarks.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--start", type=datetime.date.fromisoformat, default="2019-01-01", help="YYYY-MM-DD")
    parser.add_argument(
        "--end", type=datetime.date.fromisoformat, default=None, help="YYYY-MM-DD (default: last price date)"
    )
    parser.add_argument("--cash", type=float, default=100_000, help="initial cash")
    parser.add_argument(
        "--frequency", choices=[f.value for f in Frequency], default=Frequency.MONTHLY.value, help="rebalance step"
    )
    parser.add_argument("--universe", nargs="+", default=["top50"], help='tickers and/or "topN" aliases')
    parser.add_argument("--lookback-days", type=int, default=3 * 365, help="price history fed to the optimizer")
    parser.add_argument("--execution-lag", type=int, default=1, help="trading days between decision and trade")
    parser.add_argument("--cost-bps", type=float, default=0.0, help="transaction cost in basis points of traded value")
    parser.add_argument("--max-positions", type=int, default=10, help="keep only the N largest positions (-1: all)")
    parser.add_argument("--max-weight", type=float, default=1.0, help="maximum weight per asset")
    parser.add_argument("--min-weight", type=float, default=0.0, help="minimum weight for every asset")
    parser.add_argument("--risk-free-rate", type=float, default=0.02, help="annual rate for Sharpe ratios")
    parser.add_argument("--benchmark", nargs="*", default=["SPY"], help="buy-and-hold benchmark tickers")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("--no-equity", action="store_true", help="omit the daily equity curves from --json output")
    parser.add_argument("--quiet", action="store_true", help="no progress bar on stderr")
    return parser


def config_from_args(args: argparse.Namespace, prices: pd.DataFrame, ticker_universe: list[str]) -> BacktestConfig:
    """Translate parsed arguments into a validated BacktestConfig."""
    universe = resolve_universe(args.universe, ticker_universe)
    constraints = Constraints(
        max_weight=args.max_weight, min_universal_weight=args.min_weight, max_positions=args.max_positions
    )
    return BacktestConfig(
        start=args.start,
        end=args.end or prices.index[-1].date(),
        universe=tuple(universe),
        initial_cash=args.cash,
        frequency=Frequency(args.frequency),
        lookback_days=args.lookback_days,
        execution_lag=args.execution_lag,
        transaction_cost_bps=args.cost_bps,
        constraints=constraints,
        risk_free_rate=args.risk_free_rate,
    )


def format_report(report: BacktestReport) -> str:
    """Human-readable summary."""
    lines = [
        f"Backtest {report.config.start} -> {report.config.end}, {report.config.frequency} rebalancing, "
        f"{len(report.config.universe)} tickers, {report.config.lookback_days}-day lookback",
        "",
        "Rebalances:",
    ]
    previous = report.result.start_value
    for event in report.result.rebalances:
        change = (event.portfolio_value / previous - 1) * 100
        holdings = ", ".join(
            f"{asset} {weight:.0%}" for asset, weight in sorted(event.weights.items(), key=lambda i: -i[1])
        )
        lines.append(
            f"  {event.execution_date.date()}  ${event.portfolio_value:>12,.2f}  {change:>+7.2f}%  "
            f"turnover {event.turnover:.0%}  [{holdings}]"
        )
        previous = event.portfolio_value
    lines.append(f"  {report.result.equity.index[-1].date()}  ${report.result.end_value:>12,.2f}  (final)")
    lines += ["", _metrics_table({"optimizer": report.metrics, **report.benchmark_metrics})]
    warnings = report.warnings + report.result.warnings
    if warnings:
        lines += ["", "Warnings:", *(f"  - {warning}" for warning in warnings)]
    return "\n".join(lines)


def _metrics_table(columns: dict[str, PerformanceMetrics]) -> str:
    rows = [
        ("Total return", "total_return", "{:+.2%}"),
        ("CAGR", "cagr", "{:+.2%}"),
        ("Annual volatility", "annual_volatility", "{:.2%}"),
        ("Sharpe ratio", "sharpe_ratio", "{:.2f}"),
        ("Max drawdown", "max_drawdown", "{:.2%}"),
        ("Calmar ratio", "calmar_ratio", "{:.2f}"),
        ("Rebalances", "rebalances", "{:d}"),
        ("Avg turnover", "average_turnover", "{:.0%}"),
        ("Transaction costs", "total_transaction_costs", "${:,.2f}"),
        ("End value", "end_value", "${:,.2f}"),
    ]
    width = 16
    header = f"{'':<20}" + "".join(f"{name:>{width}}" for name in columns)
    lines = [header, "-" * len(header)]
    for label, field, fmt in rows:
        cells = []
        for metrics in columns.values():
            value = getattr(metrics, field)
            cells.append(f"{fmt.format(value):>{width}}" if value == value else f"{'n/a':>{width}}")
        lines.append(f"{label:<20}" + "".join(cells))
    return "\n".join(lines)


class TerminalProgressBar:
    """Redraws a one-line progress bar on stderr."""

    WIDTH = 30

    def __init__(self, stream=sys.stderr) -> None:
        """Draw to `stream`."""
        self.stream = stream

    def __call__(self, progress: Progress) -> None:
        """Redraw for the latest progress."""
        filled = int(self.WIDTH * progress.fraction)
        bar = "#" * filled + "-" * (self.WIDTH - filled)
        self.stream.write(f"\r[{bar}] {progress.fraction:4.0%}  {progress.message:<40}")
        self.stream.flush()

    def close(self) -> None:
        """Erase the bar so the report starts on a clean line."""
        self.stream.write("\r" + " " * (self.WIDTH + 50) + "\r")
        self.stream.flush()


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    prices = load_historical_prices()
    try:
        config = config_from_args(args, prices, load_ticker_universe())
        check_runnable(prices, config)
        progress_bar = TerminalProgressBar() if sys.stderr.isatty() and not args.quiet else None
        try:
            report = run_report(prices, config, benchmark_tickers=tuple(args.benchmark), on_progress=progress_bar)
        finally:
            if progress_bar is not None:
                progress_bar.close()
    except (InvalidOptimizationInput, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(report.to_dict(include_equity=not args.no_equity), sys.stdout, indent=2)
        print()
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
