"""Shared fixtures: a Services object on a temp directory, a Flask client, a synthetic market."""

import ipaddress
import socket

import numpy as np
import pandas as pd
import pytest
import yfinance
from curl_cffi import requests as curl_requests

from wolvwealth.config import Settings
from wolvwealth.domain.market import MarketData
from wolvwealth.market import yahoo
from wolvwealth.services.container import Services
from wolvwealth.web import create_app

TICKERS = ["AAA", "BBB", "CCC", "DDD"]
#: A T-bill quote date the freshness rule accepts on any day the suite runs.
RECENT_DAY = (pd.Timestamp.now().normalize() - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
#: The real adapter, captured before ``offline_tbill`` stubs it out, for the one test of the adapter itself.
REAL_DOWNLOAD_TBILL_RATE = yahoo.download_tbill_rate


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


def dated_to_today(market: MarketData) -> MarketData:
    """The same prices re-dated so the newest row is today: a cache that is fresh, not years old."""
    frame = market.prices.copy()
    frame.index = pd.date_range(end=pd.Timestamp.now().normalize(), periods=len(frame))
    frame.index.name = "Date"
    return MarketData.build(list(market.universe), frame)


def _is_loopback(host) -> bool:
    if host in (None, "", "localhost"):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any test that tries to leave this machine.

    The suite must run offline and must never hit Yahoo for real. A
    ``pytest.fail`` here raises a BaseException, so the app's own
    ``except Exception`` fallbacks cannot quietly swallow the attempt.

    Two layers, because yfinance talks HTTP through curl_cffi (C libcurl),
    which never goes through Python's ``socket`` module:
    the yfinance and curl_cffi entry points, and non-loopback sockets for
    anything else (requests, urllib, a future provider).
    """

    def refuse(what):
        def blocked(*args, **_kwargs):
            pytest.fail(f"test tried to reach the network via {what} {args[:2]!r}; tests must be offline")

        return blocked

    for name in ("download", "Ticker", "Tickers", "Search", "Lookup"):
        monkeypatch.setattr(yfinance, name, refuse(f"yfinance.{name}"))
    monkeypatch.setattr(curl_requests.Session, "request", refuse("curl_cffi"))
    monkeypatch.setattr(curl_requests.AsyncSession, "request", refuse("curl_cffi"))

    real_connect, real_getaddrinfo = socket.socket.connect, socket.getaddrinfo

    def connect(sock, address, *args, **kwargs):
        if sock.family == getattr(socket, "AF_UNIX", None) or _is_loopback(address[0]):
            return real_connect(sock, address, *args, **kwargs)
        return refuse("socket.connect")(address)

    def getaddrinfo(host, *args, **kwargs):
        if _is_loopback(host):
            return real_getaddrinfo(host, *args, **kwargs)
        return refuse("DNS lookup")(host)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


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
    # No second opinions unless a test wires fakes: the real ones reach the
    # network, and the suite must run offline (``no_network`` enforces it).
    built.market.verifier = None
    built.market.splits_source = None
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
