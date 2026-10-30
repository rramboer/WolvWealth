"""Delisted tickers: in the ticker list, but with no current price.

Real data is full of them (acquisitions, renames). They used to reach the
solver, where their all-NaN covariance made it reject the entire problem,
and reach `market.value()`, where they raised an uncaught KeyError.
"""

import numpy as np
import pytest

from conftest import synthetic_market
from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import ValidationError
from wolvwealth.domain.market import MarketData
from wolvwealth.domain.stats import MarketStats


@pytest.fixture
def with_delisted():
    """A market whose CCC stopped trading a year before the data ends."""
    base = synthetic_market()
    prices = base.prices.copy()
    prices.iloc[-250:, prices.columns.get_loc("CCC")] = np.nan
    return MarketData.build(base.universe, prices)


def test_delisted_ticker_is_in_the_universe_but_not_tradeable(with_delisted):
    assert "CCC" in with_delisted
    assert not with_delisted.has_price("CCC")
    assert "CCC" not in with_delisted.tradeable


def test_aliases_skip_delisted_names_and_still_fill_the_count(with_delisted):
    assert "CCC" not in with_delisted.top(3)
    assert with_delisted.top(3) == ["AAA", "BBB", "DDD"]


def test_default_universe_excludes_delisted_names(with_delisted):
    request = optimizer.parse_request({"initial_cash": 1000}, with_delisted)
    assert "CCC" not in request.universe


def test_delisted_universe_entry_is_a_clear_validation_error(with_delisted):
    with pytest.raises(ValidationError) as excinfo:
        optimizer.optimize({"initial_cash": 1000, "universe": ["AAA", "CCC"]}, with_delisted)
    assert "CCC" in excinfo.value.message and "delisted" in excinfo.value.message


def test_unknown_universe_entry_keeps_its_own_message(with_delisted):
    with pytest.raises(ValidationError) as excinfo:
        optimizer.optimize({"initial_cash": 1000, "universe": ["NOPE"]}, with_delisted)
    assert "Invalid symbol in universe" in excinfo.value.message


def test_delisted_holding_is_a_400_not_an_uncaught_keyerror(with_delisted):
    """Regression: market.value() raised KeyError, which the API turned into a 500."""
    with pytest.raises(ValidationError) as excinfo:
        optimizer.optimize(
            {"initial_cash": 1000, "initial_holdings": {"CCC": 10}, "universe": ["AAA", "BBB"]},
            with_delisted,
        )
    assert "CCC" in excinfo.value.message and "delisted" in excinfo.value.message


def test_default_request_solves_despite_a_delisted_name(with_delisted):
    """The whole point: NaN covariance no longer reaches the solver."""
    result = optimizer.optimize({"initial_cash": 10_000}, with_delisted)
    assert result["optimized_portfolio"]
    assert "CCC" not in result["optimized_portfolio"]


def test_statistics_over_tradeable_names_are_free_of_nan(with_delisted):
    stats = MarketStats.compute(with_delisted.prices[list(with_delisted.tradeable)])
    assert not stats.mu.isna().any()
    assert not stats.cov.isna().to_numpy().any()


def test_api_reports_a_delisted_holding_as_400(client, services, with_delisted):
    services.market.set_data(with_delisted)
    response = client.post("/api/optimize/", json={"initial_cash": 1000, "initial_holdings": {"CCC": 1}})
    assert response.status_code == 400
    assert "CCC" in response.get_json()["error"]["message"]
