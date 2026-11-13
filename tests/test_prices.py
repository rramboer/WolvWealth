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
