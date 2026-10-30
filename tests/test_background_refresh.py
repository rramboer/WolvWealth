"""The startup refresh runs off the request path, and cache writes are atomic."""

import dataclasses
import threading

import pandas as pd
import pytest

from conftest import synthetic_market
from wolvwealth.services.market_data import MarketDataService
from wolvwealth.storage.price_store import PriceStore
import wolvwealth.__main__ as cli


def build_service(tmp_path, downloader):
    """A service over a real store seeded with the synthetic market."""
    market = synthetic_market()
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv")
    store.save_universe(list(market.universe))
    store.save_prices(market.prices)
    return MarketDataService(store, "2020-01-01", downloader=downloader, log=lambda *_: None), store


def appending_downloader(service, rows=3, before=None):
    """A downloader that appends ``rows`` business days after the cached history."""

    def download(tickers, _start, **_kwargs):
        if before is not None:
            before()
        last = service.data.prices.index[-1]
        dates = pd.bdate_range(last + pd.Timedelta(days=1), periods=rows)
        return pd.DataFrame({t: [100.0 + i for i in range(rows)] for t in tickers}, index=dates)

    return download


def test_fresh_cache_starts_no_thread(tmp_path):
    service, _ = build_service(tmp_path, lambda *a, **k: pytest.fail("must not download"))
    service.set_data(service.data)
    assert service.start_background_refresh(max_age_days=100_000) is None


def test_stale_cache_refreshes_off_the_calling_thread(tmp_path):
    service, _ = build_service(tmp_path, None)
    service.downloader = appending_downloader(service)
    before = service.data.as_of

    thread = service.start_background_refresh(max_age_days=0)
    assert thread is not None
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert service.data.as_of > before


def test_the_cached_snapshot_is_served_until_the_refresh_lands(tmp_path):
    """The whole point: requests keep working on old prices while the download runs."""
    service, _ = build_service(tmp_path, None)
    released = threading.Event()
    observed = {}

    def wait_then_download():
        observed["during"] = service.data.as_of  # readable mid-refresh
        released.wait(timeout=30)

    service.downloader = appending_downloader(service, before=wait_then_download)
    before = service.data.as_of
    thread = service.start_background_refresh(max_age_days=0)
    released.set()
    thread.join(timeout=30)
    assert observed["during"] == before
    assert service.data.as_of > before


def test_a_second_trigger_does_not_start_a_duplicate_download(tmp_path):
    service, _ = build_service(tmp_path, None)
    started = threading.Event()
    release = threading.Event()
    downloads = []

    def blocking():
        downloads.append(1)
        started.set()
        release.wait(timeout=30)

    service.downloader = appending_downloader(service, before=blocking)
    first = service.start_background_refresh(max_age_days=0)
    started.wait(timeout=30)
    assert service.start_background_refresh(max_age_days=0) is None
    release.set()
    first.join(timeout=30)
    assert len(downloads) == 1


def test_a_failing_refresh_leaves_the_cached_prices_serving(tmp_path):
    def boom(*_args, **_kwargs):
        raise RuntimeError("yahoo is down")

    service, _ = build_service(tmp_path, boom)
    before = service.data.as_of
    thread = service.start_background_refresh(max_age_days=0)
    thread.join(timeout=30)
    assert service.data.as_of == before
    assert service.data.prices is not None


def test_serve_does_not_block_on_a_stale_refresh(services, monkeypatch):
    """`wolvwealth serve` must reach app.run() without waiting for the download."""
    order = []
    release = threading.Event()

    def slow_refresh(_max_age_days):
        order.append("refresh-started")
        thread = threading.Thread(target=release.wait, daemon=True)
        thread.start()
        return thread

    services.settings = dataclasses.replace(services.settings, auto_refresh=True)
    monkeypatch.setattr(services.market, "start_background_refresh", slow_refresh)
    monkeypatch.setattr("flask.Flask.run", lambda self, **kwargs: order.append("serving"))
    assert cli.main(["serve", "--no-browser"], services) == 0
    release.set()
    assert order == ["refresh-started", "serving"]


def test_price_writes_are_atomic(tmp_path):
    """A failed write must not leave a truncated cache behind."""
    store = PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv")
    good = synthetic_market().prices
    store.save_prices(good)

    class Unwritable(pd.DataFrame):
        def to_parquet(self, *_args, **_kwargs):
            raise OSError("disk full")

    with pytest.raises(OSError):
        store.save_prices(Unwritable(good))
    assert store.load_prices().equals(good)
    assert not list(tmp_path.glob("*.partial"))
