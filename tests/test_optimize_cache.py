"""Memoization of the max-Sharpe solve: what reuses a solution, what must not."""

import numpy as np
import pytest

from conftest import synthetic_market
from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import ValidationError
from wolvwealth.domain.market import MAX_STALE_DAYS, MarketData

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


def test_an_identical_request_is_served_without_solving_or_reslicing_again(market, solves, slices):
    first = optimizer.optimize(BASE, market)
    sliced = len(slices)
    assert optimizer.optimize(BASE, market) == first
    assert len(solves) == 1 and len(slices) == sliced


@pytest.mark.parametrize(
    "changes",
    [{"initial_cash": 25_000}, {"initial_holdings": {"AAA": 5}}, {"exclude_metrics": True}],
    ids=["cash", "holdings", "exclude_metrics"],
)
def test_fields_that_only_scale_the_result_reuse_the_same_solve(market, solves, changes):
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
        # The reserve rescales the solver's weight bounds, so a cached
        # fully-invested answer must not be served for a reserved request.
        {"constraints": {"cash_reserve": 0.25}},
    ],
    ids=["universe", "max_weight", "min_universal_weight", "max_turnover", "risk_free_rate", "cash_reserve"],
)
def test_fields_that_reach_the_solver_get_their_own_solve(market, solves, changes):
    optimizer.optimize(BASE, market)
    optimizer.optimize({**BASE, **changes}, market)
    assert len(solves) == 2


def test_max_positions_solves_on_its_own_and_leaves_the_full_entry_intact(market, solves):
    """It selects a subset and re-solves over it, so it cannot share the full solve.

    Regression: while ``max_positions`` only truncated the solved weights it
    was deliberately absent from the cache key. Now that it changes what the
    solver is asked, a cached full-universe answer must not be served for it,
    its metrics must describe the capped portfolio, and the capped solve must
    not corrupt the entry the plain request reuses.
    """
    full = optimizer.optimize(BASE, market)
    capped = optimizer.optimize({**BASE, "constraints": {"max_positions": 1}}, market)
    assert len(capped["optimized_portfolio"]) == 1 and len(solves) > 1
    before = len(solves)
    assert optimizer.optimize({**BASE, "constraints": {"max_positions": 1}}, market) == capped
    assert len(solves) == before, "the capped request is itself memoized"
    assert capped["metrics"]["num_assets"] == 1
    assert capped["metrics"]["annual_volatility"] != full["metrics"]["annual_volatility"]
    assert optimizer.optimize(BASE, market) == full and len(solves) == before


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


def test_a_refreshed_snapshot_never_serves_the_old_one_s_weights(market, solves):
    optimizer.optimize(BASE, market)
    # Same universe and constraints, different prices: a new snapshot, so a new cache.
    newer = MarketData.build(market.universe, synthetic_market(seed=7).prices)
    assert len(newer.solutions) == 0
    assert optimizer.optimize(BASE, newer) != optimizer.optimize(BASE, market)
    assert len(solves) == 2


def test_truncated_backtest_snapshots_have_their_own_cache(market, solves):
    optimizer.optimize(BASE, market)
    past = market.truncated(market.prices.index[300])  # still a year of history, so the universe is unchanged
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


def test_a_throttled_day_carries_the_last_close_forward_and_says_so(market):
    """One missing day must not unprice a ticker: that is the whole main path (see MAX_STALE_DAYS)."""
    prices = market.prices.copy()
    prices.loc[prices.index[-1], "BBB"] = np.nan
    snapshot = MarketData.build(market.universe, prices)
    carried = float(prices["BBB"].iloc[-2])
    assert snapshot.has_price("BBB") and snapshot.price("BBB") == pytest.approx(carried)
    assert snapshot.stale_prices["BBB"] == {
        "as_of": str(prices.index[-2].date()),
        "trading_days_stale": 1,
    }
    assert "AAA" not in snapshot.stale_prices, "a ticker that printed today is not reported as stale"
    assert snapshot.value({"BBB": 2}) == pytest.approx(2 * carried)


def test_latest_prices_skip_tickers_quoted_nowhere_in_the_window(market):
    prices = market.prices.copy()
    prices.loc[prices.index[-(MAX_STALE_DAYS + 1) :], "BBB"] = np.nan
    snapshot = MarketData.build(market.universe, prices)
    assert not snapshot.has_price("BBB")
    assert snapshot.has_price("AAA")
    with pytest.raises(KeyError):
        snapshot.price("BBB")
    assert snapshot.price("AAA") == pytest.approx(float(prices["AAA"].iloc[-1]))
    # Valuing a position someone holds still works, at the last close there was.
    last = float(prices["BBB"].dropna().iloc[-1])
    assert snapshot.can_value("BBB") and snapshot.valuation_price("BBB") == pytest.approx(last)
    assert snapshot.value({"AAA": 2}) == pytest.approx(2 * float(prices["AAA"].iloc[-1]))
