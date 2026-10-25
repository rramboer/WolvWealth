"""Unit tests for the domain optimizer (plain dict in, plain dict out, no app needed)."""
import numpy as np
import pandas as pd
import pytest

from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import ValidationError


def error_of(market, body):
    """Return the ValidationError message optimizing ``body`` raises."""
    with pytest.raises(ValidationError) as excinfo:
        optimizer.optimize(body, market)
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
    request = optimizer.parse_request({"initial_cash": 1000, "universe": ["top20", "aaa", "BBB"]}, market)
    assert request.universe == ["AAA", "BBB", "CCC", "DDD"]


def test_lowercase_tickers_accepted(market):
    request = optimizer.parse_request({"initial_cash": 1000, "universe": ["aaa", "bbb"]}, market)
    assert request.universe == ["AAA", "BBB"]


def test_empty_universe_uses_default(market):
    request = optimizer.parse_request({"initial_cash": 1000, "universe": []}, market)
    assert request.universe == list(market.universe)


# --- results ----------------------------------------------------------------


def test_weights_sum_to_one_and_values_consistent(market):
    portfolio = optimizer.optimize({"initial_cash": 10000}, market)["optimized_portfolio"]
    assert portfolio, "expected at least one position"
    assert sum(p["percent_weight"] for p in portfolio.values()) == pytest.approx(100, abs=0.5)
    assert sum(p["value"] for p in portfolio.values()) == pytest.approx(10000, abs=10)
    for ticker, position in portfolio.items():
        assert position["shares"] * market.price(ticker) == pytest.approx(position["value"], abs=0.5)


def test_holdings_are_valued_and_pooled(market):
    result = optimizer.optimize({"initial_cash": 1000, "initial_holdings": {"AAA": 10}}, market)
    assert result["metrics"]["portfolio_value"] == pytest.approx(1000 + 10 * market.price("AAA"), abs=0.01)


def test_max_weight_respected(market):
    result = optimizer.optimize({"initial_cash": 10000, "constraints": {"max_weight": 0.4}}, market)
    for position in result["optimized_portfolio"].values():
        assert position["percent_weight"] <= 40.0 + 0.01


def test_max_positions_truncates_and_renormalizes(market):
    result = optimizer.optimize({"initial_cash": 10000, "constraints": {"max_positions": 2}}, market)
    portfolio = result["optimized_portfolio"]
    assert len(portfolio) <= 2
    assert result["metrics"]["num_assets"] == len(portfolio)
    assert sum(p["percent_weight"] for p in portfolio.values()) == pytest.approx(100, abs=0.5)


def test_metrics_describe_the_returned_portfolio(market):
    """Regression: metrics used to come from the untruncated solver output."""
    from pypfopt import expected_returns, risk_models

    result = optimizer.optimize({"initial_cash": 10000, "constraints": {"max_positions": 1}}, market)
    portfolio = result["optimized_portfolio"]
    assert len(portfolio) == 1
    weights = pd.Series({t: p["percent_weight"] / 100 for t, p in portfolio.items()}, dtype=float).reindex(
        market.prices.columns, fill_value=0.0
    )
    mu = expected_returns.mean_historical_return(market.prices)
    cov = risk_models.exp_cov(market.prices)
    expected_return = float(weights @ mu)
    volatility = float(np.sqrt(weights @ cov @ weights))
    assert result["metrics"]["expected_annual_return"] == pytest.approx(expected_return, abs=0.01)
    assert result["metrics"]["annual_volatility"] == pytest.approx(volatility, abs=0.01)
    assert result["metrics"]["sharpe_ratio"] == pytest.approx((expected_return - 0.02) / volatility, abs=0.05)


def test_exclude_metrics(market):
    assert "metrics" not in optimizer.optimize({"initial_cash": 10000, "exclude_metrics": True}, market)


def test_solver_error_is_surfaced(market):
    """Regression: the real solver error used to be swallowed into a bare 'Infeasible'."""
    message = error_of(market, {"initial_cash": 10000, "constraints": {"min_universal_weight": 0.5, "max_weight": 1}})
    assert message.startswith("Optimization Error.")
    assert len(message) > len("Optimization Error. Check your inputs and constraints. ")


def test_market_snapshot_helpers(market):
    assert "AAA" in market and "ZZZ" not in market
    assert market.top(2) == ["AAA", "BBB"]
    assert market.value({"AAA": 2}) == pytest.approx(2 * market.price("AAA"))
    truncated = market.truncated("2020-06-30")
    assert truncated.as_of <= pd.Timestamp("2020-06-30").date() and len(truncated.prices) < len(market.prices)
    assert market.age_days(now=market.prices.index[-1]) == 0


def test_degenerate_history_reports_null_sharpe_instead_of_crashing():
    """Regression: two price rows give zero volatility, which used to raise ZeroDivisionError (a 500)."""
    from wolvwealth.domain.market import MarketData

    frame = pd.DataFrame(
        {"AAA": [100.0, 101.0], "BBB": [200.0, 201.0]}, index=pd.to_datetime(["2026-10-23", "2026-10-24"])
    )
    result = optimizer.optimize({"initial_cash": 1000}, MarketData.build(["AAA", "BBB"], frame))
    assert result["metrics"]["sharpe_ratio"] is None
    assert result["metrics"]["portfolio_value"] == 1000
