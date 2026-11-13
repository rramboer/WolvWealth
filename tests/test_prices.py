"""Tests for the price refresh (wolvwealth/api/prices.py). None of these download anything."""
import datetime

import numpy as np
import pandas as pd
import pytest

from wolvwealth.api import prices

NY = prices.MARKET_TZ


@pytest.mark.parametrize(
    "now, end",
    [
        ("2026-11-12 10:00", "2026-11-12"),  # Thursday, market open: stop at Wednesday.
        ("2026-11-12 16:29", "2026-11-12"),
        ("2026-11-12 16:30", "2026-11-13"),  # Close is final: include Thursday.
        ("2026-11-14 18:00", "2026-11-14"),  # Saturday: Friday is the last day either way.
    ],
)
def test_download_end_date(now, end):
    when = datetime.datetime.fromisoformat(now).replace(tzinfo=NY)
    assert prices.download_end_date(when) == datetime.date.fromisoformat(end)


def test_download_end_date_converts_other_time_zones():
    # 22:00 UTC on Thursday is 5:00 pm in New York, after the close.
    when = datetime.datetime(2026, 11, 12, 22, 0, tzinfo=datetime.timezone.utc)
    assert prices.download_end_date(when) == datetime.date(2026, 11, 13)


def frame(rows: dict) -> pd.DataFrame:
    """Build a price frame from {date: [A, B, C]}."""
    return pd.DataFrame(list(rows.values()), index=pd.to_datetime(list(rows)), columns=["A", "B", "C"])


def test_drop_incomplete_days_drops_a_partial_last_day():
    closes = frame({"2026-11-10": [1, 2, 3], "2026-11-11": [1, 2, 3], "2026-11-12": [np.nan, np.nan, 3]})
    assert list(prices.drop_incomplete_days(closes).index.day) == [10, 11]


def test_drop_incomplete_days_drops_empty_days_anywhere():
    closes = frame({"2026-11-10": [1, 2, 3], "2026-11-11": [np.nan] * 3, "2026-11-12": [1, 2, 3]})
    assert list(prices.drop_incomplete_days(closes).index.day) == [10, 12]


def test_drop_incomplete_days_keeps_a_day_with_most_prices():
    closes = frame({"2026-11-11": [1, 2, 3], "2026-11-12": [1, np.nan, 3]})
    assert list(prices.drop_incomplete_days(closes).index.day) == [11, 12]


def test_stopped_trading():
    days = pd.date_range("2026-10-01", "2026-11-12", freq="B")
    closes = pd.DataFrame({"LIVE": 1.0, "GONE": 1.0}, index=days)
    closes.loc[closes.index > "2026-10-20", "GONE"] = np.nan
    assert prices.stopped_trading(closes) == {"GONE": datetime.date(2026, 10, 20)}


def test_write_prices_round_trips_through_the_app_loader(tmp_path):
    closes = frame({"2006-01-03": [0.29133, 2.23575, np.nan], "2026-11-11": [173.73, 355.63, 59000.5]})
    closes.index.name = "Date"
    path = tmp_path / "prices.csv"
    prices.write_prices(closes[["C", "A", "B"]], str(path))
    assert path.read_text().splitlines()[:2] == ["Date,A,B,C", "2006-01-03,0.29133,2.23575,"]
    back = pd.read_csv(path, parse_dates=True, index_col="Date")
    pd.testing.assert_frame_equal(back, closes, check_freq=False, check_index_type=False)
    assert not (tmp_path / "prices.csv.tmp").exists()


@pytest.mark.parametrize("prune", [False, True])
def test_refresh_reports_and_optionally_prunes(tmp_path, monkeypatch, prune):
    days = pd.date_range("2026-10-01", "2026-11-11", freq="B")
    downloaded = pd.DataFrame({"LIVE": 1.0, "GONE": 1.0, "ALSO": 2.0}, index=days)
    downloaded.loc[downloaded.index > "2026-10-20", "GONE"] = np.nan  # Stopped trading.
    downloaded.index.name = "Date"
    monkeypatch.setattr(prices, "download_closes", lambda tickers, start, end: downloaded)
    monkeypatch.setattr(prices, "PRICES_FILE", str(tmp_path / "prices.csv"))
    monkeypatch.setattr(prices, "UNIVERSE_FILE", str(tmp_path / "universe.csv"))
    universe = ["LIVE", "NODATA", "GONE", "ALSO"]  # Largest first; NODATA isn't in the download.
    prices.write_universe(universe, prices.UNIVERSE_FILE)

    report = prices.refresh(universe, force=True, prune=prune)

    assert report["missing"] == ["NODATA"]
    assert report["stopped_trading"] == {"GONE": datetime.date(2026, 10, 20)}
    assert report["last_date"] == datetime.date(2026, 11, 11)
    assert report["suspicious"] == {}
    written = pd.read_csv(prices.PRICES_FILE, index_col="Date")
    if prune:
        assert report["removed"] == ["NODATA", "GONE"]
        assert prices.read_universe(prices.UNIVERSE_FILE) == ["LIVE", "ALSO"]
        assert list(written.columns) == ["ALSO", "LIVE"]
    else:
        assert report["removed"] == []
        assert prices.read_universe(prices.UNIVERSE_FILE) == universe
        assert list(written.columns) == ["ALSO", "GONE", "LIVE", "NODATA"]
        assert written["NODATA"].isna().all()


def test_refresh_leaves_files_alone_when_too_much_is_missing(tmp_path, monkeypatch):
    downloaded = pd.DataFrame({"A": [1.0]}, index=pd.to_datetime(["2026-11-11"]))
    monkeypatch.setattr(prices, "download_closes", lambda tickers, start, end: downloaded)
    monkeypatch.setattr(prices, "PRICES_FILE", str(tmp_path / "prices.csv"))
    with pytest.raises(RuntimeError, match="rate limiting"):
        prices.refresh(["A", "B", "C", "D"])  # 3 of 4 missing.
    assert not (tmp_path / "prices.csv").exists()


def test_suspicious_prices_flags_non_positive_prices_and_big_moves():
    days = pd.date_range("2026-11-02", periods=6, freq="B")
    closes = pd.DataFrame(
        {
            "FINE": [10.0, 11.0, 10.5, 10.8, 11.2, 11.0],
            "NEG": [-2.0, -1.5, 5.0, 5.1, 5.2, 5.3],  # Negative, then a normal price.
            "JUMP": [10.0, 10.5, 25.0, 25.5, 2.0, 2.1],  # +138%, then -92%.
            "GAP": [10.0, np.nan, np.nan, 30.0, 30.5, 31.0],  # +200% across missing days.
            "EDGE": [10.0, 19.0, 19.1, 1.91, 1.95, 2.0],  # +90% and -90% exactly aren't over the line.
        },
        index=days,
    )
    flags = prices.suspicious_prices(closes)
    assert set(flags) == {"NEG", "JUMP", "GAP"}
    assert flags["NEG"]["non_positive_days"] == 2
    assert flags["NEG"]["non_positive_from"] == datetime.date(2026, 11, 2)
    assert flags["NEG"]["non_positive_to"] == datetime.date(2026, 11, 3)
    assert flags["NEG"]["large_moves"] == []  # No move is measured from a negative price.
    assert [(day, round(change, 2)) for day, change in flags["JUMP"]["large_moves"]] == [
        (datetime.date(2026, 11, 4), 1.38),
        (datetime.date(2026, 11, 6), -0.92),
    ]
    assert flags["JUMP"]["non_positive_days"] == 0 and flags["JUMP"]["non_positive_from"] is None
    gap_moves = [(day, round(change, 2)) for day, change in flags["GAP"]["large_moves"]]
    assert gap_moves == [(datetime.date(2026, 11, 5), 2.0)]


def test_describe_flag():
    flag = {"non_positive_days": 988, "large_moves": [(None, 1.0), (None, 47.7)]}
    assert prices.describe_flag(flag) == "988 days at or below $0, 2 one-day moves over 90%"
    assert prices.describe_flag({"non_positive_days": 0, "large_moves": [(None, 1.0)]}) == "1 one-day move over 90%"
