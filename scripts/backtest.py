"""Walk-forward backtest of the optimizer over the cached price history, against a 1/N baseline.

Usage: python scripts/backtest.py [--estimator NAME] [--start YYYY-MM-DD] [--frequencies quarterly,monthly]

Reuses the app's optimizer (no duplicated maths). At each step the market
snapshot is truncated to the current date, so every solve only sees data
available at that time (no lookahead). Holdings stay fixed between
rebalances and are valued daily, so volatility and drawdown are measured
on what was actually held, not just on the rebalance dates. Nothing global
is mutated.

``--estimator`` picks the expected-return estimator (one of
``domain.stats.ESTIMATORS``; default the one the app uses), which is how
candidates are compared. The equal-weight line is the hurdle: a max-Sharpe
portfolio that does not beat 1/N on realized Sharpe is not earning its
estimation error.

The results are survivorship-biased, and the output says so: see
``SURVIVORSHIP_WARNING``.
"""

import argparse
import dataclasses
import datetime
import math
import sys

import pandas as pd

from wolvwealth.config import Settings
from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import DataError
from wolvwealth.domain.market import MIN_HISTORY_DAYS, MarketData
from wolvwealth.domain.stats import ESTIMATOR, ESTIMATORS, TRADING_DAYS
from wolvwealth.storage.price_store import PriceStore

INITIAL_CASH = 100_000.0
UNIVERSE_SIZE = 50
MAX_POSITIONS = 10
START_DATE = "2019-01-01"
FREQUENCIES = {"yearly": 365, "semiannually": 182, "quarterly": 91, "monthly": 30, "weekly": 7}
STRATEGIES = ("max_sharpe", "equal_weight")
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


@dataclasses.dataclass
class Walk:
    """One strategy walked forward: its daily value and the dates it rebalanced on."""

    values: pd.Series  # daily portfolio value, from the first rebalance to the end of the data
    rebalances: list  # "YYYY-MM-DD" strings
    turnovers: list  # per rebalance after the first: value sold as a fraction of the portfolio's value that day


def seasoned_universe(known: MarketData) -> list:
    """The ``UNIVERSE_SIZE`` largest tradeable names with at least ``MIN_HISTORY_DAYS`` of prices by ``known``'s end.

    The universe is ranked by today's market cap, so a newly listed company
    can be "top 50" on its first trading day, when its return history is one
    number and its covariance is NaN. The app's own seasoning rule (the
    domain's ``MIN_HISTORY_DAYS``, a year) is applied as of ``known``'s end,
    which is also what an investor at the time had.
    """
    names = known.top(UNIVERSE_SIZE)
    if not names:
        raise ValueError(f"no ticker has {MIN_HISTORY_DAYS} days of history by {known.as_of}; start the walk later")
    return names


def optimize_at(known: MarketData, universe: list, cash, holdings):
    """Optimize over ``universe`` plus the held names with only the data in ``known``; return the result dict."""
    return optimizer.optimize(
        {
            "initial_cash": cash,
            "initial_holdings": holdings,
            "universe": universe + [ticker for ticker in holdings if ticker not in universe],
            "constraints": {"max_positions": MAX_POSITIONS},
        },
        known,
    )


def target_shares(market: MarketData, date, cash, holdings, strategy: str) -> dict:
    """Shares to hold from ``date`` on, under ``strategy``, given the cash and holdings arriving there."""
    known = market.truncated(date)
    names = seasoned_universe(known)
    if strategy == "max_sharpe":
        result = optimize_at(known, names, cash, holdings)
        return {ticker: row["shares"] for ticker, row in result["optimized_portfolio"].items()}
    if strategy == "equal_weight":
        value = cash + known.value(holdings)
        return {ticker: value / len(names) / known.price(ticker) for ticker in names}
    raise ValueError(f"unknown strategy {strategy!r}")


def turnover(before: dict, after: dict, prices: pd.Series) -> float:
    """Fraction of the portfolio's value sold going from ``before`` to ``after`` at ``prices``."""
    value = sum(shares * prices[ticker] for ticker, shares in before.items())
    sold = sum(max(0.0, shares - after.get(ticker, 0.0)) * prices[ticker] for ticker, shares in before.items())
    return sold / value if value else 0.0


def next_trading_day(index, date_string, delta_days):
    """Return the first trading day on or after date + delta, or None past the end."""
    target = datetime.datetime.strptime(date_string, "%Y-%m-%d") + datetime.timedelta(days=delta_days)
    position = index.searchsorted(target)
    if position >= len(index):
        return None
    return str(index[position].date())


def run_backtest(market: MarketData, start_date, frequency_days, initial_cash=INITIAL_CASH, strategy="max_sharpe"):
    """Walk forward from start_date, rebalancing every frequency_days. Return a ``Walk``."""
    # Forward-filled so a name whose quotes stop mid-period keeps its last price until the next rebalance.
    prices = market.prices.ffill()
    date = next_trading_day(prices.index, start_date, 0)
    holdings = target_shares(market, date, initial_cash, {}, strategy)
    rebalances, turnovers, pieces = [date], [], []
    while True:
        following = next_trading_day(prices.index, date, frequency_days)
        window = prices.loc[date:following] if following else prices.loc[date:]
        pieces.append(window[list(holdings)].mul(pd.Series(holdings)).sum(axis=1))
        if following is None:
            break
        date = following
        previous, holdings = holdings, target_shares(market, date, 0, holdings, strategy)
        turnovers.append(turnover(previous, holdings, prices.loc[date]))
        rebalances.append(date)
    values = pd.concat(pieces)
    return Walk(values[~values.index.duplicated(keep="last")], rebalances, turnovers)


def metrics(values: pd.Series, turnovers=()) -> dict:
    """Total return, CAGR, annualized volatility, realized Sharpe (0% risk-free), max drawdown, and mean turnover.

    Turnover is the average fraction of the portfolio sold per rebalance.
    The walk charges no commissions, spreads or taxes, so two strategies
    with similar Sharpe ratios are not equal if one churns twice as much.
    """
    years = (values.index[-1] - values.index[0]).days / 365.25
    daily = values.pct_change().dropna()
    volatility = float(daily.std() * math.sqrt(TRADING_DAYS))
    cagr = float((values.iloc[-1] / values.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0
    return {
        "total_return": float(values.iloc[-1] / values.iloc[0] - 1),
        "cagr": cagr,
        "volatility": volatility,
        "sharpe": float(daily.mean() * TRADING_DAYS / volatility) if volatility > 0 else None,
        "max_drawdown": float((values / values.cummax() - 1).min()),
        "turnover": float(sum(turnovers) / len(turnovers)) if turnovers else 0.0,
    }


def format_row(label: str, figures: dict) -> str:
    """One strategy's line of the summary table."""
    sharpe = "   n/a" if figures["sharpe"] is None else f"{figures['sharpe']:6.2f}"
    return (
        f"  {label:<14}{figures['total_return']:>+9.1%}{figures['cagr']:>8.1%}{figures['volatility']:>8.1%}"
        f"{sharpe:>8}{figures['max_drawdown']:>9.1%}{figures['turnover']:>10.1%}"
    )


def _iso_date(raw: str) -> str:
    """Validate a YYYY-MM-DD date for argparse, which reports the error as a usage message."""
    try:
        return datetime.date.fromisoformat(raw).isoformat()
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a date (YYYY-MM-DD)") from err


def build_parser() -> argparse.ArgumentParser:
    """Flags: which estimator, where to start, which rebalance frequencies."""
    parser = argparse.ArgumentParser(description="Walk-forward backtest of the optimizer against a 1/N baseline.")
    parser.add_argument("--estimator", default=ESTIMATOR, choices=sorted(ESTIMATORS), help=f"default {ESTIMATOR}")
    parser.add_argument(
        "--start", default=START_DATE, type=_iso_date, metavar="YYYY-MM-DD", help=f"default {START_DATE}"
    )
    parser.add_argument(
        "--frequencies",
        default=",".join(FREQUENCIES),
        metavar="LIST",
        help=f"comma-separated subset of {', '.join(FREQUENCIES)} (default all)",
    )
    parser.add_argument("--verbose", action="store_true", help="Also print the portfolio value at every rebalance")
    return parser


def load_market() -> MarketData:
    """The cached prices, without the app's precomputed statistics (every step computes its own, truncated)."""
    settings = Settings.from_env()
    store = PriceStore(settings.prices_path, settings.ticker_universe_path, settings.legacy_csv_paths)
    return MarketData.build(store.load_universe(), store.load_prices())


def main(argv=None, market: MarketData = None, out=print) -> int:
    """Run every strategy at every requested frequency and print a summary table, bracketed by the bias warning.

    ``market`` and ``out`` are injectable so a test can run the whole report
    against a synthetic snapshot without the real cache.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    unknown = [name for name in args.frequencies.split(",") if name not in FREQUENCIES]
    if unknown:
        parser.error(f"unknown frequency {', '.join(unknown)}; choose from {', '.join(FREQUENCIES)}")
    frequencies = {name: FREQUENCIES[name] for name in args.frequencies.split(",")}
    try:
        market = (market if market is not None else load_market()).with_estimator(args.estimator)
    except DataError as err:
        parser.exit(1, f"backtest: {err.message}\n")
    if next_trading_day(market.prices.index, args.start, 0) is None:
        parser.error(f"--start {args.start} is after the last cached price ({market.as_of})")
    try:
        seasoned_universe(market.truncated(next_trading_day(market.prices.index, args.start, 0)))
    except ValueError as err:
        parser.error(str(err))
    out(SURVIVORSHIP_WARNING)
    out("")
    out(
        f"Universe: the {UNIVERSE_SIZE} largest names with {MIN_HISTORY_DAYS}+ days of history at each rebalance; "
        f"max {MAX_POSITIONS} positions; expected returns: {args.estimator}; from {args.start}"
    )
    for name, days in frequencies.items():
        out(f"=====  FREQUENCY: {name}  =====")
        out(f"  {'strategy':<14}{'total':>9}{'CAGR':>8}{'vol':>8}{'Sharpe':>8}{'max DD':>9}{'turnover':>10}")
        for strategy in STRATEGIES:
            walk = run_backtest(market, args.start, days, strategy=strategy)
            out(format_row(strategy, metrics(walk.values, walk.turnovers)))
            if args.verbose:
                for date in walk.rebalances:
                    out(f"    {date}: {walk.values.loc[date]:.2f}")
        out("  (survivorship-biased; see the warning)\n")
    out(SURVIVORSHIP_WARNING)
    return 0


if __name__ == "__main__":
    sys.exit(main())
