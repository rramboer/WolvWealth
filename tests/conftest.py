"""Shared fixtures: a hermetic var/ directory, a temp database, and a small synthetic market.

The app loads market data at import time, so the synthetic price history is
written to a throwaway var/ directory *before* ``wolvwealth`` is imported.
This keeps the tests independent of the 40 MB real price cache and of the
network, which is what lets them run in CI.
"""
import os
import tempfile

import numpy as np
import pandas as pd
import pytest

TICKERS = ["AAA", "BBB", "CCC", "DDD"]


def synthetic_prices() -> pd.DataFrame:
    """Four tickers, 400 trading days, fixed seed: deterministic but non-trivial."""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2020-01-02", periods=400)
    prices = {
        ticker: np.round(100 * np.exp(np.cumsum(0.0004 * (i + 1) + 0.01 * rng.standard_normal(len(dates)))), 2)
        for i, ticker in enumerate(TICKERS)
    }
    frame = pd.DataFrame(prices, index=dates)
    frame.index.name = "Date"
    return frame


_VAR = tempfile.mkdtemp(prefix="wolvwealth-test-var-")
synthetic_prices().to_csv(os.path.join(_VAR, "historical_prices.csv"))
os.environ["WOLVWEALTH_VAR"] = _VAR
os.environ["WOLVWEALTH_NO_AUTO_REFRESH"] = "1"

import wolvwealth  # noqa: E402  (must come after the environment is prepared)
import wolvwealth.model  # noqa: E402


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
    """The app state with the synthetic 4-ticker universe (prices were loaded at import)."""
    state = wolvwealth.state
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
    """Fetch the id of tester/main through the DB helpers."""
    with wolvwealth.app.test_request_context():
        portfolio = wolvwealth.portfolios.fetch_portfolio("tester", name="main")
        return portfolio["id"]
