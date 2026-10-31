"""Memoization of the max-Sharpe solve: what reuses a solution, what must not."""

import numpy as np
import pytest

from conftest import synthetic_market
from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import ValidationError
from wolvwealth.domain.market import MarketData

BASE = {"initial_cash": 10_000, "universe": ["AAA", "BBB", "CCC"]}


@pytest.fixture
def solves(monkeypatch):
    """A list that gains an entry every time the request reaches the solver."""
    calls = []
    original = optimizer.EfficientFrontier

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(optimizer, "EfficientFrontier", counted)
    return calls


@pytest.fixture
def slices(monkeypatch):
    """A list that gains an entry every time statistics are sliced out of the snapshot."""
    calls = []
    original = MarketData.stats_for

    def counted(self, tickers):
        calls.append(1)
        return original(self, tickers)

    monkeypatch.setattr(MarketData, "stats_for", counted)
    return calls


def test_identical_request_is_served_without_solving_again(market, solves):
    first = optimizer.optimize(BASE, market)
    assert optimizer.optimize(BASE, market) == first
    assert len(solves) == 1


def test_repeated_request_does_not_reslice_the_statistics(market, slices):
    optimizer.optimize(BASE, market)
    before = len(slices)
    optimizer.optimize(BASE, market)
    assert len(slices) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"initial_cash": 25_000},
        {"initial_holdings": {"AAA": 5}},
        {"constraints": {"max_positions": 2}},
        {"exclude_metrics": True},
    ],
    ids=["cash", "holdings", "max_positions", "exclude_metrics"],
)
def test_fields_that_only_scale_or_truncate_reuse_the_same_solve(market, solves, changes):
    optimizer.optimize(BASE, market)
    result = optimizer.optimize({**BASE, **changes}, market)
    assert result["optimized_portfolio"]
    assert len(solves) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"universe": ["AAA", "BBB"]},
        {"constraints": {"max_weight": 0.5}},
        {"constraints": {"min_universal_weight": 0.1}},
        {"initial_holdings": {"AAA": 5}, "constraints": {"max_turnover": 0.2}},
        {"risk_free_rate": 0.01},
    ],
    ids=["universe", "max_weight", "min_universal_weight", "max_turnover", "risk_free_rate"],
)
def test_fields_that_reach_the_solver_get_their_own_solve(market, solves, changes):
    optimizer.optimize(BASE, market)
    optimizer.optimize({**BASE, **changes}, market)
    assert len(solves) == 2


def test_a_portfolio_rate_and_an_equal_request_rate_share_a_solve(market, solves):
    """The source of the rate is bookkeeping for the response; only its value reaches the solver."""
    from wolvwealth.domain.models import Portfolio
    from wolvwealth.services import optimize as service

    stored = Portfolio(id=1, workspace="w", name="p", cash=10_000, risk_free_rate=0.03)
    first = service.optimize(BASE, market, portfolio=stored)
    second = service.optimize({**BASE, "risk_free_rate": 0.03}, market)
    assert len(solves) == 1
    assert first["optimized_portfolio"] == second["optimized_portfolio"]
    assert first["risk_free_rate"]["source"] == "portfolio" and second["risk_free_rate"]["source"] == "request"


def test_holdings_join_the_key_only_under_a_turnover_limit(market, solves):
    """A turnover limit makes the held weights a solver input, so different holdings must not share a solve."""
    limited = {**BASE, "constraints": {"max_turnover": 0.2}}
    optimizer.optimize({**limited, "initial_holdings": {"AAA": 5}}, market)
    optimizer.optimize({**limited, "initial_holdings": {"AAA": 50}}, market)
    assert len(solves) == 2
    optimizer.optimize({**limited, "initial_holdings": {"AAA": 50}}, market)
    assert len(solves) == 2


def test_a_turnover_limit_of_one_shares_the_unconstrained_solve(market, solves):
    optimizer.optimize(BASE, market)
    optimizer.optimize({**BASE, "initial_holdings": {"AAA": 5}, "constraints": {"max_turnover": 1}}, market)
    assert len(solves) == 1


def test_cached_weights_survive_a_max_positions_truncation(market):
    """The truncating request must not corrupt the entry the plain request reuses."""
    full = optimizer.optimize(BASE, market)
    truncated = optimizer.optimize({**BASE, "constraints": {"max_positions": 1}}, market)
    assert len(truncated["optimized_portfolio"]) == 1
    assert optimizer.optimize(BASE, market) == full


def test_metrics_follow_the_truncated_portfolio_not_the_cached_full_one(market):
    """max_positions is in the metrics key, so a truncated portfolio reports its own numbers."""
    full = optimizer.optimize(BASE, market)
    truncated = optimizer.optimize({**BASE, "constraints": {"max_positions": 1}}, market)
    assert truncated["metrics"]["num_assets"] == 1
    assert truncated["metrics"]["annual_volatility"] != full["metrics"]["annual_volatility"]


def test_a_refreshed_snapshot_never_serves_the_old_one_s_weights(market, solves):
    optimizer.optimize(BASE, market)
    # Same universe and constraints, different prices: a new snapshot, so a new cache.
    newer = MarketData.build(market.universe, synthetic_market(seed=7).prices)
    assert len(newer.solutions) == 0
    assert optimizer.optimize(BASE, newer) != optimizer.optimize(BASE, market)
    assert len(solves) == 2


def test_truncated_backtest_snapshots_have_their_own_cache(market, solves):
    optimizer.optimize(BASE, market)
    past = market.truncated(market.prices.index[200])
    assert len(past.solutions) == 0
    optimizer.optimize(BASE, past)
    assert len(solves) == 2


def test_memoized_result_matches_an_unmemoized_one(market):
    body = {**BASE, "constraints": {"max_positions": 2}}
    fresh = MarketData.build(market.universe, market.prices, market.stats)
    assert optimizer.optimize(body, market) == optimizer.optimize(body, fresh)


def test_a_failed_solve_is_not_remembered(market, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("solver unavailable")

    monkeypatch.setattr(optimizer, "EfficientFrontier", explode)
    with pytest.raises(ValidationError):
        optimizer.optimize(BASE, market)
    assert len(market.solutions) == 0
    monkeypatch.undo()
    assert optimizer.optimize(BASE, market)["optimized_portfolio"]


def test_latest_prices_skip_delisted_tickers(market):
    prices = market.prices.copy()
    prices.loc[prices.index[-1], "BBB"] = np.nan
    snapshot = MarketData.build(market.universe, prices)
    assert not snapshot.has_price("BBB")
    assert snapshot.has_price("AAA")
    with pytest.raises(KeyError):
        snapshot.price("BBB")
    assert snapshot.price("AAA") == pytest.approx(float(prices["AAA"].iloc[-1]))
    assert snapshot.value({"AAA": 2}) == pytest.approx(2 * float(prices["AAA"].iloc[-1]))
