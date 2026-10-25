"""Shared fixtures: an isolated temp database and a small deterministic market."""
import os
import pathlib
import tempfile

# Both must be set before the app import below. The throwaway var/ gets a tiny
# synthetic price cache so importing the app never downloads anything, even on
# a machine that has no real cache yet.
os.environ["WOLVWEALTH_NO_AUTO_REFRESH"] = "1"
_TEST_VAR = pathlib.Path(tempfile.mkdtemp(prefix="wolvwealth-test-var-"))
os.environ["WOLVWEALTH_VAR"] = str(_TEST_VAR)
(_TEST_VAR / "historical_prices.csv").write_text(
    "Date,AAPL,MSFT\n2024-01-02,100.0,200.0\n2024-01-03,101.0,201.0\n"
)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import wolvwealth  # noqa: E402
import wolvwealth.model  # noqa: E402

TICKERS = ["AAA", "BBB", "CCC", "DDD"]


@pytest.fixture
def client(tmp_path):
    """Flask test client backed by a fresh temp database (with the demo seed)."""
    original = wolvwealth.app.config["DATABASE_FILENAME"]
    wolvwealth.app.config["DATABASE_FILENAME"] = tmp_path / "test.sqlite3"
    wolvwealth.model.ensure_db()
    yield wolvwealth.app.test_client()
    wolvwealth.app.config["DATABASE_FILENAME"] = original


@pytest.fixture
def market(monkeypatch):
    """Replace the global market data with 4 synthetic tickers, deterministic seed."""
    state = wolvwealth.state
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2020-01-02", periods=400)
    prices = {
        ticker: np.round(100 * np.exp(np.cumsum(0.0004 * (i + 1) + 0.01 * rng.standard_normal(len(dates)))), 2)
        for i, ticker in enumerate(TICKERS)
    }
    frame = pd.DataFrame(prices, index=dates)
    frame.index.name = "Date"
    monkeypatch.setattr(state, "HISTORICAL_PRICES", frame)
    monkeypatch.setattr(state, "TICKER_UNIVERSE", list(TICKERS))
    return state


@pytest.fixture
def workspace_client(client, market):
    """Client with a workspace 'tester' open and one portfolio with cash and a holding."""
    client.post("/workspaces/create/", data={"name": "tester"})
    client.post("/portfolios/create/", data={"name": "main", "cash": "5000"})
    client.post("/portfolios/holdings/", data={"portfolio_id": _portfolio_id(client), "ticker": "AAA", "shares": "10"})
    return client


def _portfolio_id(client):
    """Fetch the id of tester/main through the JSON API-free DB helpers."""
    with wolvwealth.app.test_request_context():
        portfolio = wolvwealth.portfolios.fetch_portfolio("tester", name="main")
        return portfolio["id"]
