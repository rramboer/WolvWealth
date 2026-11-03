"""The universe-rebuild merge rules (scripts/build_universe.py), offline.

``build`` is pure given its inputs, so every rule that decides who joins the
file, who is skipped, and how everyone is ordered is pinned here without the
network. Fetching is the script's job; deciding is tested.
"""

import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "build_universe.py"
spec = importlib.util.spec_from_file_location("build_universe", SCRIPT)
build_universe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_universe)

BIG = build_universe.NEW_TICKER_CAP_FLOOR


def no_lookup(_ticker):
    return 0.0


def test_new_big_names_join_ranked_and_old_names_are_never_dropped():
    old = ["OLDBIG", "DEADCO"]
    listed = {"OLDBIG", "NEWCO"}
    stocks = [("OLDBIG", 2 * BIG, "old big inc"), ("NEWCO", 3 * BIG, "newco inc")]
    merged, summary = build_universe.build(old, listed, stocks, size_lookup=no_lookup)
    assert merged == ["NEWCO", "OLDBIG", "DEADCO"], "ranked by cap; the delisted name sinks but stays"
    assert summary == {"kept": 2, "added": 1, "delisted_retained": 1, "total": 3}


def test_small_new_names_stay_out_but_small_old_names_stay_in():
    old = ["OLDSMALL"]
    listed = {"OLDSMALL", "NEWSMALL"}
    stocks = [("OLDSMALL", BIG / 10, "old small inc"), ("NEWSMALL", BIG / 10, "new small inc")]
    merged, _ = build_universe.build(old, listed, stocks, size_lookup=no_lookup)
    assert merged == ["OLDSMALL"], "the cap floor admits new names; it never evicts old ones"


def test_unlisted_screener_rows_are_not_admitted():
    merged, _ = build_universe.build([], {"REAL"}, [("GHOST", 5 * BIG, "ghost inc")], size_lookup=no_lookup)
    assert merged == []


def test_one_line_per_company():
    """A second share class never joins: not beside an old class, not beside a bigger new one."""
    old = ["GOOGL"]
    listed = {"GOOGL", "GOOG", "NEWB-A", "NEWB-B"}
    stocks = [
        ("GOOGL", 10 * BIG, "alphabet inc."),
        ("GOOG", 10 * BIG, "alphabet inc."),  # sibling of an existing line
        ("NEWB-A", 5 * BIG, "newberry inc."),  # brand-new dual-class issuer:
        ("NEWB-B", 4 * BIG, "newberry inc."),  # only the largest class joins
    ]
    merged, summary = build_universe.build(old, listed, stocks, size_lookup=no_lookup)
    assert merged == ["GOOGL", "NEWB-A"]
    assert summary["added"] == 1


def test_issuer_of_strips_share_class_decorations():
    issuer = build_universe.issuer_of
    assert issuer("Alphabet Inc. Class C Capital Stock") == "alphabet inc."
    assert issuer("Alphabet Inc. Depositary Shares representing a 1/20th Interest") == "alphabet inc."
    assert issuer("Berkshire Hathaway Inc.") == issuer("Berkshire Hathaway Inc. ")
    assert issuer("SK hynix Inc. American Depositary Shares") == "sk hynix inc."
    # Different companies stay different.
    assert issuer("Agilent Technologies Inc. Common Stock") != issuer("Alcoa Corporation Common Stock")


def test_capless_listed_old_names_are_sized_by_the_lookup():
    """ETFs are absent from the stock screener; the lookup (Yahoo fund assets) ranks them."""
    old = ["SPY", "TINY"]
    listed = {"SPY", "TINY"}
    stocks = [("TINY", BIG, "tiny inc")]
    merged, _ = build_universe.build(old, listed, stocks, size_lookup=lambda t: 100 * BIG if t == "SPY" else 0.0)
    assert merged == ["SPY", "TINY"]


def test_delisted_names_keep_their_old_relative_order_at_the_tail():
    old = ["LIVE", "DEAD1", "DEAD2"]
    merged, _ = build_universe.build(old, {"LIVE"}, [("LIVE", BIG, "live inc")], size_lookup=no_lookup)
    assert merged == ["LIVE", "DEAD1", "DEAD2"]


def test_excluded_name_pattern_catches_derivatives_not_lookalikes():
    pattern = build_universe.EXCLUDED_NAME
    assert pattern.search("Foo Corp Warrants expiring 2030")
    assert pattern.search("Bar Acquisition Corp Unit")
    assert pattern.search("Baz Industries 6% Preferred Stock")
    assert pattern.search("Qux Inc Common Stock When Issued")
    assert not pattern.search("Wright Medical Group")  # 'right' only as a word
    assert not pattern.search("Unitil Corporation")
    assert not pattern.search("United Airlines Holdings")


def test_the_shipped_universe_is_well_formed():
    """The committed file: unique Yahoo-style symbols, one per line, plausibly cap-ordered."""
    import csv
    import re

    path = pathlib.Path(__file__).resolve().parent.parent / "wolvwealth" / "data" / "ticker_universe.csv"
    with open(path, newline="", encoding="utf-8") as handle:
        tickers = [row[0] for row in csv.reader(handle) if row and row[0].strip()]
    assert len(tickers) == len(set(tickers)), "no ticker twice"
    assert all(re.fullmatch(r"[A-Z0-9-]+", ticker) for ticker in tickers), "Yahoo-style symbols only"
    assert len(tickers) > 1500, "the whole point is breadth"
