"""A flagged price move gets a second opinion before it costs anything.

One price series cannot say whether a 6x day was real or corrupt, so the
check escalates instead of convicting: Yahoo's own split calendar first (a
move matching a split ratio is an adjustment artifact, cured by the existing
re-download machinery), then an independent source (stooq). Agreement means
the move is real and recorded as accepted with the evidence; disagreement
confirms the flagged prices wrong; no answer leaves the scoped single-source
verdict, saying so. A human acceptance outranks all of it.
"""

import pandas as pd
import pytest

from conftest import synthetic_market
from test_split_repair import build_service
from wolvwealth.market import stooq
from wolvwealth.services import price_checks, verification


def moved_market(cut=200, factor=20.0, ticker="BBB"):
    """The synthetic prices with ``ticker`` scaled by ``factor`` from row ``cut`` on; returns (clean, moved, day)."""
    clean = synthetic_market().prices
    moved = clean.copy()
    moved.iloc[cut:, moved.columns.get_loc(ticker)] *= factor
    return clean, moved, str(clean.index[cut].date())


def serving(frame):
    return lambda tickers, _start, **_k: frame.loc[:, list(tickers)]


def counting(series_by_ticker):
    """A fake second source serving fixed series, counting its calls."""
    calls = []

    def fetch(ticker):
        calls.append(ticker)
        return series_by_ticker.get(ticker)

    fetch.source_name = "stooq"
    return fetch, calls


# --- the stooq adapter and the pure comparisons ------------------------------


def test_stooq_payloads_are_parsed_like_any_untrusted_download():
    good = b"Date,Open,High,Low,Close,Volume\n2021-01-26,10,12,9,100.5,1000\n2021-01-27,10,12,9,236.2,9000\n"
    closes = stooq.parse_closes(good, "gme")
    assert list(closes.round(2)) == [100.5, 236.2] and closes.index[0] == pd.Timestamp("2021-01-26")
    assert closes.name == "GME"
    for junk in (
        b"",
        b"<html>No data</html>",
        b"Date,Open\n2021-01-26,10\n",  # no Close column
        b"Date,Close\n2021-01-26,-5\n2021-01-27,0\n",  # nothing believable survives cleaning
        b"x" * (stooq.MAX_BYTES + 1),
    ):
        assert stooq.parse_closes(junk, "GME") is None, junk[:30]


def test_a_second_opinion_compares_the_same_two_dates_and_knows_when_it_has_no_answer():
    second = pd.Series([100.0, 2000.0], index=pd.to_datetime(["2021-01-26", "2021-01-27"]))
    assert price_checks.second_opinion(second, "2021-01-26", "2021-01-27", 20.0) == "real"
    assert price_checks.second_opinion(second * 7, "2021-01-26", "2021-01-27", 20.0) == "real", "ratios are basis-free"
    assert price_checks.second_opinion(second, "2021-01-26", "2021-01-27", 1.01) == "corrupt"
    assert price_checks.second_opinion(second, "2021-01-20", "2021-01-27", 20.0) is None, "a missing date is no answer"
    assert price_checks.second_opinion(None, "2021-01-26", "2021-01-27", 20.0) is None
    assert price_checks.second_opinion(second * 0, "2021-01-26", "2021-01-27", 20.0) is None


@pytest.mark.parametrize(
    "splits, day, ratio, expected",
    [
        ({"2020-10-08": 10.0}, "2020-10-08", 0.1004, 10.0),
        ({"2020-10-08": 10.0}, "2020-10-08", 9.8, 10.0),  # the inverse artifact: double-adjusted
        ({"2020-10-06": 10.0}, "2020-10-08", 0.1, 10.0),  # a couple of days of wobble
        ({"2020-08-01": 10.0}, "2020-10-08", 0.1, None),  # a split elsewhere explains nothing
        ({"2020-10-08": 2.0}, "2020-10-08", 0.1, None),  # the ratio must actually match
        ({}, "2020-10-08", 0.1, None),
        (None, "2020-10-08", 0.1, None),
    ],
)
def test_a_split_explains_a_move_only_on_the_right_date_with_the_right_ratio(splits, day, ratio, expected):
    series = pd.Series({pd.Timestamp(d): r for d, r in splits.items()}) if splits is not None else None
    assert price_checks.matching_split(series, day, ratio) == expected


# --- the verdicts, end to end -------------------------------------------------


def test_a_move_the_second_source_also_shows_is_real_and_never_flagged_again(tmp_path):
    """The GameStop case: a genuine repricing both sources saw costs nothing at all."""
    clean, moved, day = moved_market()
    service, store = build_service(tmp_path, downloader=serving(moved))
    # A different source serves different absolute prices (its own adjustment
    # basis); only the move across the same two dates has to agree.
    service.verifier, calls = counting({"BBB": (moved["BBB"] * 3).round(2)})
    assert service.refresh(full=True) is True

    assert service.quarantine == {} and service.price_exclusions == {}, "nothing is excluded"
    assert service.data.prices["BBB"].count() == len(clean), "the full history serves, jump and all"
    note = store.load_accepted_moves()["BBB"][day]
    assert "stooq" in note, "the evidence is recorded where a human would record theirs"
    assert calls == ["BBB"]

    # Settled for good: the next full refresh re-downloads the same move and asks nobody.
    assert service.refresh(full=True) is True
    assert calls == ["BBB"] and service.quarantine == {} and service.price_exclusions == {}


def test_a_genuine_collapse_near_the_end_of_history_is_kept_once_corroborated(tmp_path):
    """The small-cap-biotech case: an 85% day with almost nothing after it used to retire the ticker.

    With too few closes left to trust after the drop, the single-source verdict is whole-ticker
    exclusion. A second source showing the same drop settles it the other way: the collapse is
    what the company is actually worth now, and all of its history serves.
    """
    clean, moved, day = moved_market(cut=-5, factor=0.15)
    service, store = build_service(tmp_path, downloader=serving(moved))
    service.verifier, _calls = counting({"BBB": (moved["BBB"] * 2).round(2)})
    assert service.refresh(full=True) is True

    assert service.quarantine == {} and service.price_exclusions == {}
    assert "BBB" in service.data.tradeable
    assert service.data.prices["BBB"].count() == len(clean)
    assert service.data.price("BBB") == pytest.approx(moved["BBB"].iloc[-1]), "priced at the collapsed level"
    assert day in store.load_accepted_moves()["BBB"]


def test_a_denied_collapse_with_nothing_after_it_stays_excluded_with_the_evidence(tmp_path):
    _clean, moved, _day = moved_market(cut=-5, factor=0.15)
    service, _store = build_service(tmp_path, downloader=serving(moved))
    service.verifier, _calls = counting({"BBB": (moved["BBB"].iloc[:-5] * 2).round(2)})  # no closes for the drop
    assert service.refresh(full=True) is True
    entry = service.quarantine["BBB"]
    assert "could not be checked" in entry["reason"], "a source without the dates has not contradicted anything"


def test_a_reverting_print_the_second_source_disputes_is_masked_with_the_evidence(tmp_path):
    clean = synthetic_market().prices
    spiked = clean.copy()
    spiked.iloc[150, spiked.columns.get_loc("BBB")] *= 20
    service, _store = build_service(tmp_path, downloader=serving(spiked))
    service.verifier, _calls = counting({"BBB": clean["BBB"]})
    assert service.refresh(full=True) is True

    entry = service.price_exclusions["BBB"]
    assert entry["masked_days"] == [str(clean.index[150].date())]
    assert "stooq does not show the move" in entry["reason"]
    assert "BBB" in service.data.tradeable


def test_a_persistent_move_the_second_source_disputes_excludes_the_wrong_side(tmp_path):
    """Truncation would keep exactly the prices the evidence is against, so the verdict escalates."""
    clean, moved, day = moved_market()
    service, _store = build_service(tmp_path, downloader=serving(moved))
    service.verifier, _calls = counting({"BBB": clean["BBB"]})
    assert service.refresh(full=True) is True

    entry = service.quarantine["BBB"]
    assert entry["scope"] == "ticker" and f"closes from {day} on" in entry["reason"]
    assert "BBB" not in service.data.tradeable
    # A holding is valued at the last close before the jump: the side both sources agree on.
    assert service.data.valuation_price("BBB") == pytest.approx(clean["BBB"].iloc[199])


def test_an_unreachable_second_source_leaves_the_scoped_verdict_and_says_so(tmp_path):
    _clean, moved, day = moved_market()
    service, _store = build_service(tmp_path, downloader=serving(moved))
    service.verifier, calls = counting({})  # configured, but it has no data for BBB
    assert service.refresh(full=True) is True

    entry = service.price_exclusions["BBB"]
    assert entry["untrusted_before"] == day, "the single-source verdict stands"
    assert "could not be checked" in entry["reason"]
    assert calls == ["BBB"]


def test_a_move_matching_the_split_calendar_is_rebased_not_disbelieved(tmp_path):
    """Idea 4: the recorded split explains the move, so the cure is the existing full re-download."""
    clean, moved, day = moved_market(factor=0.1)  # a 10:1 split applied to one side of the history
    service, store = build_service(tmp_path, downloader=serving(moved))
    service.splits_source = lambda ticker: pd.Series({pd.Timestamp(day): 10.0}) if ticker == "BBB" else None
    service.verifier, calls = counting({"BBB": clean["BBB"]})
    assert service.refresh(full=True) is True

    entry = service.quarantine["BBB"]
    assert price_checks.is_basis_failure(entry), "diagnosed as a basis problem, not implausible prices"
    assert "10:1" in entry["reason"] and calls == [], "the calendar answers before the second source is asked"
    # Valued meanwhile at the newest close: the side Yahoo serves live, hence today's basis.
    assert service.data.valuation_price("BBB") == pytest.approx(moved["BBB"].iloc[-1])

    # The next refresh re-downloads the history whole on one basis, and the ticker recovers.
    new_dates = pd.bdate_range(moved.index[-1] + pd.Timedelta(days=1), periods=2)
    healed = clean.copy()
    healed["BBB"] = (healed["BBB"] * 0.1).round(4)
    healed = pd.concat([healed, pd.DataFrame({t: [healed[t].iloc[-1]] * 2 for t in healed.columns}, index=new_dates)])
    service.downloader = lambda tickers, start, **_k: healed.loc[healed.index >= start, list(tickers)]
    assert service.refresh() is True
    assert service.quarantine == {} and "BBB" in service.data.tradeable
    assert store.load_prices()["BBB"].count() == len(healed)


def test_verification_spends_a_bounded_budget_per_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(verification, "MAX_TICKERS", 1)
    clean = synthetic_market().prices
    moved = clean.copy()
    moved.iloc[200:, moved.columns.get_loc("BBB")] *= 20
    moved.iloc[200:, moved.columns.get_loc("CCC")] *= 20
    logged = []
    service, _store = build_service(tmp_path, downloader=serving(moved))
    service.log = logged.append
    service.verifier, calls = counting({"BBB": clean["BBB"], "CCC": clean["CCC"]})
    assert service.refresh(full=True) is True

    assert len(calls) == 1, "one ticker checked, the rest left on the single-source verdict"
    assert len(service.quarantine) + len(service.price_exclusions) == 2, "both are still excluded somehow"
    assert any("budget" in line for line in logged)


def test_a_human_acceptance_outranks_every_source(tmp_path):
    """An accepted date is never flagged, so it never even reaches the verifier."""
    _clean, moved, day = moved_market()
    service, _store = build_service(tmp_path, downloader=serving(moved))
    service.accept_price_move("BBB", day)

    def never(_ticker):
        pytest.fail("the verifier must not be consulted about an accepted move")

    service.verifier = never
    service.splits_source = never
    assert service.refresh(full=True) is True
    assert service.quarantine == {} and service.price_exclusions == {}
