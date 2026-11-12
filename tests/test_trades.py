"""Tests for the trade list (compute_trades in wolvwealth/api/optimize.py)."""
from wolvwealth.api.optimize import compute_trades

PRICES = {"AAA": 10.0, "BBB": 50.0, "CCC": 200.0, "DDD": 5.0}


def test_sells_come_before_buys_largest_first():
    trades = compute_trades({"AAA": 100, "BBB": 10}, {"CCC": 4, "DDD": 120}, PRICES.get)
    assert [(t["action"], t["ticker"], t["value"]) for t in trades] == [
        ("sell", "AAA", 1000.0),
        ("sell", "BBB", 500.0),
        ("buy", "CCC", 800.0),
        ("buy", "DDD", 600.0),
    ]


def test_dropped_holding_is_sold_in_full_exactly():
    trades = compute_trades({"AAA": 12.123456}, {}, PRICES.get)
    assert trades == [{"action": "sell", "ticker": "AAA", "shares": 12.123456, "price": 10.0, "value": 121.23}]


def test_partial_changes_and_unchanged_holdings():
    trades = compute_trades({"AAA": 10, "BBB": 4, "CCC": 1}, {"AAA": 7.5, "BBB": 6, "CCC": 1}, PRICES.get)
    assert [(t["action"], t["ticker"], t["shares"]) for t in trades] == [("sell", "AAA", 2.5), ("buy", "BBB", 2)]


def test_no_holdings_no_targets():
    assert compute_trades({}, {}, PRICES.get) == []
