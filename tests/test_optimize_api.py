"""End-to-end tests for POST /api/optimize/."""
import pytest

from conftest import API_KEY
from wolvwealth.api.state import ApplicationState

UNIVERSE = ["AAPL", "MSFT", "JNJ", "NVDA"]  # Small, so each optimization is quick.

NVDA_LOTS = [
    {"shares": 10, "cost_per_share": 15.20, "acquired": "2021-05-03"},
    {"shares": 12, "cost_per_share": 300.00, "acquired": "2024-06-10"},
    {"shares": 8, "cost_per_share": 170.00, "acquired": "2026-10-25"},
]
KO_LOTS = [
    {"shares": 20, "cost_per_share": 62.00, "acquired": "2025-12-01"},
    {"shares": 5, "cost_per_share": 50.00, "acquired": "2019-01-15"},
]
LOTS_REQUEST = {
    "initial_cash": 1000,
    "initial_holdings": {"NVDA": NVDA_LOTS, "KO": KO_LOTS, "XOM": 4},
    "universe": UNIVERSE,
    "prices": {"NVDA": 173.73, "AAPL": 355.63},
    "trade_date": "2026-11-12",
}


@pytest.fixture(scope="module")
def lots_response(optimize):
    status, body = optimize(LOTS_REQUEST)
    assert status == 200, body
    return body


# Backward compatibility


def test_plain_request_has_only_the_original_fields(optimize):
    status, body = optimize({"initial_cash": 1000, "initial_holdings": {"AAPL": 5}, "universe": UNIVERSE})
    assert status == 200
    assert set(body) == {"metrics", "optimized_portfolio", "trades"}
    assert all(set(t) == {"action", "ticker", "shares", "price", "value"} for t in body["trades"])


def test_default_universe_solves(optimize):
    # The full default universe (top 500), which cvxpy's default solver couldn't finish.
    status, body = optimize({"initial_cash": 5000})
    assert status == 200, body
    assert sum(p["percent_weight"] for p in body["optimized_portfolio"].values()) == pytest.approx(100, abs=0.5)


def test_tax_options_alone_do_not_change_the_response(optimize):
    plain = {"initial_cash": 1000, "initial_holdings": {"AAPL": 5}, "universe": UNIVERSE}
    with_options = {**plain, "lot_method": "fifo", "tax_rates": {"short_term": 0.37}, "trade_date": "2026-01-01"}
    assert optimize(with_options) == optimize(plain)


def test_lots_optimize_the_same_as_their_share_count(optimize):
    lots = {"initial_cash": 1000, "initial_holdings": {"NVDA": NVDA_LOTS}, "universe": UNIVERSE}
    plain = {"initial_cash": 1000, "initial_holdings": {"NVDA": 30}, "universe": UNIVERSE}
    _, with_lots = optimize(lots)
    _, without = optimize(plain)
    assert with_lots["optimized_portfolio"] == without["optimized_portfolio"]
    assert with_lots["metrics"] == without["metrics"]


# Trades


def test_trades_take_holdings_to_the_target(lots_response):
    holdings = {"NVDA": 30, "KO": 25, "XOM": 4}
    actions = [t["action"] for t in lots_response["trades"]]
    assert actions == sorted(actions, key=lambda a: a != "sell")  # All sells before any buy.
    for trade in lots_response["trades"]:
        sign = 1 if trade["action"] == "buy" else -1
        holdings[trade["ticker"]] = round(holdings.get(trade["ticker"], 0) + sign * trade["shares"], 6)
    held = {ticker: shares for ticker, shares in holdings.items() if shares}
    assert held == {ticker: p["shares"] for ticker, p in lots_response["optimized_portfolio"].items()}


# Price override


def test_prices_override_values_holdings(lots_response):
    # 1000 cash + 30 NVDA at 173.73 + 25 KO and 4 XOM at the last prices in the data (59.90, 104.85).
    assert lots_response["metrics"]["portfolio_value"] == round(1000 + 30 * 173.73 + 25 * 59.90 + 4 * 104.85, 2)
    nvda = next(t for t in lots_response["trades"] if t["ticker"] == "NVDA")
    assert nvda["price"] == 173.73


def test_price_sources_lists_where_each_price_came_from(lots_response):
    sources = lots_response["price_sources"]
    assert sources["price_data_date"] == "2024-01-30"
    assert "NVDA" in sources["from_request"] and "AAPL" in sources["from_request"]
    assert "KO" in sources["from_price_data"] and "XOM" in sources["from_price_data"]


# Tax lots


def test_each_sell_lists_lots_that_add_up(lots_response):
    for trade in lots_response["trades"]:
        if trade["ticker"] in ("NVDA", "KO"):
            assert trade["action"] == "sell"
            assert sum(lot["shares"] for lot in trade["lots"]) == pytest.approx(trade["shares"])
            assert sum(lot["gain"] for lot in trade["lots"]) == pytest.approx(trade["realized_gain"], abs=0.02)
        else:
            assert "lots" not in trade


def test_min_tax_sells_the_nvda_loss_lot_first(lots_response):
    nvda = next(t for t in lots_response["trades"] if t["ticker"] == "NVDA")
    first = nvda["lots"][0]
    expected = {"acquired": "2024-06-10", "shares": 12.0, "cost_per_share": 300.0, "term": "long", "gain": -1515.24}
    assert first == expected


def test_tax_summary(lots_response):
    summary = lots_response["tax_summary"]
    lots = [lot for t in lots_response["trades"] for lot in t.get("lots", [])]
    short = sum(lot["gain"] for lot in lots if lot["term"] == "short")
    long_ = sum(lot["gain"] for lot in lots if lot["term"] == "long")
    assert summary["short_term_gain"] == pytest.approx(short, abs=0.02)
    assert summary["long_term_gain"] == pytest.approx(long_, abs=0.02)
    assert summary["total_gain"] == pytest.approx(short + long_, abs=0.02)
    assert summary["unknown_basis"] == ["XOM"]
    assert summary["priced_from_price_data"] == ["KO"]
    assert summary["lot_method"] == "min_tax"
    assert summary["trade_date"] == "2026-11-12"
    assert [w["ticker"] for w in summary["wash_sale_warnings"]] == ["NVDA"]
    assert summary["wash_sale_warnings"][0]["recent_purchases"] == ["2026-10-25"]


def test_fifo_sells_the_oldest_lot_first(optimize):
    status, body = optimize({**LOTS_REQUEST, "lot_method": "fifo"})
    assert status == 200
    nvda = next(t for t in body["trades"] if t["ticker"] == "NVDA")
    assert [lot["acquired"] for lot in nvda["lots"]][:2] == ["2021-05-03", "2024-06-10"]


# Tickers with no price on the last day of the data (they stopped trading).


@pytest.fixture
def no_last_price(monkeypatch):
    """Return a function that blanks the last day's price of some tickers, for one test."""
    state = ApplicationState()

    def blank(*tickers):
        history = state.HISTORICAL_PRICES.copy()
        history.loc[history.index[-1], list(tickers)] = float("nan")
        monkeypatch.setattr(state, "HISTORICAL_PRICES", history)

    return blank


def test_holding_with_no_current_price_is_a_400(optimize, no_last_price):
    no_last_price("KO")
    status, body = optimize({"initial_holdings": {"KO": 10}, "universe": UNIVERSE})
    assert status == 400
    assert body["error"]["message"].startswith("No current price for KO in initial_holdings. It last traded on ")


def test_named_universe_ticker_with_no_current_price_is_a_400(optimize, no_last_price):
    no_last_price("KO")
    status, body = optimize({"initial_cash": 1000, "universe": ["AAPL", "MSFT", "ko"]})
    assert status == 400
    assert "No current price for KO in universe" in body["error"]["message"]


def test_alias_leaves_out_tickers_with_no_current_price(optimize, no_last_price):
    no_last_price("AAPL", "MSFT")
    status, body = optimize({"initial_cash": 1000, "universe": ["top20"]})
    assert status == 200
    assert "AAPL" not in body["optimized_portfolio"] and "MSFT" not in body["optimized_portfolio"]


# Errors


@pytest.mark.parametrize(
    "body, message",
    [
        ({"initial_cash": 1, "prices": [1]}, "Invalid prices"),
        ({"initial_cash": 1, "prices": {"NOPE": 1}}, "Invalid symbol in prices"),
        ({"initial_cash": 1, "prices": {"AAPL": 0}}, "Invalid price for AAPL"),
        ({"initial_cash": 1, "prices": {"AAPL": True}}, "Invalid price for AAPL"),
        ({"initial_holdings": {"AAPL": []}}, "at least one lot"),
        ({"initial_holdings": {"AAPL": [1]}}, "Invalid lot 1 for AAPL"),
        ({"initial_holdings": {"AAPL": [{"shares": 1, "acquired": "2020-01-01"}]}}, "Missing cost_per_share"),
        (
            {"initial_holdings": {"AAPL": [{"shares": 0, "cost_per_share": 1, "acquired": "2020-01-01"}]}},
            "shares must be a number greater than 0",
        ),
        (
            {"initial_holdings": {"AAPL": [{"shares": 1, "cost_per_share": -1, "acquired": "2020-01-01"}]}},
            "cost_per_share must be a number, 0 or more",
        ),
        (
            {"initial_holdings": {"AAPL": [{"shares": 1, "cost_per_share": 1, "acquired": "03/01/2021"}]}},
            "acquired must be a date",
        ),
        (
            {
                "trade_date": "2026-01-01",
                "initial_holdings": {"AAPL": [{"shares": 1, "cost_per_share": 1, "acquired": "2026-06-01"}]},
            },
            "is after the trade date",
        ),
        ({"initial_cash": 1, "lot_method": "FIFO"}, "Invalid lot_method"),
        ({"initial_cash": 1, "tax_rates": {"st": 0.3}}, "Invalid tax_rates"),
        ({"initial_cash": 1, "tax_rates": {"long_term": 1.5}}, "Invalid tax_rates"),
        ({"initial_cash": 1, "trade_date": "tomorrow"}, "Invalid trade_date"),
    ],
)
def test_invalid_input_is_a_400(optimize, body, message):
    status, response = optimize(body)
    assert status == 400
    assert message in response["error"]["message"]


# A ticker listed more than once. These send raw JSON, since a Python dict
# can't hold a repeated key.

ONE_LOT = '[{"shares": 3, "cost_per_share": 120, "acquired": "2021-03-01"}]'
SMALL = '"universe": ["AAPL", "MSFT", "JNJ"], "initial_cash": 100'


def post_raw(client, raw):
    response = client.post(
        "/api/optimize/", data=raw, content_type="application/json", headers={"Authorization": API_KEY}
    )
    return response.status_code, response.get_json()


@pytest.mark.parametrize(
    "holdings",
    [
        f'"AAPL": {ONE_LOT}, "AAPL": 5',  # The JSON parser alone would keep only the 5.
        f'"AAPL": 5, "AAPL": {ONE_LOT}',
        f'"AAPL": {ONE_LOT}, "aapl": 5',
        f'"AAPL": {ONE_LOT}, "aapl": {ONE_LOT}',
    ],
)
def test_repeated_ticker_with_lots_is_a_400(client, holdings):
    status, body = post_raw(client, '{"initial_holdings": {%s}, %s}' % (holdings, SMALL))
    assert status == 400
    assert "AAPL is listed more than once" in body["error"]["message"]


def test_repeated_share_counts_still_keep_the_last(client):
    assert post_raw(client, '{"initial_holdings": {"AAPL": 2, "aapl": 5}, %s}' % SMALL) == post_raw(
        client, '{"initial_holdings": {"AAPL": 5}, %s}' % SMALL
    )


@pytest.mark.parametrize("prices", ['"AAPL": 355.63, "AAPL": 188', '"AAPL": 355.63, "aapl": 355.63'])
def test_repeated_ticker_in_prices_is_a_400(client, prices):
    status, body = post_raw(client, '{"prices": {%s}, %s}' % (prices, SMALL))
    assert status == 400
    assert "AAPL is listed more than once" in body["error"]["message"]
