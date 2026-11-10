"""Implausible prices cost the days they affect, not the company.

A flat "5x in a day is impossible" rule is wrong often enough that the
system has to survive being wrong. 2,590 tickers back to 2006 is some
thirteen million closes, and genuine one-day moves past 5x -- a biotech
trial result, a collapse, a post-bankruptcy relisting -- are a certainty at
that scale, not a freak. Worse, the symmetric lower bound catches an 80%
drop, which is an ordinary bad day for a small-cap biotech.

So the verdict is scoped (one print, or the history before a repricing),
reversible (nothing is deleted), and adjudicable (`accept-price`). The old
behaviour -- exclude the whole ticker forever, and tell the user to run the
`--full` refresh that re-downloads the same spike and re-excludes it -- was
a closed loop with no way out.
"""

import numpy as np
import pandas as pd
import pytest

from conftest import synthetic_market
from test_split_repair import build_service
from wolvwealth import __main__ as cli
from wolvwealth.domain.errors import ValidationError
from wolvwealth.domain.market import MIN_TRUSTED_DAYS, classify_implausible


def series(**changes) -> pd.Series:
    """BBB's synthetic closes, with ``iloc`` slices scaled by the given factors."""
    closes = synthetic_market().prices["BBB"].copy()
    for where, factor in changes.items():
        closes.iloc[slice(*(int(p) if p else None for p in where.split("_")))] *= factor
    return closes


@pytest.mark.parametrize(
    "closes, expected",
    [
        (series(), None),
        # One print, straight back: the company is fine, that close is not.
        (series(**{"200_201": 20}), {"scope": "partial", "masked_days": 1, "untrusted_before": None}),
        (series(**{"200_201": 0.05}), {"scope": "partial", "masked_days": 1, "untrusted_before": None}),
        # It stayed there, so the history across the jump is no basis for statistics.
        (series(**{"200_": 20}), {"scope": "partial", "masked_days": 0, "untrusted_before": "2020-10-08"}),
        (series(**{"200_": 0.05}), {"scope": "partial", "masked_days": 0, "untrusted_before": "2020-10-08"}),
        # Nothing usable after it, so there is no history left worth serving.
        (series(**{"-5_": 20}), {"scope": "ticker", "masked_days": 0, "untrusted_before": None}),
        # A move with nothing after it yet cannot be judged; drop the one print for now.
        (series(**{"-1_": 20}), {"scope": "partial", "masked_days": 1, "untrusted_before": None}),
    ],
    ids=["clean", "spike reverts", "crash reverts", "spike persists", "crash persists", "nothing left", "trailing"],
)
def test_a_flagged_move_is_judged_by_whether_the_price_stayed_there(closes, expected):
    finding = classify_implausible(closes)
    if expected is None:
        assert finding is None
        return
    assert finding["scope"] == expected["scope"]
    assert len(finding.get("masked_days", [])) == expected["masked_days"]
    assert finding.get("untrusted_before") == expected["untrusted_before"]


def test_a_reverting_spike_is_one_bad_print_not_a_spike_and_a_crash():
    """Regression: judging each day against the one before it made a single bad close look like two moves.

    The second day was measured from the corrupt number, so a print that reverted read as a
    permanent repricing and truncated the whole history behind it.
    """
    finding = classify_implausible(series(**{"200_201": 20}))
    assert finding["masked_days"] == ["2020-10-08"] and "untrusted_before" not in finding
    assert "back the next close" in finding["reason"]


def test_a_truncation_keeps_the_ticker_usable_rather_than_excluding_it(tmp_path):
    """The point of scoping: a 2020 spike must not cost a name its 2021."""
    service, store = build_service(tmp_path)
    clean = synthetic_market().prices
    moved = clean.copy()
    moved.iloc[200:, moved.columns.get_loc("BBB")] *= 20
    service.downloader = lambda tickers, _s, **_k: moved.loc[:, list(tickers)]
    assert service.refresh(full=True) is True

    assert service.quarantine == {}, "a real repricing is not grounds for excluding the company"
    entry = service.price_exclusions["BBB"]
    assert entry["untrusted_before"] == "2020-10-08" and entry["scope"] == "partial"
    assert "BBB" in service.data.tradeable, "it still trades, on the prices that are believable"
    assert service.data.prices["BBB"].count() == len(clean) - 200
    assert service.data.prices["BBB"].iloc[:200].isna().all() and service.data.prices["BBB"].iloc[200:].notna().all()
    # Nothing was deleted: the archive still holds every close the exclusion hides.
    assert store.load_prices()["BBB"].count() == len(clean)
    assert MIN_TRUSTED_DAYS <= 200, "the fixture must leave enough history to be worth serving"


def test_accepting_a_move_restores_the_history_and_is_never_re_judged(tmp_path):
    """The escape hatch: a person who knows the move was real says so once, and no refresh re-decides it."""
    service, store = build_service(tmp_path)
    clean = synthetic_market().prices
    moved = clean.copy()
    moved.iloc[200:, moved.columns.get_loc("BBB")] *= 20
    service.downloader = lambda tickers, _s, **_k: moved.loc[:, list(tickers)]
    assert service.refresh(full=True) is True
    assert service.data.prices["BBB"].count() == len(clean) - 200

    assert service.accept_price_move("bbb", "2020-10-08") is None, "nothing is excluded for BBB any more"
    assert store.load_accepted_moves() == {"BBB": ["2020-10-08"]}
    assert "BBB" not in store.load_quarantine()
    assert service.data.prices["BBB"].count() == len(clean), "the full history is served again"

    # And the judgement sticks: the refresh that used to re-exclude it no longer does.
    assert service.refresh(full=True) is True
    assert service.quarantine == {} and service.price_exclusions == {}
    assert service.data.prices["BBB"].count() == len(clean)


def test_accepting_one_move_leaves_any_other_standing(tmp_path):
    """Accepting a date is not a blanket pardon for the ticker."""
    service, _store = build_service(tmp_path)
    clean = synthetic_market().prices
    moved = clean.copy()
    column = moved.columns.get_loc("BBB")
    moved.iloc[100, column] *= 20  # a bad print, reverts
    moved.iloc[200:, column] *= 20  # a repricing, persists
    service.downloader = lambda tickers, _s, **_k: moved.loc[:, list(tickers)]
    assert service.refresh(full=True) is True
    assert service.price_exclusions["BBB"]["untrusted_before"] == "2020-10-08"

    remaining = service.accept_price_move("BBB", "2020-10-08")
    assert remaining is not None and remaining["masked_days"] == [str(clean.index[100].date())]
    assert service.data.prices["BBB"].count() == len(clean) - 1, "only the bad print stays out"


@pytest.mark.parametrize(
    "ticker, day, fragment",
    [("ZZZ", "2020-10-08", "No price history"), ("BBB", "not-a-date", "Invalid date"), ("", "2020-10-08", "ticker")],
)
def test_accept_price_move_validates_its_arguments(tmp_path, ticker, day, fragment):
    service, _store = build_service(tmp_path)
    with pytest.raises(ValidationError, match=fragment):
        service.accept_price_move(ticker, day)


def test_the_cli_reports_a_partial_exclusion_and_can_accept_it(tmp_path, services, capsys):
    """An unattended run must be able to see what stopped being used, and a human to overrule it."""
    clean = synthetic_market().prices
    moved = clean.copy()
    moved.iloc[200:, moved.columns.get_loc("BBB")] *= 20
    services.market.downloader = lambda tickers, _s, **_k: moved.loc[:, list(tickers)]
    assert cli.main(["refresh-data", "--full"], services) == 0
    out = capsys.readouterr().out
    assert "BBB still trades, but some of its prices are unused" in out
    assert "Quarantined BBB" not in out
    assert "accept-price" in out

    assert cli.main(["accept-price", "BBB", "2020-10-08"], services) == 0
    out = capsys.readouterr().out
    assert "Accepted BBB's price move on 2020-10-08" in out and "full price history" in out
    assert services.market.data.prices["BBB"].count() == len(clean)


def test_the_market_report_separates_unusable_tickers_from_unused_prices(tmp_path, client, services):
    """An agent told a name is quarantined must not find it allocated to, and vice versa."""
    clean = synthetic_market().prices
    moved = clean.copy()
    moved.iloc[200:, moved.columns.get_loc("BBB")] *= 20  # partial: still tradeable
    moved.iloc[-5:, moved.columns.get_loc("DDD")] *= 20  # nothing left: excluded outright
    services.market.downloader = lambda tickers, _s, **_k: moved.loc[:, list(tickers)]
    assert services.market.refresh(full=True) is True

    body = client.get("/api/market/").get_json()
    assert list(body["quarantined"]) == ["DDD"]
    assert list(body["price_exclusions"]) == ["BBB"]
    assert "BBB" in body["tickers"] and "DDD" not in body["tickers"]
    assert body["price_exclusions"]["BBB"]["untrusted_before"] == "2020-10-08"


@pytest.mark.parametrize(
    "cut, optimized",
    [(100, True), (200, False)],
    ids=["enough history left to season", "too little left to season"],
)
def test_a_partially_excluded_ticker_degrades_by_the_seasoning_rule_not_by_exclusion(services, cut, optimized):
    """Truncation hands the ticker to machinery that already exists, instead of needing its own.

    With a year of closes left it is an ordinary ticker; with less, the seasoning rule keeps
    it out of universes and freezes a held position -- the same treatment a recent listing
    gets. Either way it is valued, reported and never silently dropped.
    """
    from wolvwealth.domain import optimizer  # pylint: disable=import-outside-toplevel

    moved = synthetic_market().prices.copy()
    moved.iloc[cut:, moved.columns.get_loc("BBB")] *= 20
    services.market.downloader = lambda tickers, _s, **_k: moved.loc[:, list(tickers)]
    assert services.market.refresh(full=True) is True
    data = services.market.data
    assert "BBB" not in data.frozen_valuations, "it has prices of its own; nothing needs freezing"
    assert data.history_days["BBB"] == len(moved) - cut

    result = optimizer.optimize({"initial_cash": 10_000, "initial_holdings": {"BBB": 5}}, data)
    assert np.isfinite(result["metrics"]["expected_annual_return"])
    if optimized:
        assert "unoptimized" not in result and "BBB" in data.seasoned()
    else:
        assert result["unoptimized"]["positions"]["BBB"]["reason"] == "short_history"
        assert result["optimized_portfolio"]["BBB"]["shares"] == 5, "kept, not sold on thin data"
