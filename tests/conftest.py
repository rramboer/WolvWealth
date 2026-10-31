"""Shared fixtures: a Services object on a temp directory, a Flask client, a synthetic market."""

import numpy as np
import pandas as pd
import pytest

from wolvwealth.config import Settings
from wolvwealth.domain.market import MarketData
from wolvwealth.market import yahoo
from wolvwealth.services.container import Services
from wolvwealth.web import create_app

TICKERS = ["AAA", "BBB", "CCC", "DDD"]


def synthetic_market(seed: int = 42, periods: int = 400) -> MarketData:
    """Four tickers with deterministic random-walk prices."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-02", periods=periods)
    prices = {
        ticker: np.round(100 * np.exp(np.cumsum(0.0004 * (i + 1) + 0.01 * rng.standard_normal(len(dates)))), 2)
        for i, ticker in enumerate(TICKERS)
    }
    frame = pd.DataFrame(prices, index=dates)
    frame.index.name = "Date"
    return MarketData.build(TICKERS, frame)


@pytest.fixture(autouse=True)
def offline_tbill(monkeypatch):
    """No test reaches Yahoo for the T-bill yield; it behaves as if the network were down.

    ``refresh()`` fetches the yield alongside prices, so without this every
    refresh test would make a real request. Tests that need a yield set
    ``service.tbill_downloader`` or call ``set_tbill``.
    """

    def offline():
        raise RuntimeError("offline in tests")

    monkeypatch.setattr(yahoo, "download_tbill_rate", offline)


@pytest.fixture
def services(tmp_path):
    """Services wired to a throwaway var/ with the synthetic market preloaded (never touches disk or network)."""
    settings = Settings(var_dir=tmp_path / "var", auto_refresh=False)
    built = Services.build(settings, log=lambda *_: None)
    built.market.set_data(synthetic_market())
    return built


@pytest.fixture
def market(services):
    """The synthetic MarketData snapshot."""
    return services.market.data


@pytest.fixture
def app(services):
    """Flask app over the test services."""
    return create_app(services)


@pytest.fixture
def client(app):
    """Flask test client backed by a fresh temp database (with the demo seed)."""
    return app.test_client()


@pytest.fixture
def repo(services):
    """A repository on its own committed-per-call connection, for asserting on stored state."""

    class Repo:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                with services.database.connect() as connection:
                    return getattr(services.repository(connection), name)(*args, **kwargs)

            return call

    return Repo()


@pytest.fixture
def workspace_client(client, repo):
    """Client with a workspace 'tester' open and one portfolio with cash and a holding."""
    client.post("/workspaces/create/", data={"name": "tester"})
    client.post("/portfolios/create/", data={"name": "main", "cash": "5000"})
    pid = repo.get_portfolio("tester", name="main").id
    client.post("/portfolios/holdings/", data={"portfolio_id": pid, "ticker": "AAA", "shares": "10"})
    return client
