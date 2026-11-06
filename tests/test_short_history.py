"""Recently listed tickers: tradeable, but with too little history for their statistics to mean anything.

The estimators annualize whatever history exists, so twenty days of a
rising new listing used to show an expected return in the thousands of
percent at almost no measured risk, and the solver put the maximum weight
there. A minimum history (a trading year by default) keeps such names out
of every universe unless a request lowers it on purpose.
"""

import numpy as np
import pytest

from conftest import synthetic_market
from wolvwealth.domain import optimizer
from wolvwealth.domain.errors import DataError, ValidationError
from wolvwealth.domain.market import MIN_HISTORY_DAYS, MarketData
from wolvwealth.services import market_data, optimize

YOUNG_DAYS = 20


@pytest.fixture
def with_young():
    """A market whose DDD listed 20 trading days before the data ends and rose 1% every day since."""
    base = synthetic_market()
    prices = base.prices.copy()
    column = prices.columns.get_loc("DDD")
    prices.iloc[:-YOUNG_DAYS, column] = np.nan
    prices.iloc[-YOUNG_DAYS:, column] = 100 * 1.01 ** np.arange(YOUNG_DAYS)
    return MarketData.build(base.universe, prices)


def test_the_bug_the_rule_prevents(with_young):
    """Without the rule, the young ticker's extrapolated return dwarfs everything and takes the portfolio."""
    mu, _ = with_young.stats_for(["AAA", "DDD"])
    assert mu["DDD"] > 10 > mu["AAA"], "1% a day for 20 days annualizes to over 1000%"
    result = optimizer.optimize({"initial_cash": 10_000, "min_history_days": YOUNG_DAYS}, with_young)
    assert result["optimized_portfolio"]["DDD"]["percent_weight"] > 95


def test_a_young_ticker_is_tradeable_but_not_seasoned(with_young):
    assert MIN_HISTORY_DAYS == 252
    assert with_young.history_days == {"AAA": 400, "BBB": 400, "CCC": 400, "DDD": YOUNG_DAYS}
    assert "DDD" in with_young.tradeable and with_young.has_price("DDD")
    assert with_young.seasoned() == ("AAA", "BBB", "CCC")
    assert with_young.short_history() == {"DDD": YOUNG_DAYS}
    assert with_young.top(4) == ["AAA", "BBB", "CCC"], "aliases skip it rather than fail"
    assert with_young.top(4, min_history_days=YOUNG_DAYS) == ["AAA", "BBB", "CCC", "DDD"]
    assert with_young.short_history(YOUNG_DAYS) == {}


def test_default_request_leaves_the_young_ticker_out(with_young):
    assert optimizer.parse_request({"initial_cash": 1000}, with_young).universe == ["AAA", "BBB", "CCC"]
    assert optimizer.parse_request({"initial_cash": 1000, "universe": ["top500"]}, with_young).universe == [
        "AAA",
        "BBB",
        "CCC",
    ]
    result = optimizer.optimize({"initial_cash": 10_000}, with_young)
    assert "DDD" not in result["optimized_portfolio"]
    assert result["metrics"]["expected_annual_return"] < 1


def test_naming_a_young_ticker_is_refused_with_the_count_and_the_override(with_young):
    with pytest.raises(ValidationError) as excinfo:
        optimizer.optimize({"initial_cash": 1000, "universe": ["AAA", "DDD"]}, with_young)
    message = excinfo.value.message
    assert "DDD" in message and f"{YOUNG_DAYS} day(s)" in message and "at least 252" in message
    assert f"min_history_days of {YOUNG_DAYS} or less" in message


def test_lowering_the_minimum_admits_it_by_name_and_by_alias(with_young):
    by_name = optimizer.parse_request(
        {"initial_cash": 1000, "universe": ["AAA", "DDD"], "min_history_days": YOUNG_DAYS}, with_young
    )
    by_alias = optimizer.parse_request({"initial_cash": 1000, "min_history_days": YOUNG_DAYS}, with_young)
    assert by_name.universe == ["AAA", "DDD"] and by_alias.universe == ["AAA", "BBB", "CCC", "DDD"]
    with pytest.raises(ValidationError, match="DDD has only"):
        optimizer.parse_request(
            {"initial_cash": 1000, "universe": ["DDD"], "min_history_days": YOUNG_DAYS + 1}, with_young
        )


def test_a_young_ticker_may_still_be_held_and_valued(with_young):
    """Holding it is a fact, not a choice; it is valued, and sold as a name outside the universe."""
    result = optimizer.optimize({"initial_cash": 0, "initial_holdings": {"DDD": 10}}, with_young)
    assert result["metrics"]["portfolio_value"] == pytest.approx(10 * with_young.price("DDD"), abs=0.01)
    assert "DDD" not in result["optimized_portfolio"]
    assert any(trade["ticker"] == "DDD" and trade["action"] == "sell" for trade in result["trades"])


@pytest.mark.parametrize(
    "value, fragment",
    [("252", "must be an integer"), (2.5, "must be an integer"), (True, "must be an integer"), (1, "at least 2")],
)
def test_min_history_days_is_validated(with_young, value, fragment):
    with pytest.raises(ValidationError, match=fragment):
        optimizer.optimize({"initial_cash": 1000, "min_history_days": value}, with_young)


def test_a_minimum_nothing_meets_is_explained(with_young):
    with pytest.raises(ValidationError, match="Invalid min_history_days. No tradeable ticker has 401 days") as excinfo:
        optimizer.optimize({"initial_cash": 1000, "min_history_days": 401}, with_young)
    assert "longest in the cache is 400 days" in excinfo.value.message
    short_cache = MarketData.build(with_young.universe, with_young.prices.iloc[-100:])
    with pytest.raises(DataError, match="No tradeable ticker has 252 days"):
        optimizer.optimize({"initial_cash": 1000}, short_cache)
    with pytest.raises(DataError, match="refresh-data --full"):
        optimizer.optimize({"initial_cash": 1000}, MarketData.build(["AAA"], with_young.prices.iloc[-100:] * np.nan))


def test_the_minimum_is_not_a_memo_key_because_the_universe_already_is(with_young):
    """Two requests resolving to the same tickers share a solve; one admitting DDD does not."""
    optimizer.optimize({"initial_cash": 1000}, with_young)
    assert len(with_young.solutions) == 2  # weights + metrics
    optimizer.optimize({"initial_cash": 1000, "min_history_days": 300}, with_young)
    assert len(with_young.solutions) == 2
    optimizer.optimize({"initial_cash": 1000, "min_history_days": YOUNG_DAYS}, with_young)
    assert len(with_young.solutions) == 4


def test_market_report_names_the_excluded_tickers(client, services, with_young):
    body = client.get("/api/market/").get_json()
    assert body["min_history_days"] == MIN_HISTORY_DAYS and "short_history" not in body
    services.market.set_data(with_young)
    body = client.get("/api/market/").get_json()
    assert body["short_history"] == {"DDD": YOUNG_DAYS}
    assert "DDD" in body["tickers"], "still tradeable: it can be held"
    assert market_data.classify_tickers(with_young, ["aaa", "ddd", "zzz"]) == {
        "tradeable": ["AAA"],
        "short_history": ["DDD"],
        "no_current_price": [],
        "unknown": ["ZZZ"],
    }


def test_api_refuses_a_young_ticker_by_name_and_accepts_the_override(client, services, with_young):
    services.market.set_data(with_young)
    response = client.post("/api/optimize/", json={"initial_cash": 1000, "universe": ["DDD"]})
    assert response.status_code == 400 and "DDD has only 20 day(s)" in response.get_json()["error"]["message"]
    response = client.post(
        "/api/optimize/", json={"initial_cash": 1000, "universe": ["DDD"], "min_history_days": YOUNG_DAYS}
    )
    assert response.status_code == 200 and "DDD" in response.get_json()["optimized_portfolio"]


def test_the_browser_form_passes_the_override_through(workspace_client, services, with_young, repo):
    services.market.set_data(with_young)
    pid = repo.get_portfolio("tester", name="main").id
    page = workspace_client.post("/optimizer/run/", data={"portfolio_id": pid, "universe": "top20"})
    assert page.status_code == 200 and b"DDD" not in page.data
    page = workspace_client.post(
        "/optimizer/run/", data={"portfolio_id": pid, "universe": "top20", "min_history_days": str(YOUNG_DAYS)}
    )
    assert page.status_code == 200 and b"DDD" in page.data
    page = workspace_client.post(
        "/optimizer/run/", data={"portfolio_id": pid, "universe": "top20", "min_history_days": "lots"}
    )
    assert b"whole number of trading days" in page.data


def test_the_held_flows_refuse_a_young_holding_rather_than_sell_it_quietly(with_young, repo):
    """The CLI and MCP add held names to the universe; a young one cannot be added, and the message says why."""
    payload = {"initial_cash": 0, "initial_holdings": {"DDD": 10}}
    with pytest.raises(ValidationError, match="DDD has only 20 day"):
        optimize.optimize_keeping_held(payload, with_young, repo)
    result, universe, _ = optimize.optimize_keeping_held(dict(payload, min_history_days=YOUNG_DAYS), with_young, repo)
    assert universe == ["top500", "DDD"] and "DDD" in result["optimized_portfolio"]
    assert optimize.payload_for_portfolio(_Stub(), "top20", min_history_days=30)["min_history_days"] == 30
    assert "min_history_days" not in optimize.payload_for_portfolio(_Stub(), "top20")


class _Stub:
    """Just enough of a Portfolio for payload_for_portfolio."""

    cash = 100.0
    holdings = {}
