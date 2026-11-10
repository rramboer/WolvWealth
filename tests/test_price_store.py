"""PriceStore on-disk behavior: Parquet round trips, legacy CSV conversion, the stats cache, atomic writes."""

import json
import pathlib

import numpy as np
import pandas as pd
import pandas.testing
import pytest

from conftest import synthetic_market
from wolvwealth.domain.errors import DataError
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


def test_the_first_existing_legacy_csv_is_migrated_once(tmp_path):
    prices = synthetic_market().prices
    legacy = tmp_path / "old_prices.csv"
    prices.to_csv(legacy, index=True)
    store = make_store(tmp_path, legacy_csv_paths=(tmp_path / "missing.csv", legacy))
    assert store.migrate_legacy_prices()
    pandas.testing.assert_frame_equal(store.load_prices(), prices, check_freq=False)
    assert not store.migrate_legacy_prices(), "a second migration is a no-op: the Parquet cache already exists"
    nothing = make_store(tmp_path / "elsewhere", legacy_csv_paths=(tmp_path / "missing.csv",))
    assert not nothing.migrate_legacy_prices() and not nothing.has_prices()
    garbage = tmp_path / "garbage.csv"
    garbage.write_text("not,a,price,file\n1,2,3,4\n", encoding="utf-8")
    broken = make_store(tmp_path / "broken", legacy_csv_paths=(garbage,))
    with pytest.raises(DataError, match="cannot be converted"):
        broken.migrate_legacy_prices()
    assert not broken.has_prices()


def test_stats_cache_round_trips_and_reads_as_absent_without_its_sidecar(tmp_path):
    store = make_store(tmp_path)
    assert store.load_stats() is None
    stats = MarketStats.compute(synthetic_market().prices)
    meta = {"as_of": "2021-07-14", "price_rows": 400}
    store.save_stats(stats.mu, stats.cov, meta)
    loaded_meta, mu, cov = store.load_stats()
    assert loaded_meta == meta
    pandas.testing.assert_series_equal(mu, stats.mu, check_names=False)
    pandas.testing.assert_frame_equal(cov, stats.cov)
    assert json.loads(store.stats_meta_path.read_text(encoding="utf-8")) == meta, "the sidecar is plain JSON"
    # No sidecar means the matrix is unvouched-for (e.g. a crash mid-save): treat as absent.
    store.stats_meta_path.unlink()
    assert store.load_stats() is None


def test_sidecars_round_trip_and_read_corrupt_as_absent(tmp_path):
    store = make_store(tmp_path)
    assert store.load_tbill() is None
    store.save_tbill(0.0412, "2026-10-30")
    assert store.load_tbill() == {"rate": 0.0412, "as_of": "2026-10-30"}
    for corrupt in (
        "not json",
        '{"rate": NaN, "as_of": "2026-10-30"}',
        '{"rate": 4.12, "as_of": "2026-10-30"}',
        '{"rate": "0.04", "as_of": "2026-10-30"}',
        '{"rate": 0.04, "as_of": null}',
        "[0.04]",
    ):
        store.tbill_path.write_text(corrupt, encoding="utf-8")
        assert store.load_tbill() is None, f"a corrupt sidecar reads as absent, never as an error: {corrupt}"
    assert store.load_quarantine() == {}
    whole = {"reason": "moved 9x", "detected": "2026-11-06", "scope": "ticker"}
    store.save_quarantine({"BBB": whole})
    assert store.load_quarantine() == {"BBB": whole}
    # A partial exclusion round-trips its dates; a record written before scopes
    # existed reads as the whole-ticker exclusion it was.
    partial = {
        "reason": "spiked",
        "detected": "2026-11-06",
        "scope": "partial",
        "untrusted_before": "2020-03-05",
        "masked_days": ["2019-07-01", "2021-02-02"],
    }
    store.save_quarantine({"BBB": partial})
    assert store.load_quarantine() == {"BBB": partial}
    store.quarantine_path.write_text('{"BBB": {"reason": "x"}, "../evil": {}, "CCC": 5}', encoding="utf-8")
    assert store.load_quarantine() == {"BBB": {"reason": "x", "detected": "", "scope": "ticker"}}
    # A partial entry whose dates are all unusable would blank nothing, so it
    # falls back to excluding the ticker rather than quietly excusing it.
    for broken in ('{"scope": "partial"}', '{"scope": "partial", "untrusted_before": "nope"}'):
        store.quarantine_path.write_text('{"BBB": %s}' % broken, encoding="utf-8")
        assert store.load_quarantine()["BBB"]["scope"] == "ticker"
    store.quarantine_path.write_text(
        '{"BBB": {"scope": "partial", "masked_days": ["2020-01-02", null, "junk"]}}', encoding="utf-8"
    )
    assert store.load_quarantine()["BBB"] == {
        "reason": "",
        "detected": "",
        "scope": "partial",
        "masked_days": ["2020-01-02"],
    }
    for corrupt in ("not json", "[1]"):
        store.quarantine_path.write_text(corrupt, encoding="utf-8")
        assert store.load_quarantine() == {}
    store.save_quarantine({})
    assert not store.quarantine_path.exists()
    assert store.load_accepted_moves() == {}
    store.save_accepted_moves({"BBB": {"2021-01-27": "moves the same way in stooq's data"}})
    assert store.load_accepted_moves() == {"BBB": {"2021-01-27": "moves the same way in stooq's data"}}
    # A file from before the evidence notes existed reads as the same dates, unexplained.
    store.accepted_path.write_text(
        '{"BBB": ["2021-01-27", "2021-01-27", "junk"], "AAA": {"2021-01-27": 5, "junk": "x"},'
        ' "../evil": ["2021-01-27"], "CCC": 5, "DDD": []}',
        encoding="utf-8",
    )
    assert store.load_accepted_moves() == {
        "BBB": {"2021-01-27": ""},
        "AAA": {"2021-01-27": "5"},
    }, "deduplicated, dated, and symbol-shaped only"
    for corrupt in ("not json", "[1]"):
        store.accepted_path.write_text(corrupt, encoding="utf-8")
        assert store.load_accepted_moves() == {}
    store.save_accepted_moves({})
    assert not store.accepted_path.exists()
    assert store.load_readjustments() == {}
    note = {"detected": "2026-11-06", "outcome": "history replaced on the new adjustment basis"}
    store.save_readjustments({"BBB": note})
    assert store.load_readjustments() == {"BBB": note}
    store.readjustments_path.write_text('{"BBB": {"outcome": "x"}, "../evil": {}, "CCC": 5}', encoding="utf-8")
    assert store.load_readjustments() == {"BBB": {"detected": "", "outcome": "x"}}
    for corrupt in ("not json", "[1]"):
        store.readjustments_path.write_text(corrupt, encoding="utf-8")
        assert store.load_readjustments() == {}
    store.save_readjustments({})
    assert not store.readjustments_path.exists()


def test_the_ticker_list_is_normalized_and_an_unusable_one_is_a_data_error(tmp_path):
    store = make_store(tmp_path)
    store.universe_path.write_text(
        " aaa \nBBB\nAAA\n\nccc,extra column\n<script>\n../../etc/passwd\n" + "X" * 40 + "\n", encoding="utf-8"
    )
    assert store.load_universe() == ["AAA", "BBB", "CCC"], "trimmed, upper-cased, first wins, symbols only"
    for content, fragment in [(b"", "is empty"), (b"\n , \n", "is empty"), (b"\xff\xfe\x00A", "unreadable")]:
        store.universe_path.write_bytes(content)
        with pytest.raises(DataError, match=fragment):
            store.load_universe()
    store.universe_path.unlink()
    with pytest.raises(DataError, match="unreadable"):
        store.load_universe()


def messy(prices):
    """Every way a cache seen in the wild departs from what readers assume, at once."""
    frame = prices.copy()
    frame.iloc[5, 0], frame.iloc[6, 0], frame.iloc[7, 0] = 0.0, -3.0, np.inf  # a close is positive and finite
    frame["bbb"] = frame["BBB"]  # a lower-case duplicate of a ticker
    frame.loc[frame.index[-1] + pd.Timedelta(days=1)] = np.nan  # a day with no price at all
    frame = frame.iloc[::-1]  # upside down
    repeated = frame.loc[[prices.index[3]]].copy()
    repeated.iloc[0, 0] *= 2
    frame = pd.concat([frame, repeated])  # a repeated date: the later row wins
    frame["DDD"] = frame["DDD"].astype(str)  # numbers stored as text
    frame.index = frame.index.strftime("%Y-%m-%d")  # and dates stored as text
    return frame


@pytest.mark.parametrize(
    "contents, fragment",
    [
        (lambda prices: prices.reset_index(drop=True), "not dated"),
        (lambda prices: prices.set_axis([f"day {n}" for n in range(len(prices))]), "not dated"),
        (lambda prices: prices.iloc[:1], "1 usable day"),
        (lambda prices: prices * np.nan, "0 usable day"),
        (b"garbage" * 50, "unreadable"),
        (b"", "unreadable"),
    ],
    ids=["numbered rows", "rows named as text", "one day", "no prices", "not parquet", "empty file"],
)
def test_an_unusable_price_cache_is_a_data_error_naming_the_fix(tmp_path, contents, fragment):
    store = make_store(tmp_path)
    if isinstance(contents, bytes):
        store.prices_path.write_bytes(contents)
    else:
        contents(synthetic_market().prices).to_parquet(store.prices_path)
    with pytest.raises(DataError, match=fragment) as excinfo:
        store.load_prices()
    assert "refresh-data" in excinfo.value.message


def test_a_messy_price_cache_is_cleaned_on_load(tmp_path):
    store = make_store(tmp_path)
    prices = synthetic_market().prices
    messy(prices).to_parquet(store.prices_path)
    loaded = store.load_prices()
    assert list(loaded.columns) == list(prices.columns) and loaded.index.is_monotonic_increasing
    assert len(loaded) == len(prices) and not loaded.index.has_duplicates and loaded.index.name == "Date"
    assert loaded.iloc[5:8, 0].isna().all(), "zero, negative and infinite closes are missing, not prices"
    assert loaded.iloc[3, 0] == prices.iloc[3, 0] * 2, "a repeated date keeps its later row"
    pd.testing.assert_series_equal(loaded["DDD"], prices["DDD"], check_freq=False)


def test_every_write_is_atomic_so_a_reader_never_sees_a_partial_file(tmp_path, monkeypatch):
    """Each writer builds the file beside its destination and renames it into place.

    A refresh writes while the web app, the CLI and an MCP server may be
    reading. So: bytes never land on the destination path itself, the
    destination is readable and unchanged at the moment a write fails, and a
    failure leaves the old file intact with no temporary behind.
    """
    store = make_store(tmp_path)
    prices = synthetic_market().prices
    stats = MarketStats.compute(prices)
    meta = {"as_of": "2021-07-14", "price_rows": 400}
    store.save_prices(prices)
    store.save_tbill(0.04, "2026-10-30")
    store.save_stats(stats.mu, stats.cov, meta)
    store.save_universe(["AAA", "BBB"])
    on_disk = {path: path.read_bytes() for path in tmp_path.iterdir()}
    assert len(on_disk) == 5 and not [path for path in on_disk if path.suffix == ".partial"]

    def crash_half_way(path):
        path = pathlib.Path(path)
        assert path.suffix == ".partial" and path not in on_disk, f"must not write onto the destination: {path}"
        path.write_bytes(b"half-written")
        destination = path.with_name(path.name[: -len(".partial")])
        assert destination.read_bytes() == on_disk[destination], "a reader mid-write must see the old file"
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, path, *_args, **_kwargs: crash_half_way(path))
    monkeypatch.setattr(pathlib.Path, "write_text", lambda self, *_args, **_kwargs: crash_half_way(self))
    writes = [
        lambda: store.save_prices(prices),
        lambda: store.save_tbill(0.05, "2026-11-01"),
        lambda: store.save_stats(stats.mu, stats.cov, {"as_of": "2026-11-01", "price_rows": 1}),
        lambda: store.save_universe(["ZZZ"]),
    ]
    for write in writes:
        with pytest.raises(OSError, match="disk full"):
            write()
    monkeypatch.undo()
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == on_disk, "every destination is as it was"
    assert store.load_prices().equals(prices) and store.load_tbill() == {"rate": 0.04, "as_of": "2026-10-30"}
    assert store.load_stats()[0] == meta and store.load_universe() == ["AAA", "BBB"]
