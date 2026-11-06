"""The trades block: what an agent actually sends to a broker."""

import pytest

from wolvwealth.domain import optimizer, trades

PRICES = {"AAA": 10.0, "BBB": 20.0, "CCC": 5.0}


def by_ticker(plan):
    return {move["ticker"]: move for move in plan}


# --- the planner on its own ------------------------------------------------


def test_rows_buy_from_cash_sell_what_the_target_drops_and_resize_by_the_difference():
    bought = by_ticker(trades.plan({}, {"AAA": 10, "BBB": 5}, PRICES))
    assert bought["AAA"] == {"ticker": "AAA", "action": "buy", "shares": 10, "price": 10.0, "value": 100.0}
    assert bought["BBB"]["action"] == "buy"
    sold = by_ticker(trades.plan({"CCC": 8}, {"AAA": 1}, PRICES))
    assert sold["CCC"] == {"ticker": "CCC", "action": "sell", "shares": 8, "price": 5.0, "value": 40.0}
    resized = by_ticker(trades.plan({"AAA": 10}, {"AAA": 12.5}, PRICES))["AAA"]
    assert resized["action"] == "buy" and resized["shares"] == pytest.approx(2.5)


def test_an_unchanged_or_sub_precision_position_is_not_a_trade():
    assert trades.plan({"AAA": 10}, {"AAA": 10}, PRICES) == []
    assert trades.plan({"AAA": 10}, {"AAA": 10.000001}, PRICES) == []


def test_sells_come_first_then_descending_value():
    plan = trades.plan({"CCC": 100}, {"AAA": 50, "BBB": 1}, PRICES)
    assert [(m["action"], m["ticker"]) for m in plan] == [
        ("sell", "CCC"),  # sells lead so the cash exists before the buys
        ("buy", "AAA"),  # 500.00
        ("buy", "BBB"),  # 20.00
    ]


# --- as part of an optimize response ---------------------------------------


def test_response_trades_carry_the_price_date_and_the_allocation_prices(market):
    result = optimizer.optimize({"initial_cash": 10_000, "universe": ["AAA", "BBB"]}, market)
    assert result["as_of"] == str(market.as_of)
    assert result["trades"] and all(m["action"] == "buy" for m in result["trades"])
    for move in result["trades"]:
        assert move["price"] == pytest.approx(round(market.price(move["ticker"]), 2))


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
