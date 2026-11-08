"""Delisted tickers: in the ticker list, but with no current price.

Real data is full of them (acquisitions, renames). They used to reach the
solver, where their all-NaN covariance made it reject the entire problem,
and reach `market.value()`, where they raised an uncaught KeyError.
"""

import numpy as np
import pytest

from conftest import synthetic_market
from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import DataError, ValidationError
from wolvwealth.domain.market import MarketData
from wolvwealth.domain.stats import MarketStats


@pytest.fixture
def with_delisted():
    """A market whose CCC stopped trading a year before the data ends."""
    base = synthetic_market()
    prices = base.prices.copy()
    prices.iloc[-250:, prices.columns.get_loc("CCC")] = np.nan
    return MarketData.build(base.universe, prices)


def test_a_delisted_ticker_is_known_but_not_tradeable_and_aliases_skip_it(with_delisted):
    assert "CCC" in with_delisted and not with_delisted.has_price("CCC")
    assert "CCC" not in with_delisted.tradeable
    assert with_delisted.top(3) == ["AAA", "BBB", "DDD"], "aliases skip it and still fill the count"


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"initial_cash": 1000, "universe": ["AAA", "CCC"]}, "delisted"),
        ({"initial_cash": 1000, "universe": ["NOPE"]}, "Invalid symbol in universe"),
    ],
    ids=["in the universe", "unknown keeps its own message"],
)
def test_a_delisted_name_is_refused_when_the_universe_names_it(with_delisted, body, fragment):
    """Buying it is impossible, so asking for it is an error -- unlike merely holding it."""
    with pytest.raises(ValidationError) as excinfo:
        optimizer.optimize(body, with_delisted)
    assert fragment in excinfo.value.message
    if fragment == "delisted":
        assert "CCC" in excinfo.value.message


def test_a_delisted_holding_is_kept_and_reported_rather_than_failing_the_request(with_delisted):
    """Refusing the whole optimization over one unquoted holding breaks unattended runs.

    Regression: an agent on a schedule got a 400 and no plan at all, whether the ticker was
    genuinely delisted or Yahoo had simply throttled it. The position is kept as it is, the
    rest is optimized around it, and the response says what was assumed and when.
    """
    body = {"initial_cash": 1000, "initial_holdings": {"CCC": 10}, "universe": ["AAA", "BBB"]}
    result = optimizer.optimize(body, with_delisted)
    kept = result["unoptimized"]["positions"]["CCC"]
    assert kept["reason"] == "no_current_price"
    assert kept["priced_as_of"] == str(with_delisted.valuation_date("CCC"))
    assert result["optimized_portfolio"]["CCC"]["shares"] == 10, "kept exactly as held"
    assert not any(trade["ticker"] == "CCC" for trade in result["trades"]), "never traded at a price nobody quotes"
    assert "CCC" in result["stale_prices"] and "last known close" in result["unoptimized"]["note"]
    # The money that can be optimized still is.
    assert set(result["optimized_portfolio"]) - {"CCC"}


def test_default_request_solves_despite_a_delisted_name(with_delisted):
    """The whole point: NaN covariance no longer reaches the solver."""
    assert "CCC" not in optimizer.parse_request({"initial_cash": 1000}, with_delisted).universe
    stats = MarketStats.compute(with_delisted.prices[list(with_delisted.tradeable)])
    assert not stats.mu.isna().any() and not stats.cov.isna().to_numpy().any()
    result = optimizer.optimize({"initial_cash": 10_000}, with_delisted)
    assert result["optimized_portfolio"] and "CCC" not in result["optimized_portfolio"]


def test_a_market_where_nothing_is_tradeable_is_a_data_error_not_a_crash():
    """A ticker list that matches none of the cached prices left the solver dividing by zero."""
    prices = synthetic_market().prices.rename(columns=str.lower).rename(columns=lambda t: f"OLD{t}")
    with pytest.raises(DataError, match="refresh-data --full"):
        optimizer.optimize({"initial_cash": 1000}, MarketData.build(["AAA", "BBB"], prices))


def test_api_serves_a_plan_for_a_delisted_holding_instead_of_an_error(client, services, with_delisted):
    services.market.set_data(with_delisted)
    response = client.post("/api/optimize/", json={"initial_cash": 1000, "initial_holdings": {"CCC": 1}})
    assert response.status_code == 200
    body = response.get_json()
    assert body["unoptimized"]["positions"]["CCC"]["reason"] == "no_current_price"
    assert body["stale_prices"]["CCC"]["as_of"] == str(with_delisted.valuation_date("CCC"))


def test_api_still_refuses_a_holding_that_never_had_a_price(client, services, market):
    """The one unvaluable case: no close has ever been recorded, so no number can be put on it."""
    prices = market.prices.copy()
    prices["CCC"] = float("nan")
    services.market.set_data(MarketData.build(market.universe, prices))
    response = client.post("/api/optimize/", json={"initial_cash": 1000, "initial_holdings": {"CCC": 1}})
    assert response.status_code == 400
    assert "No price has ever been downloaded for CCC" in response.get_json()["error"]["message"]
