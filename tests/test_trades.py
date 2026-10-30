"""The trades block: what an agent actually sends to a broker."""

import pytest

from wolvwealth.domain import optimizer, trades

PRICES = {"AAA": 10.0, "BBB": 20.0, "CCC": 5.0}


def by_ticker(plan):
    return {move["ticker"]: move for move in plan}


# --- the planner on its own ------------------------------------------------


def test_buys_everything_when_starting_from_cash():
    plan = trades.plan({}, {"AAA": 10, "BBB": 5}, PRICES)
    assert [m["action"] for m in plan] == ["buy", "buy"]
    assert by_ticker(plan)["AAA"] == {
        "ticker": "AAA",
        "action": "buy",
        "shares": 10,
        "price": 10.0,
        "value": 100.0,
    }


def test_sells_a_holding_the_target_drops_entirely():
    plan = trades.plan({"CCC": 8}, {"AAA": 1}, PRICES)
    assert by_ticker(plan)["CCC"] == {
        "ticker": "CCC",
        "action": "sell",
        "shares": 8,
        "price": 5.0,
        "value": 40.0,
    }


def test_reports_only_the_difference_for_a_resized_position():
    plan = trades.plan({"AAA": 10}, {"AAA": 12.5}, PRICES)
    assert by_ticker(plan)["AAA"]["action"] == "buy"
    assert by_ticker(plan)["AAA"]["shares"] == pytest.approx(2.5)


def test_an_unchanged_position_is_not_a_trade():
    assert trades.plan({"AAA": 10}, {"AAA": 10}, PRICES) == []


def test_a_difference_below_the_reported_precision_is_not_a_trade():
    assert trades.plan({"AAA": 10}, {"AAA": 10.000001}, PRICES) == []


def test_sells_come_first_then_descending_value():
    plan = trades.plan({"CCC": 100}, {"AAA": 50, "BBB": 1}, PRICES)
    assert [(m["action"], m["ticker"]) for m in plan] == [
        ("sell", "CCC"),  # sells lead so the cash exists before the buys
        ("buy", "AAA"),  # 500.00
        ("buy", "BBB"),  # 20.00
    ]


# --- as part of an optimize response ---------------------------------------


def test_response_carries_trades_and_the_price_date(market):
    result = optimizer.optimize({"initial_cash": 10_000, "universe": ["AAA", "BBB"]}, market)
    assert result["as_of"] == str(market.as_of)
    assert result["trades"] and all(m["action"] == "buy" for m in result["trades"])


def test_trades_reconcile_with_the_target_portfolio(market):
    held = {"AAA": 20}
    result = optimizer.optimize(
        {"initial_cash": 5_000, "initial_holdings": held, "universe": ["AAA", "BBB", "CCC"]}, market
    )
    final = dict(held)
    for move in result["trades"]:
        final[move["ticker"]] = final.get(move["ticker"], 0) + (
            move["shares"] if move["action"] == "buy" else -move["shares"]
        )
    final = {t: s for t, s in final.items() if round(s, 4) != 0}
    target = {t: row["shares"] for t, row in result["optimized_portfolio"].items()}
    assert final.keys() == target.keys()
    for ticker, shares in target.items():
        assert final[ticker] == pytest.approx(shares, abs=1e-4)


def test_trade_prices_match_the_prices_used_for_the_allocation(market):
    result = optimizer.optimize({"initial_cash": 10_000, "universe": ["AAA", "BBB"]}, market)
    for move in result["trades"]:
        assert move["price"] == pytest.approx(round(market.price(move["ticker"]), 2))


def test_exclude_trades_omits_the_block(market):
    result = optimizer.optimize({"initial_cash": 10_000, "exclude_trades": True}, market)
    assert "trades" not in result
    assert "optimized_portfolio" in result


def test_exclude_trades_must_be_a_boolean(market):
    with pytest.raises(Exception) as excinfo:
        optimizer.optimize({"initial_cash": 10_000, "exclude_trades": "yes"}, market)
    assert "exclude_trades" in str(excinfo.value)


# --- the market endpoint ---------------------------------------------------


def test_market_endpoint_reports_freshness_and_tickers(client, market):
    body = client.get("/api/market/").get_json()
    assert body["as_of"] == str(market.as_of)
    assert body["tradeable_size"] == len(market.tradeable)
    assert body["tickers"] == list(market.tradeable)
    assert "top20" in body["universe_aliases"]


def test_api_index_points_at_the_contract(client):
    body = client.get("/api/").get_json()
    assert "/api/market/" in body and "API.md" in body["contract"]
