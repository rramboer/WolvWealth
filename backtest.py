#!/usr/bin/env python3
"""Backtest the WolvWealth portfolio optimizer over historical data.

Re-optimizes a max-Sharpe portfolio at a fixed frequency (e.g. monthly),
carrying holdings forward, and reports the portfolio value and percent change
at each rebalance date. Uses the same optimization core as the /api/optimize/
endpoint (wolvwealth.optimizer_core).

Usage (from the repository root):

    uv run python backtest.py --start 2019-01-01 --cash 100000 --frequency monthly
"""

import argparse
import datetime

import pandas as pd

from wolvwealth.api.state import ApplicationState
from wolvwealth.optimizer_core import build_portfolio_output, compute_max_sharpe_portfolio

FREQUENCIES: dict[str, datetime.timedelta] = {
    "yearly": datetime.timedelta(days=365),
    "semiannually": datetime.timedelta(days=182),
    "quarterly": datetime.timedelta(days=91),
    "monthly": datetime.timedelta(days=30),
    "biweekly": datetime.timedelta(days=14),
    "weekly": datetime.timedelta(days=7),
    "daily": datetime.timedelta(days=1),
}

UNIVERSE_SIZE = 50  # Optimize over the N largest stocks by market cap.
MAX_POSITIONS = 10  # Keep only the N largest positions each rebalance.


def run_optimization(prices: pd.DataFrame, universe: list[str], holdings: dict[str, float], cash: float) -> dict:
    """Optimize a portfolio given price history up to the rebalance date."""

    def price_of(ticker: str) -> float:
        return prices[ticker].iloc[-1]

    total_investment = cash + sum(shares * price_of(asset) for asset, shares in holdings.items())
    weights, performance = compute_max_sharpe_portfolio(prices[universe], max_positions=MAX_POSITIONS)
    return build_portfolio_output(weights, total_investment, price_of, performance)


def holdings_from_output(output: dict) -> dict[str, float]:
    """Convert an optimization output into the next rebalance's initial holdings."""
    return {ticker: position["shares"] for ticker, position in output["optimized_portfolio"].items()}


def next_trading_date(index: pd.DatetimeIndex, date: str, step: datetime.timedelta) -> str:
    """Advance `date` by `step`, then forward to the next date present in the index."""
    moment = datetime.datetime.strptime(date, "%Y-%m-%d") + step
    while moment <= index[-1]:
        candidate = moment.strftime("%Y-%m-%d")
        if candidate in index:
            return candidate
        moment += datetime.timedelta(days=1)
    return (index[-1] + datetime.timedelta(days=1)).strftime("%Y-%m-%d")  # past the end: stops the loop


def percent_change(old: float, new: float) -> float:
    """Percent change from old to new, rounded to 2 decimal places."""
    return round((new - old) / old * 100, 2)


def run_backtest(start_date: str, end_date: str, initial_cash: float, frequency: datetime.timedelta) -> dict:
    """Run the backtest and return {date: (portfolio_value, percent_change)}."""
    state = ApplicationState()
    prices = state.HISTORICAL_PRICES
    universe = state.TICKER_UNIVERSE[:UNIVERSE_SIZE]

    # The price data is finite: once the schedule passes the last CSV date,
    # next_trading_date() keeps returning the same "past the end" sentinel
    # (last date + 1 day). Clamp end_date to that sentinel so the loop below
    # terminates even when --end (default: today) is beyond the data.
    past_data_end = (prices.index[-1] + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    end_date = min(end_date, past_data_end)

    if start_date >= past_data_end:
        raise SystemExit(f"--start {start_date} is after the last available price date ({prices.index[-1].date()})")
    while start_date not in prices.index:
        start_date = (datetime.datetime.strptime(start_date, "%Y-%m-%d") + datetime.timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )

    output = run_optimization(prices.loc[:start_date], universe, {}, initial_cash)
    returns = {start_date: (output["metrics"]["portfolio_value"], 0)}
    prev_total = output["metrics"]["portfolio_value"]

    curr_date = next_trading_date(prices.index, start_date, frequency)
    while curr_date < end_date:
        output = run_optimization(prices.loc[:curr_date], universe, holdings_from_output(output), 0.0)
        total = output["metrics"]["portfolio_value"]
        returns[curr_date] = (total, percent_change(prev_total, total))
        prev_total = total
        curr_date = next_trading_date(prices.index, curr_date, frequency)
    return returns


def print_results(returns: dict) -> None:
    """Print per-rebalance portfolio values and the total return."""
    values = list(returns.values())
    for date, (value, change) in returns.items():
        print(f"{date}: {value} ({change}%)")
    print(f"=====  TOTAL RETURN: {percent_change(values[0][0], values[-1][0])}% =====")


def main() -> None:
    """Parse CLI arguments and run backtests."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2019-01-01", help="backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=datetime.date.today().strftime("%Y-%m-%d"), help="backtest end date")
    parser.add_argument("--cash", type=float, default=100000, help="initial cash")
    parser.add_argument(
        "--frequency",
        choices=sorted(FREQUENCIES),
        action="append",
        help="rebalance frequency; repeatable (default: all but daily)",
    )
    args = parser.parse_args()

    frequencies = args.frequency or ["yearly", "semiannually", "quarterly", "monthly", "weekly"]
    for name in frequencies:
        print(f"=====  FREQUENCY: {name.upper()}  =====")
        print_results(run_backtest(args.start, args.end, args.cash, FREQUENCIES[name]))
        print()


if __name__ == "__main__":
    main()
