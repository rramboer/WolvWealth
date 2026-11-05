"""Walk-forward backtest of the optimizer over the cached price history.

Usage: python scripts/backtest.py

Reuses the app's optimizer (no duplicated maths). At each step the market
snapshot is truncated to the current date, so every solve only sees data
available at that time (no lookahead). Nothing global is mutated.

The results are survivorship-biased, and the output says so: see
``SURVIVORSHIP_WARNING``.
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
#: Printed before and after the numbers. Yahoo serves no history at all for a
#: symbol once it is delisted, so the cache holds only companies that were
#: still listed on the download date: every bankruptcy and failed merger that
#: the optimizer would have bought along the way is missing. The universe
#: itself is ranked by *today's* market cap, so "top50" in 2019 is really the
#: fifty biggest names of today, picked with hindsight. Both effects flatter
#: the result; the true figure is lower, by an amount this script cannot know.
SURVIVORSHIP_WARNING = (
    "WARNING: survivorship-biased. The price cache only holds companies still listed when it was\n"
    "downloaded, and the universe is ranked by today's market cap, so the optimizer never sees the\n"
    "names that failed and picks from winners chosen with hindsight. Returns below are overstated\n"
    "by an unknown amount. Compare strategies against each other, not against reality."
)


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


def main(market: MarketData = None, frequencies: dict = None, out=print, start_date: str = START_DATE):
    """Run the backtest at every frequency and print per-period values, bracketed by the bias warning.

    ``market``, ``frequencies``, ``out`` and ``start_date`` are injectable so a
    test can run the whole report against a synthetic snapshot without the real cache.
    """
    market = market if market is not None else Services.build().market.data
    out(SURVIVORSHIP_WARNING)
    out("")
    for name, days in (frequencies or FREQUENCIES).items():
        out(f"=====  FREQUENCY: {name}  =====")
        values = run_backtest(market, start_date, days)
        previous = None
        for date, value in values.items():
            change = f" ({(value - previous) / previous * 100:+.2f}%)" if previous else ""
            out(f"{date}: {value:.2f}{change}")
            previous = value
        series = list(values.values())
        out(f"=====  TOTAL RETURN: {(series[-1] - series[0]) / series[0] * 100:.2f}%  (survivorship-biased)  =====\n")
    out(SURVIVORSHIP_WARNING)
    return 0


if __name__ == "__main__":
    sys.exit(main())
