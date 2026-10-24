"""Unit tests for the Optimization core (plain dict in, plain dict out)."""
import numpy as np
import pandas as pd
import pytest

import wolvwealth
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.api.optimize import Optimization


def optimize(market, body):
    """Run one optimization inside a request context, return the result dict."""
    with wolvwealth.app.test_request_context():
        return Optimization(body).result


def error_of(market, body):
    """Run one optimization and return the InvalidUsage message it raises."""
    with pytest.raises(InvalidUsage) as excinfo:
        optimize(market, body)
    return excinfo.value.message


# --- input validation -------------------------------------------------------


def test_rejects_non_dict_input(market):
    assert "JSON object" in error_of(market, ["not", "a", "dict"])


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({"initial_cash": "100"}, "initial_cash"),
        ({"initial_cash": -5}, "initial_cash"),
        ({"universe": "AAA"}, "universe"),
        ({"universe": [42]}, "universe"),
        ({"universe": ["FAKETICKER"]}, "FAKETICKER"),
        ({"initial_cash": 100, "initial_holdings": ["AAA"]}, "initial_holdings"),
        ({"initial_cash": 100, "initial_holdings": {"FAKETICKER": 1}}, "FAKETICKER"),
        ({"initial_cash": 100, "initial_holdings": {"AAA": -1}}, "positive"),
        ({"initial_cash": 100, "constraints": "nope"}, "constraints"),
        ({"initial_cash": 100, "constraints": {"max_weight": "big"}}, "max_weight"),
        ({"initial_cash": 100, "constraints": {"max_weight": 1.5}}, "max_weight"),
        ({"initial_cash": 100, "constraints": {"max_positions": 2.5}}, "max_positions"),
        ({"initial_cash": 100, "constraints": {"max_positions": 0}}, "max_positions"),
        ({"initial_cash": 100, "constraints": {"max_positions": 2, "max_weight": 0.25}}, "Infeasible"),
        ({"initial_cash": 100, "constraints": {"min_universal_weight": 2}}, "min_universal_weight"),
        ({"initial_cash": 100, "constraints": {"min_universal_weight": 0.5, "max_weight": 0.3}}, "less than"),
        ({"initial_cash": 100, "exclude_metrics": "yes"}, "exclude_metrics"),
    ],
)
def test_rejects_bad_input(market, body, fragment):
    assert fragment in error_of(market, body)


def test_rejects_zero_total_investment(market):
    assert "cannot be 0" in error_of(market, {"initial_cash": 0})


def test_invalid_ticker_after_alias_is_still_rejected(market):
    """Regression: mutating the universe list while iterating let a bad ticker
    that followed an alias slip through validation unchecked."""
    assert "FAKETICKER" in error_of(market, {"initial_cash": 100, "universe": ["top20", "FAKETICKER"]})


# --- universe handling ------------------------------------------------------


def test_alias_expansion_and_dedupe(market):
    with wolvwealth.app.test_request_context():
        opt = Optimization({"initial_cash": 1000, "universe": ["top20", "aaa", "BBB"]})
    assert opt.universe == ["AAA", "BBB", "CCC", "DDD"]


def test_lowercase_tickers_accepted(market):
    with wolvwealth.app.test_request_context():
        opt = Optimization({"initial_cash": 1000, "universe": ["aaa", "bbb"]})
    assert opt.universe == ["AAA", "BBB"]


def test_empty_universe_uses_default(market):
    with wolvwealth.app.test_request_context():
        opt = Optimization({"initial_cash": 1000, "universe": []})
    assert opt.universe == market.TICKER_UNIVERSE


# --- results ----------------------------------------------------------------


def test_weights_sum_to_one_and_values_consistent(market):
    result = optimize(market, {"initial_cash": 10000})
    portfolio = result["optimized_portfolio"]
    assert portfolio, "expected at least one position"
    assert sum(p["percent_weight"] for p in portfolio.values()) == pytest.approx(100, abs=0.5)
    assert sum(p["value"] for p in portfolio.values()) == pytest.approx(10000, abs=10)
    for ticker, position in portfolio.items():
        price = market.fetch_ticker_price(ticker)
        assert position["shares"] * price == pytest.approx(position["value"], abs=0.5)


def test_holdings_are_valued_and_pooled(market):
    shares = 10
    value_of_holdings = shares * market.fetch_ticker_price("AAA")
    result = optimize(market, {"initial_cash": 1000, "initial_holdings": {"AAA": shares}})
    assert result["metrics"]["portfolio_value"] == pytest.approx(1000 + value_of_holdings, abs=0.01)


def test_max_weight_respected(market):
    result = optimize(market, {"initial_cash": 10000, "constraints": {"max_weight": 0.4}})
    for position in result["optimized_portfolio"].values():
        assert position["percent_weight"] <= 40.0 + 0.01


def test_max_positions_truncates_and_renormalizes(market):
    result = optimize(market, {"initial_cash": 10000, "constraints": {"max_positions": 2}})
    portfolio = result["optimized_portfolio"]
    assert len(portfolio) <= 2
    assert result["metrics"]["num_assets"] == len(portfolio)
    assert sum(p["percent_weight"] for p in portfolio.values()) == pytest.approx(100, abs=0.5)


def test_metrics_describe_the_returned_portfolio(market):
    """Regression: metrics used to come from the untruncated solver output."""
    from pypfopt import expected_returns, risk_models

    result = optimize(market, {"initial_cash": 10000, "constraints": {"max_positions": 1}})
    portfolio = result["optimized_portfolio"]
    assert len(portfolio) == 1

    weights = pd.Series(
        {t: p["percent_weight"] / 100 for t, p in portfolio.items()}, dtype=float
    ).reindex(market.HISTORICAL_PRICES.columns, fill_value=0.0)
    mu = expected_returns.mean_historical_return(market.HISTORICAL_PRICES)
    cov = risk_models.exp_cov(market.HISTORICAL_PRICES)
    expected_return = float(weights @ mu)
    volatility = float(np.sqrt(weights @ cov @ weights))

    assert result["metrics"]["expected_annual_return"] == pytest.approx(expected_return, abs=0.01)
    assert result["metrics"]["annual_volatility"] == pytest.approx(volatility, abs=0.01)
    assert result["metrics"]["sharpe_ratio"] == pytest.approx((expected_return - 0.02) / volatility, abs=0.05)


def test_exclude_metrics(market):
    result = optimize(market, {"initial_cash": 10000, "exclude_metrics": True})
    assert "metrics" not in result


def test_solver_error_is_surfaced(market):
    """Regression: the real solver error used to be swallowed into a bare 'Infeasible'."""
    # Lower bound of 0.5 on all 4 assets cannot sum to 1: genuinely infeasible.
    message = error_of(market, {"initial_cash": 10000, "constraints": {"min_universal_weight": 0.5, "max_weight": 1}})
    assert message != "Optimization Error. Check your inputs and constraints. Infeasible."
    assert message.startswith("Optimization Error.")
    assert len(message) > len("Optimization Error. Check your inputs and constraints. ")
