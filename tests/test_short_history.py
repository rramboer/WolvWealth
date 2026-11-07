"""Recently listed tickers: tradeable, but with too little history for their statistics to mean anything.

The estimators annualize whatever history exists, so twenty days of a
rising new listing used to show an expected return in the thousands of
percent at almost no measured risk, and the solver put the maximum weight
there. A minimum history (a trading year by default) keeps such names out
of every universe unless a request lowers it on purpose.
"""

import pathlib

import numpy as np
import pytest

from conftest import synthetic_market
from wolvwealth import __main__ as cli
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


def test_a_young_holding_is_kept_as_it_is_and_the_rest_is_optimized_around_it(with_young):
    """Holding it is a fact, not a choice; the optimizer has nothing to size it by, so it leaves it alone."""
    result = optimizer.optimize({"initial_cash": 10_000, "initial_holdings": {"DDD": 10}}, with_young)
    total = 10_000 + 10 * with_young.price("DDD")
    kept = result["optimized_portfolio"]["DDD"]
    assert kept["shares"] == 10 and kept["value"] == pytest.approx(10 * with_young.price("DDD"), abs=0.01)
    assert kept["percent_weight"] == pytest.approx(100 * 10 * with_young.price("DDD") / total, abs=0.01)
    assert sum(row["percent_weight"] for row in result["optimized_portfolio"].values()) == pytest.approx(100, abs=0.05)
    assert not any(trade["ticker"] == "DDD" for trade in result["trades"]), "a kept position produces no trade"
    assert result["unoptimized"]["positions"] == {
        "DDD": {"history_days": YOUNG_DAYS, "percent_weight": kept["percent_weight"]}
    }
    note = result["unoptimized"]["note"]
    assert "Kept at their current size" in note and f"min_history_days of {YOUNG_DAYS} or less" in note
    assert (
        result["metrics"]["num_assets"] == 4 and result["metrics"]["expected_annual_return"] < 1
    ), "DDD's noise is not in the metrics"
    assert "unoptimized" not in optimizer.optimize({"initial_cash": 10_000}, with_young)


def test_the_users_reproducer_a_young_holding_with_cash_and_no_turnover_sells_nothing(with_young):
    """Holding a young stock plus cash at max_turnover 0 used to refuse (or, before that, sell the stock)."""
    body = {"initial_cash": 10_000, "initial_holdings": {"DDD": 10}, "constraints": {"max_turnover": 0}}
    result = optimizer.optimize(body, with_young)
    assert all(trade["action"] == "buy" for trade in result["trades"]) and result["metrics"]["turnover"] == 0
    assert result["optimized_portfolio"]["DDD"]["shares"] == 10
    assert sum(trade["value"] for trade in result["trades"]) == pytest.approx(10_000, rel=1e-3), "the cash is deployed"


def test_naming_a_held_young_ticker_in_the_universe_keeps_it_rather_than_refusing(with_young):
    """The browser, CLI and MCP flows add held tickers to the universe; that must not make them fail."""
    body = {"initial_cash": 1000, "initial_holdings": {"DDD": 1}, "universe": ["AAA", "DDD"]}
    request = optimizer.parse_request(body, with_young)
    assert request.universe == ["AAA"] and list(request.unoptimized) == ["DDD"]
    assert with_young.expand_universe(["DDD"], held=["DDD"]) == []


def test_a_portfolio_that_is_only_a_young_holding_comes_back_unchanged(with_young):
    result = optimizer.optimize({"initial_cash": 0, "initial_holdings": {"DDD": 10}}, with_young)
    assert result["optimized_portfolio"] == {
        "DDD": {"shares": 10, "value": pytest.approx(10 * with_young.price("DDD"), abs=0.01), "percent_weight": 100.0}
    }
    assert result["trades"] == [] and result["metrics"]["num_assets"] == 1


def test_lowering_the_minimum_makes_a_held_young_ticker_ordinary_again(with_young):
    """With the override it is optimized when the universe names it and sold when it does not."""
    held = {"initial_cash": 1000, "initial_holdings": {"DDD": 1}, "min_history_days": YOUNG_DAYS}
    optimized = optimizer.optimize({**held, "universe": ["AAA", "DDD"]}, with_young)
    assert "unoptimized" not in optimized and optimized["optimized_portfolio"]["DDD"]["percent_weight"] > 50
    sold = optimizer.optimize({**held, "universe": ["AAA"]}, with_young)
    assert "DDD" not in sold["optimized_portfolio"] and sold["trades"][0] == {
        **sold["trades"][0],
        "ticker": "DDD",
        "action": "sell",
    }


@pytest.mark.parametrize(
    "constraints, fields, fragment",
    [
        (
            {"max_weight": 0.5},
            ["constraints.max_weight", "initial_holdings", "min_history_days"],
            "above max_weight 0.5",
        ),
        (
            {"max_positions": 1},
            ["constraints.max_positions", "initial_holdings", "min_history_days"],
            "used up by the 1",
        ),
    ],
)
def test_a_kept_position_that_breaks_a_constraint_is_refused_with_both_ways_out(
    with_young, constraints, fields, fragment
):
    """It cannot be sold down or dropped, so the constraint must give or the minimum must."""
    body = {"initial_cash": 1000, "initial_holdings": {"DDD": 10}, "constraints": constraints}  # DDD is ~55%
    with pytest.raises(ValidationError) as excinfo:
        optimizer.optimize(body, with_young)
    assert fragment in excinfo.value.message and f"min_history_days of {YOUNG_DAYS} or less" in excinfo.value.message
    assert excinfo.value.details["conflicts"][0]["fields"] == fields
    assert "unoptimized" not in optimizer.optimize({**body, "min_history_days": YOUNG_DAYS}, with_young)


def test_a_cash_reserve_cannot_be_funded_by_money_kept_in_a_young_holding(with_young):
    """Regression: $6000 kept in a young stock plus $4000 cash with cash_reserve 0.5 reported a portfolio
    of 60% stock and 50% cash -- 110% of the money. Only $4000 of cash exists, and the kept position
    cannot be sold to raise more, so the request must be refused with both ways out.
    """
    shares = 6000 / with_young.price("DDD")
    body = {"initial_cash": 4000, "initial_holdings": {"DDD": shares}, "constraints": {"cash_reserve": 0.5}}
    with pytest.raises(ValidationError) as excinfo:
        optimizer.optimize(body, with_young)
    message = excinfo.value.message
    assert "cash_reserve 0.5" in message and "DDD is 60.0%" in message and "at most 40.0% for cash" in message
    assert "Lower cash_reserve to at most 0.4" in message and f"min_history_days of {YOUNG_DAYS} or less" in message
    assert excinfo.value.details["conflicts"][0]["fields"] == [
        "constraints.cash_reserve",
        "initial_holdings",
        "min_history_days",
    ]


def test_the_reserve_the_kept_position_leaves_room_for_is_exactly_the_cash_held(with_young):
    """The boundary of the regression above: a 40% reserve is all $4000 of real cash, nothing invented."""
    shares = 6000 / with_young.price("DDD")
    body = {"initial_cash": 4000, "initial_holdings": {"DDD": shares}, "constraints": {"cash_reserve": 0.4}}
    result = optimizer.optimize(body, with_young)
    assert result["cash"] == {"value": 4000.0, "percent_weight": 40.0}
    assert result["optimized_portfolio"] == {
        "DDD": {"shares": shares, "value": pytest.approx(6000, abs=0.01), "percent_weight": pytest.approx(60, abs=0.01)}
    }
    assert result["trades"] == [], "every dollar is accounted for without a single trade"
    weights = sum(row["percent_weight"] for row in result["optimized_portfolio"].values())
    assert weights + result["cash"]["percent_weight"] == pytest.approx(100, abs=0.05)


def test_a_kept_position_is_not_counted_as_cash_by_the_turnover_floor(with_young):
    """Regression: held cash was computed as one minus the *optimizable* holdings, so a kept position
    looked like cash, and a reserve that in fact required selling slipped past the named check into
    an opaque solver failure.
    """
    holdings = {"DDD": 6000 / with_young.price("DDD"), "AAA": 3000 / with_young.price("AAA")}
    body = {
        "initial_cash": 1000,
        "initial_holdings": holdings,
        "constraints": {"cash_reserve": 0.2, "max_turnover": 0},
    }
    with pytest.raises(ValidationError, match="cash_reserve 0.2 requires 10.0% more cash than is held") as excinfo:
        optimizer.optimize(body, with_young)
    assert "max_turnover 0 is below the 10.0%" in excinfo.value.message
    enough_room = optimizer.optimize({**body, "constraints": {"cash_reserve": 0.2, "max_turnover": 0.15}}, with_young)
    assert enough_room["metrics"]["turnover"] <= 0.15 + 1e-3
    assert enough_room["optimized_portfolio"]["DDD"]["shares"] == holdings["DDD"], "the sale never touches DDD"


def test_constraints_are_honored_around_a_kept_position(with_young):
    body = {
        "initial_cash": 10_000,
        "initial_holdings": {"DDD": 10},  # ~10.8%
        "constraints": {"max_positions": 3, "max_weight": 0.5, "cash_reserve": 0.1},
    }
    result = optimizer.optimize(body, with_young)
    weights = {ticker: row["percent_weight"] for ticker, row in result["optimized_portfolio"].items()}
    assert len(weights) == 3 and "DDD" in weights, "the kept position counts toward max_positions"
    assert max(weights.values()) <= 50.05
    assert sum(weights.values()) == pytest.approx(90, abs=0.1) and result["cash"]["percent_weight"] == 10


def test_a_kept_position_s_weight_is_a_memo_key(with_young):
    """Different kept weights mean different room for the solver, so they cannot share a solve."""
    optimizer.optimize({"initial_cash": 10_000, "initial_holdings": {"DDD": 10}}, with_young)
    assert len(with_young.solutions) == 2
    optimizer.optimize({"initial_cash": 10_000, "initial_holdings": {"DDD": 10}}, with_young)
    assert len(with_young.solutions) == 2
    optimizer.optimize({"initial_cash": 10_000, "initial_holdings": {"DDD": 20}}, with_young)
    assert len(with_young.solutions) == 4


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


def test_the_held_flows_keep_a_young_holding_and_say_so(with_young, repo, capsys):
    """The CLI and MCP add held names to the universe; a young one is kept, and the report says why."""
    payload = {"initial_cash": 1000, "initial_holdings": {"DDD": 10}}
    result, universe, _ = optimize.optimize_keeping_held(payload, with_young, repo)
    assert universe == ["top500", "DDD"] and result["optimized_portfolio"]["DDD"]["shares"] == 10
    report = cli.render_report(result, universe)
    assert "Kept as held, not optimized: DDD (20 days of history)" in report and "Kept at their current size" in report
    assert optimize.payload_for_portfolio(_Stub(), "top20", min_history_days=30)["min_history_days"] == 30
    assert "min_history_days" not in optimize.payload_for_portfolio(_Stub(), "top20")


def test_the_browser_shows_a_kept_young_holding(workspace_client, services, with_young, repo):
    services.market.set_data(with_young)
    pid = repo.get_portfolio("tester", name="main").id
    workspace_client.post("/portfolios/holdings/", data={"portfolio_id": pid, "ticker": "DDD", "shares": "3"})
    page = workspace_client.post("/optimizer/run/", data={"portfolio_id": pid, "universe": "top20"}).data.decode()
    assert "Kept as held, not optimized" in page and "DDD (20" in page and "days of history" in page


def test_the_api_documents_the_unoptimized_block():
    text = (pathlib.Path(__file__).resolve().parent.parent / "API.md").read_text(encoding="utf-8")
    assert "`unoptimized`" in text and "`history_days`" in text


class _Stub:
    """Just enough of a Portfolio for payload_for_portfolio."""

    cash = 100.0
    holdings = {}
