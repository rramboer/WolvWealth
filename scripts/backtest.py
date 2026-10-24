"""Walk-forward backtest of the optimizer.

Starts with cash on START_DATE, re-optimizes at a fixed frequency using only
prices known at that date, and reports the portfolio value over time. Uses
the same ``Optimization`` class as the app, so the two cannot drift apart.

Usage (from any directory, after ``pip install -e .``)::

    python scripts/backtest.py                       # yearly, quarterly, monthly
    python scripts/backtest.py --frequency weekly    # one frequency
    python scripts/backtest.py --start 2021-01-01 --cash 50000 --universe top100 --max-positions 15
"""
import argparse
import datetime
import os
import sys

os.environ.setdefault("WOLVWEALTH_NO_AUTO_REFRESH", "1")  # A backtest should not touch the network

from wolvwealth.api.api_exceptions import InvalidUsage  # noqa: E402
from wolvwealth.api.optimize import Optimization  # noqa: E402
from wolvwealth.api.state import ApplicationState  # noqa: E402

FREQUENCIES = {
    "yearly": datetime.timedelta(days=365),
    "semiannual": datetime.timedelta(days=182),
    "quarterly": datetime.timedelta(days=91),
    "monthly": datetime.timedelta(days=30),
    "biweekly": datetime.timedelta(days=14),
    "weekly": datetime.timedelta(days=7),
}


def optimize_as_of(state, full_prices, date, body):
    """Run one optimization seeing only prices up to and including ``date``."""
    state.HISTORICAL_PRICES = full_prices.loc[:date]
    return Optimization(body).result


def holdings_from(result):
    """Turn an optimization result into the initial_holdings of the next run."""
    return {ticker: position["shares"] for ticker, position in result["optimized_portfolio"].items()}


def run(frequency, args):
    """Walk forward from args.start to today at the given frequency, printing values."""
    state = ApplicationState()
    full_prices = state.HISTORICAL_PRICES
    trading_days = full_prices.index
    constraints = {"max_positions": args.max_positions}

    date = trading_days[trading_days.searchsorted(args.start)]
    result = optimize_as_of(state, full_prices, date, {"initial_cash": args.cash, "universe": [args.universe],
                                                       "constraints": constraints})
    start_value = previous = result["metrics"]["portfolio_value"]
    print(f"{date.date()}: {start_value:,.2f}")

    while True:
        target = date + FREQUENCIES[frequency]
        position = trading_days.searchsorted(target)
        if position >= len(trading_days):
            break
        date = trading_days[position]
        result = optimize_as_of(state, full_prices, date, {"initial_holdings": holdings_from(result),
                                                           "universe": [args.universe], "constraints": constraints})
        value = result["metrics"]["portfolio_value"]
        print(f"{date.date()}: {value:,.2f} ({(value - previous) / previous * 100:+.2f}%)")
        previous = value

    state.HISTORICAL_PRICES = full_prices
    print(f"TOTAL RETURN: {(previous - start_value) / start_value * 100:+.2f}%")


def main():
    """Parse arguments and run one backtest per requested frequency."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frequency", choices=FREQUENCIES, action="append",
                        help="rebalance frequency (repeatable; default yearly, quarterly, monthly)")
    parser.add_argument("--start", default="2019-01-01", help="first optimization date (YYYY-MM-DD)")
    parser.add_argument("--cash", type=float, default=100_000, help="starting cash")
    parser.add_argument("--universe", default="top50", help="universe alias or ticker, e.g. top50")
    parser.add_argument("--max-positions", type=int, default=10)
    args = parser.parse_args()
    for frequency in args.frequency or ["yearly", "quarterly", "monthly"]:
        print(f"===== {frequency.upper()} REBALANCING =====")
        try:
            run(frequency, args)
        except InvalidUsage as err:
            print(f"Optimization failed: {err.message}", file=sys.stderr)
            return 1
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
