"""PriceStore on-disk behavior: Parquet round trips, legacy CSV conversion, the stats cache."""

import json

import pandas as pd
import pandas.testing

from conftest import synthetic_market
from wolvwealth.domain.stats import MarketStats
from wolvwealth.storage.price_store import PriceStore


def make_store(tmp_path, legacy_csv_paths=()) -> PriceStore:
    return PriceStore(tmp_path / "historical_prices.parquet", tmp_path / "universe.csv", legacy_csv_paths)


def test_prices_round_trip_through_parquet(tmp_path):
    store = make_store(tmp_path)
    prices = synthetic_market().prices
    assert not store.has_prices()
    store.save_prices(prices)
    assert store.has_prices() and store.prices_path.suffix == ".parquet"
    loaded = store.load_prices()
    # check_freq=False: the synthetic fixture's bdate_range carries a freq; real caches never do.
    pandas.testing.assert_frame_equal(loaded, prices, check_freq=False)
    assert loaded.index.name == "Date"


def test_migrates_first_existing_legacy_csv(tmp_path):
    prices = synthetic_market().prices
    legacy = tmp_path / "old_prices.csv"
    prices.to_csv(legacy, index=True)
    store = make_store(tmp_path, legacy_csv_paths=(tmp_path / "missing.csv", legacy))
    assert store.migrate_legacy_prices()
    pandas.testing.assert_frame_equal(store.load_prices(), prices, check_freq=False)
    # A second migration is a no-op: the Parquet cache already exists.
    assert not store.migrate_legacy_prices()


def test_migrate_without_any_legacy_csv_returns_false(tmp_path):
    store = make_store(tmp_path, legacy_csv_paths=(tmp_path / "missing.csv",))
    assert not store.migrate_legacy_prices()
    assert not store.has_prices()


def test_stats_round_trip(tmp_path):
    store = make_store(tmp_path)
    stats = MarketStats.compute(synthetic_market().prices)
    meta = {"as_of": "2021-07-14", "price_rows": 400}
    store.save_stats(stats.mu, stats.cov, meta)
    loaded_meta, mu, cov = store.load_stats()
    assert loaded_meta == meta
    pandas.testing.assert_series_equal(mu, stats.mu, check_names=False)
    pandas.testing.assert_frame_equal(cov, stats.cov)


def test_stats_absent_without_both_files(tmp_path):
    store = make_store(tmp_path)
    assert store.load_stats() is None
    stats = MarketStats.compute(synthetic_market().prices)
    store.save_stats(stats.mu, stats.cov, {"as_of": "2021-07-14", "price_rows": 400})
    # No sidecar means the matrix is unvouched-for (e.g. a crash mid-save): treat as absent.
    store.stats_meta_path.unlink()
    assert store.load_stats() is None


def test_stats_meta_is_plain_json(tmp_path):
    store = make_store(tmp_path)
    stats = MarketStats.compute(synthetic_market().prices)
    store.save_stats(stats.mu, stats.cov, {"as_of": "2021-07-14", "price_rows": 400})
    assert json.loads(store.stats_meta_path.read_text(encoding="utf-8"))["price_rows"] == 400
