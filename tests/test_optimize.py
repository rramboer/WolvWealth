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
    rate = optimizer.DEFAULT_RISK_FREE_RATE
    assert result["metrics"]["sharpe_ratio"] == pytest.approx((expected_return - rate) / volatility, abs=0.05)
    assert result["risk_free_rate"]["value"] == rate


def test_solver_uses_the_same_risk_free_rate_the_metrics_report(market, monkeypatch):
    """Regression: max_sharpe() got no rate, so the solve followed PyPortfolioOpt's version-dependent default."""
    seen = []
    original = optimizer.EfficientFrontier.max_sharpe

    def recording(self, risk_free_rate="unset"):
        seen.append(risk_free_rate)
        return original(self, risk_free_rate=risk_free_rate)

    monkeypatch.setattr(optimizer.EfficientFrontier, "max_sharpe", recording)
    optimizer.optimize({"initial_cash": 10000}, market)
    assert seen == [optimizer.DEFAULT_RISK_FREE_RATE]
    optimizer.optimize({"initial_cash": 10000, "risk_free_rate": 0.01}, market)
    assert seen[-1] == 0.01


def test_returned_portfolio_maximizes_the_reported_sharpe(market):
    """The portfolio solved at a rate must score at least as well on that rate's Sharpe as one solved at another."""
    from pypfopt.efficient_frontier import EfficientFrontier

    mu, cov = market.stats_for(market.universe)
    rate = 0.04

    def sharpe_at(weights):
        vector = pd.Series(weights, dtype=float).reindex(mu.index, fill_value=0.0)
        return (float(vector @ mu) - rate) / float(np.sqrt(vector @ cov @ vector))

    frontier = EfficientFrontier(mu, cov, weight_bounds=(0, 1), solver=optimizer.SOLVER)
    frontier.max_sharpe(risk_free_rate=0.0)
    rival = sharpe_at(frontier.clean_weights())
    ours = optimizer.optimize({"initial_cash": 10000, "risk_free_rate": rate}, market)["optimized_portfolio"]
    assert sharpe_at({t: p["percent_weight"] / 100 for t, p in ours.items()}) >= rival - 1e-6


def test_no_rate_means_zero_with_a_note(market):
    """The fallback is 0%, flagged in the response so a reader can tell it from a deliberate zero."""
    result = optimizer.optimize({"initial_cash": 10000}, market)
    assert result["risk_free_rate"]["value"] == 0.0
    assert result["risk_free_rate"]["source"] == "default"
    assert "0% was assumed" in result["risk_free_rate"]["note"]
    metrics = result["metrics"]
    # At a zero rate the ratio is return over volatility; both are reported to 3 decimals, hence the slack.
    assert metrics["sharpe_ratio"] == pytest.approx(
        metrics["expected_annual_return"] / metrics["annual_volatility"], abs=0.05
    )
    explicit = optimizer.optimize({"initial_cash": 10000, "risk_free_rate": 0}, market)
    assert explicit["risk_free_rate"] == {"value": 0.0, "source": "request"}
    assert explicit["optimized_portfolio"] == result["optimized_portfolio"]
    assert "risk_free_rate" in optimizer.optimize({"initial_cash": 10000, "exclude_metrics": True}, market)


def test_a_higher_rate_tilts_toward_return(market):
    """A higher risk-free rate moves the tangency point up the frontier: more expected return, more volatility."""
    low = optimizer.optimize({"initial_cash": 10000, "risk_free_rate": 0.0}, market)["metrics"]
    high = optimizer.optimize({"initial_cash": 10000, "risk_free_rate": 0.08}, market)["metrics"]
    assert high["expected_annual_return"] >= low["expected_annual_return"]
    assert high["annual_volatility"] >= low["annual_volatility"]


def test_rate_above_every_expected_return_is_a_clear_error(market):
    message = error_of(market, {"initial_cash": 10000, "risk_free_rate": 0.9})
    assert "risk_free_rate" in message and "90.00%" in message and "cash" in message


@pytest.mark.parametrize(
    "value, fragment",
    [("0.04", "must be a number"), (True, "must be a number"), (4, "0.04 means 4%"), (-1, "0.04 means 4%")],
)
def test_rejects_bad_risk_free_rate(market, value, fragment):
    assert fragment in error_of(market, {"initial_cash": 1000, "risk_free_rate": value})


def test_exclude_metrics(market):
    assert "metrics" not in optimizer.optimize({"initial_cash": 10000, "exclude_metrics": True}, market)


# --- max_turnover -----------------------------------------------------------


def all_in(market, ticker, value=10_000.0):
    """Holdings worth ``value`` entirely in ``ticker``."""
    return {ticker: value / market.price(ticker)}


def sold_fraction(result):
    """Fraction of portfolio value the trade list sells."""
    sells = sum(trade["value"] for trade in result["trades"] if trade["action"] == "sell")
    return sells / result["metrics"]["portfolio_value"]


def test_unconstrained_run_sells_most_of_a_concentrated_portfolio(market):
    result = optimizer.optimize({"initial_holdings": all_in(market, "DDD")}, market)
    assert result["metrics"]["turnover"] > 0.5
    assert result["metrics"]["turnover"] == pytest.approx(sold_fraction(result), abs=1e-3)


def test_max_turnover_caps_what_is_sold(market):
    body = {"initial_holdings": all_in(market, "DDD"), "constraints": {"max_turnover": 0.3}}
    result = optimizer.optimize(body, market)
    assert sold_fraction(result) <= 0.3 + 1e-3
    assert result["metrics"]["turnover"] == pytest.approx(0.3, abs=1e-3), "the cap should bind"
    assert result["optimized_portfolio"]["DDD"]["percent_weight"] == pytest.approx(70, abs=0.1)


def test_zero_turnover_keeps_every_holding(market):
    body = {"initial_holdings": {"AAA": 20, "DDD": 10}, "constraints": {"max_turnover": 0}}
    result = optimizer.optimize(body, market)
    assert result["trades"] == []
    assert result["metrics"]["turnover"] == 0
    assert result["optimized_portfolio"]["AAA"]["shares"] == pytest.approx(20, abs=1e-3)
    assert result["optimized_portfolio"]["DDD"]["shares"] == pytest.approx(10, abs=1e-3)


def test_buying_with_cash_is_not_turnover(market):
    body = {
        "initial_cash": 10_000,
        "initial_holdings": all_in(market, "DDD"),
        "constraints": {"max_turnover": 0},
    }
    result = optimizer.optimize(body, market)
    assert all(trade["action"] == "buy" for trade in result["trades"])
    assert result["trades"], "the cash should be invested"
    assert result["optimized_portfolio"]["DDD"]["percent_weight"] == pytest.approx(50, abs=0.1)
    assert result["metrics"]["turnover"] == 0


def test_an_untouched_holding_does_not_become_a_dust_sell(market):
    """Regression: weight rounding left a kept position 0.0001 shares short, which the trade list reported as a sell."""
    body = {"initial_cash": 5000, "initial_holdings": {"AAA": 10}, "constraints": {"max_turnover": 0}}
    result = optimizer.optimize(body, market)
    assert all(trade["action"] == "buy" for trade in result["trades"])
    assert result["optimized_portfolio"]["AAA"]["shares"] == 10
    assert sum(p["percent_weight"] for p in result["optimized_portfolio"].values()) == pytest.approx(100, abs=0.05)


def test_tighter_turnover_never_beats_a_looser_one(market):
    holdings = all_in(market, "DDD")
    sharpes = [
        optimizer.optimize({"initial_holdings": holdings, "constraints": {"max_turnover": limit}}, market)["metrics"][
            "sharpe_ratio"
        ]
        for limit in (0.1, 0.3, 0.6, 1.0)
    ]
    assert sharpes == sorted(sharpes)


def test_turnover_limit_of_one_matches_no_limit(market):
    holdings = all_in(market, "DDD")
    plain = optimizer.optimize({"initial_holdings": holdings}, market)
    assert optimizer.optimize({"initial_holdings": holdings, "constraints": {"max_turnover": 1}}, market) == plain


def test_holding_outside_the_universe_is_a_forced_sale(market):
    body = {
        "initial_holdings": {**all_in(market, "DDD", 2_000), **all_in(market, "AAA", 8_000)},
        "universe": ["AAA", "BBB", "CCC"],
        "constraints": {"max_turnover": 0.25},
    }
    result = optimizer.optimize(body, market)
    assert "DDD" not in result["optimized_portfolio"]
    assert sold_fraction(result) <= 0.25 + 1e-3


def test_turnover_below_what_the_universe_forces_names_the_ticker(market):
    message = error_of(
        market,
        {"initial_holdings": all_in(market, "DDD"), "universe": ["AAA", "BBB"], "constraints": {"max_turnover": 0.5}},
    )
    assert message.startswith("Infeasible.")
    assert "DDD" in message and "universe" in message


def test_turnover_below_what_max_weight_forces_says_so(market):
    message = error_of(
        market,
        {"initial_holdings": all_in(market, "DDD"), "constraints": {"max_turnover": 0.1, "max_weight": 0.5}},
    )
    assert message.startswith("Infeasible.")
    assert "max_weight" in message and "50.0%" in message


def test_turnover_limit_with_no_holdings_is_harmless(market):
    plain = optimizer.optimize({"initial_cash": 10_000}, market)
    limited = optimizer.optimize({"initial_cash": 10_000, "constraints": {"max_turnover": 0}}, market)
    assert limited["optimized_portfolio"] == plain["optimized_portfolio"]
    assert limited["metrics"]["turnover"] == 0


@pytest.mark.parametrize(
    "value, fragment",
    [("0.2", "must be a number"), (True, "must be a number"), (-0.1, "between 0 and 1"), (1.5, "between 0 and 1")],
)
def test_rejects_bad_max_turnover(market, value, fragment):
    assert fragment in error_of(market, {"initial_cash": 1000, "constraints": {"max_turnover": value}})


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
