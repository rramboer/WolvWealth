"""A ticker Yahoo did not quote today must not break the optimization.

Yahoo throttles individual tickers constantly, so a price cache routinely
holds names whose newest close is a day or two behind the file's own date.
The app used to treat "no close stamped with the newest date" as "no price",
which refused the entire request -- on the main path, for a single missing
day, in exactly the unattended scheduled runs that cannot ask a human what
to do. A close within MAX_STALE_DAYS is now carried forward and reported;
beyond that the position is kept as it is rather than failing the request.
"""

import numpy as np
import pytest

from conftest import synthetic_market
from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import ValidationError
from wolvwealth.domain.market import MAX_STALE_DAYS, MarketData

HOLDINGS = {"BBB": 10}


def market_missing(days: int, ticker: str = "BBB") -> MarketData:
    """A market where ``ticker`` has no close for the last ``days`` trading days."""
    base = synthetic_market()
    prices = base.prices.copy()
    if days:
        prices.iloc[-days:, prices.columns.get_loc(ticker)] = np.nan
    return MarketData.build(base.universe, prices)


@pytest.mark.parametrize("missing", [1, 2, MAX_STALE_DAYS], ids=lambda n: f"{n} day(s) throttled")
def test_a_throttled_holding_still_optimizes_at_its_last_close(missing):
    """The headline case: one throttled day used to return a 400 and no plan at all."""
    market = market_missing(missing)
    carried = float(synthetic_market().prices["BBB"].iloc[-(missing + 1)])
    result = optimizer.optimize({"initial_cash": 10_000, "initial_holdings": HOLDINGS}, market)

    assert result["optimized_portfolio"], "a plan is produced, not an error"
    assert "unoptimized" not in result, "a throttled ticker is ordinary, not a kept position"
    # Priced at its last close, and the response says which day that was.
    assert market.price("BBB") == pytest.approx(carried)
    assert result["stale_prices"]["BBB"]["trading_days_stale"] == missing
    assert result["stale_prices"]["BBB"]["as_of"] < result["as_of"]
    # It stays a normal ticker: nameable, and eligible for the universe.
    assert "BBB" in optimizer.parse_request({"initial_cash": 1000, "universe": ["BBB"]}, market).universe


def test_nothing_is_reported_stale_when_every_ticker_printed():
    result = optimizer.optimize({"initial_cash": 10_000, "initial_holdings": HOLDINGS}, synthetic_market())
    assert "stale_prices" not in result and "unoptimized" not in result


def test_past_the_window_the_position_is_kept_instead_of_failing_the_request():
    market = market_missing(MAX_STALE_DAYS + 1)
    result = optimizer.optimize({"initial_cash": 10_000, "initial_holdings": HOLDINGS}, market)
    kept = result["unoptimized"]["positions"]["BBB"]
    assert kept["reason"] == "no_current_price"
    assert kept["priced_as_of"] == str(market.valuation_date("BBB"))
    assert result["optimized_portfolio"]["BBB"]["shares"] == 10, "kept exactly as held, never resized"
    assert not any(trade["ticker"] == "BBB" for trade in result["trades"])
    assert result["stale_prices"]["BBB"]["trading_days_stale"] is None, "staler than the window can measure"


def test_a_kept_unquoted_position_still_counts_toward_the_portfolio_value():
    """Its value is uncertain, but omitting it would understate the portfolio outright."""
    market = market_missing(MAX_STALE_DAYS + 1)
    result = optimizer.optimize({"initial_cash": 10_000, "initial_holdings": HOLDINGS}, market)
    expected = 10_000 + 10 * market.valuation_price("BBB")
    assert result["metrics"]["portfolio_value"] == pytest.approx(expected, abs=0.01)
    assert sum(row["percent_weight"] for row in result["optimized_portfolio"].values()) == pytest.approx(100, abs=0.05)


def test_naming_an_unquoted_ticker_in_the_universe_is_still_refused_unless_it_is_held():
    """Buying needs a price someone is actually quoting; holding does not."""
    market = market_missing(MAX_STALE_DAYS + 1)
    with pytest.raises(ValidationError, match="No current price for BBB"):
        optimizer.optimize({"initial_cash": 1000, "universe": ["AAA", "BBB"]}, market)
    # Held, the same universe is accepted and the position is kept rather than refused.
    body = {"initial_cash": 1000, "initial_holdings": HOLDINGS, "universe": ["AAA", "BBB"]}
    assert optimizer.optimize(body, market)["unoptimized"]["positions"]["BBB"]


def test_a_quote_returning_puts_the_ticker_straight_back_into_the_optimization():
    """No manual step: the next refresh that quotes it makes it ordinary again."""
    recovered = optimizer.optimize({"initial_cash": 10_000, "initial_holdings": HOLDINGS}, market_missing(0))
    assert "unoptimized" not in recovered and "stale_prices" not in recovered


def test_a_throttled_ticker_is_not_dropped_from_the_default_universe():
    """It used to vanish from top-N silently, changing the opportunity set with no notice."""
    full = synthetic_market()
    throttled = market_missing(1, ticker="CCC")
    assert throttled.top(4) == full.top(4)
