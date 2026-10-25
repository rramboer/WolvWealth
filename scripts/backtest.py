"""Walk-forward backtest of the optimizer over the cached price history.

Usage: python scripts/backtest.py

Reuses the app's optimizer (no duplicated maths). At each step the market
snapshot is truncated to the current date, so every solve only sees data
available at that time (no lookahead). Nothing global is mutated.
"""
import datetime
import sys

from wolvwealth.domain import optimizer
from wolvwealth.domain.market import MarketData
from wolvwealth.services.container import Services

INITIAL_CASH = 100_000.0
UNIVERSE = ["top50"]
MAX_POSITIONS = 10
START_DATE = "2019-01-01"
FREQUENCIES = {"yearly": 365, "semiannually": 182, "quarterly": 91, "monthly": 30, "weekly": 7}


def optimize_at(market: MarketData, date, cash, holdings):
    """Optimize with only the data known on `date`; return the result dict."""
    return optimizer.optimize(
        {
            "initial_cash": cash,
            "initial_holdings": holdings,
            "universe": UNIVERSE + list(holdings),
            "constraints": {"max_positions": MAX_POSITIONS},
        },
        market.truncated(date),
    )


def next_trading_day(index, date_string, delta_days):
    """Return the first trading day on or after date + delta, or None past the end."""
    target = datetime.datetime.strptime(date_string, "%Y-%m-%d") + datetime.timedelta(days=delta_days)
    position = index.searchsorted(target)
    if position >= len(index):
        return None
    return str(index[position].date())


def run_backtest(market: MarketData, start_date, frequency_days, initial_cash=INITIAL_CASH):
    """Walk forward from start_date, reoptimizing every frequency_days. Return {date: value}."""
    index = market.prices.index
    date = next_trading_day(index, start_date, 0)
    result = optimize_at(market, date, initial_cash, {})
    values = {date: result["metrics"]["portfolio_value"]}
    while True:
        date = next_trading_day(index, date, frequency_days)
        if date is None:
            break
        holdings = {ticker: row["shares"] for ticker, row in result["optimized_portfolio"].items()}
        result = optimize_at(market, date, 0, holdings)
        values[date] = result["metrics"]["portfolio_value"]
    return values


def main():
    """Run the backtest at every frequency and print per-period values."""
    market = Services.build().market.data
    for name, days in FREQUENCIES.items():
        print(f"=====  FREQUENCY: {name}  =====")
        values = run_backtest(market, START_DATE, days)
        previous = None
        for date, value in values.items():
            change = f" ({(value - previous) / previous * 100:+.2f}%)" if previous else ""
            print(f"{date}: {value:.2f}{change}")
            previous = value
        series = list(values.values())
        print(f"=====  TOTAL RETURN: {(series[-1] - series[0]) / series[0] * 100:.2f}%  =====\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
