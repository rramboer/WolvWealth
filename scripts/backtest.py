"""Walk-forward backtest of the optimizer over the cached price history.

Usage: python scripts/backtest.py

Reuses the app's Optimization class (no duplicated maths). At each step the
global price DataFrame is truncated to the current date, so every solve only
sees data available at that time (no lookahead). The script mutates the
ApplicationState singleton in place: run it standalone, never import it from
the running web app.
"""
import datetime
import os
import sys

os.environ.setdefault("WOLVWEALTH_NO_AUTO_REFRESH", "1")  # The backtest manages its own data window

from wolvwealth.api.optimize import Optimization  # noqa: E402
from wolvwealth.api.state import ApplicationState  # noqa: E402

INITIAL_CASH = 100_000.0
UNIVERSE = ["top50"]
MAX_POSITIONS = 10
START_DATE = "2019-01-01"
FREQUENCIES = {"yearly": 365, "semiannually": 182, "quarterly": 91, "monthly": 30, "weekly": 7}


def optimize_at(full_prices, date, cash, holdings):
    """Optimize with only the data known on `date`; return the result dict."""
    state = ApplicationState()
    state.HISTORICAL_PRICES = full_prices[:date]
    return Optimization(
        {
            "initial_cash": cash,
            "initial_holdings": holdings,
            "universe": UNIVERSE + list(holdings),
            "constraints": {"max_positions": MAX_POSITIONS},
        }
    ).result


def next_trading_day(index, date_string, delta_days):
    """Return the first trading day on or after date + delta, or None past the end."""
    target = datetime.datetime.strptime(date_string, "%Y-%m-%d") + datetime.timedelta(days=delta_days)
    position = index.searchsorted(target)
    if position >= len(index):
        return None
    return str(index[position].date())


def run_backtest(full_prices, start_date, frequency_days, initial_cash=INITIAL_CASH):
    """Walk forward from start_date, reoptimizing every frequency_days. Return {date: value}."""
    date = next_trading_day(full_prices.index, start_date, 0)
    result = optimize_at(full_prices, date, initial_cash, {})
    values = {date: result["metrics"]["portfolio_value"]}
    while True:
        date = next_trading_day(full_prices.index, date, frequency_days)
        if date is None:
            break
        holdings = {ticker: row["shares"] for ticker, row in result["optimized_portfolio"].items()}
        result = optimize_at(full_prices, date, 0, holdings)
        values[date] = result["metrics"]["portfolio_value"]
    return values


def main():
    """Run the backtest at every frequency and print per-period values."""
    state = ApplicationState()
    full_prices = state.HISTORICAL_PRICES
    try:
        for name, days in FREQUENCIES.items():
            print(f"=====  FREQUENCY: {name}  =====")
            values = run_backtest(full_prices, START_DATE, days)
            previous = None
            for date, value in values.items():
                change = f" ({(value - previous) / previous * 100:+.2f}%)" if previous else ""
                print(f"{date}: {value:.2f}{change}")
                previous = value
            series = list(values.values())
            print(f"=====  TOTAL RETURN: {(series[-1] - series[0]) / series[0] * 100:.2f}%  =====\n")
    finally:
        state.HISTORICAL_PRICES = full_prices
    return 0


if __name__ == "__main__":
    sys.exit(main())
