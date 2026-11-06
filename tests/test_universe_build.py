"""The universe-rebuild merge rules (scripts/build_universe.py), offline.

``build`` is pure given its inputs, so every rule that decides who joins the
file, who is skipped, and how everyone is ordered is pinned here without the
network. Fetching is the script's job; deciding is tested.
"""

import csv
import importlib.util
import json
import pathlib
import re

import pytest

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


def test_issuer_of_strips_share_class_decorations_and_symbols_are_written_yahoo_style():
    issuer = build_universe.issuer_of
    assert issuer("Alphabet Inc. Class C Capital Stock") == "alphabet inc."
    assert issuer("Alphabet Inc. Depositary Shares representing a 1/20th Interest") == "alphabet inc."
    assert issuer("Berkshire Hathaway Inc.") == issuer("Berkshire Hathaway Inc. ")
    assert issuer("SK hynix Inc. American Depositary Shares") == "sk hynix inc."
    # Different companies stay different.
    assert issuer("Agilent Technologies Inc. Common Stock") != issuer("Alcoa Corporation Common Stock")
    assert build_universe.to_yahoo_symbol(" BRK/B ") == build_universe.to_yahoo_symbol("BRK.B") == "BRK-B"


def test_the_nasdaq_feeds_are_parsed_into_listed_symbols_and_sized_stocks(monkeypatch):
    """Pipe-delimited symbol directories (test issues dropped) and the screener JSON (derivatives, capless dropped)."""
    nasdaq_url, other_url = (entry[0] for entry in build_universe.SYMBOL_DIRECTORY_URLS)
    payloads = {
        nasdaq_url: (
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
            "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
            "ZTEST|Test Issue|Q|Y|N|100|N|N\n"
            "File Creation Time: 1105202600||||||\n"
        ).encode(),
        other_url: (
            "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
            "BRK.B|Berkshire Hathaway Inc. Class B|N|BRK B|N|100|N|BRK/B\n"
            "SPY|SPDR S&P 500|P|SPY|Y|100|N|SPY\n"
            "ZZT|NYSE Test|N|ZZT|N|100|Y|ZZT\n"
            "<script>|Hostile|N|X|N|100|N|X\n"
            "../../etc|Hostile|N|X|N|100|N|X\n"
            "short|line\n"
        ).encode(),
        build_universe.SCREENER_URL: json.dumps(
            {
                "data": {
                    "rows": [
                        {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "marketCap": "3000000000000"},
                        {"symbol": "BRK/B", "name": "Berkshire Hathaway Inc. Class B", "marketCap": "900000000000"},
                        {"symbol": "FOO^A", "name": "Foo Inc. Preferred", "marketCap": "10"},
                        {"symbol": "BAR", "name": "Bar Corp Warrants", "marketCap": "10"},
                        {"symbol": "NOCAP", "name": "Nocap Inc", "marketCap": "NA"},
                        {"symbol": "ZERO", "name": "Zero Inc", "marketCap": "0"},
                        # Every feed is untrusted: none of these may reach the ticker list.
                        {"symbol": "HUGE", "name": "Huge Inc", "marketCap": "1e400"},
                        {"symbol": "BIGGER", "name": "Bigger Inc", "marketCap": "1e20"},
                        {"symbol": "<img src=x>", "name": "Hostile", "marketCap": "5e9"},
                        {"symbol": 42, "name": None, "marketCap": "5e9"},
                        "not a row",
                    ]
                }
            }
        ).encode(),
    }
    monkeypatch.setattr(build_universe, "fetch", lambda url: payloads[url])
    assert build_universe.listed_symbols() == {"AAPL", "BRK-B", "SPY"}
    assert build_universe.screener_stocks() == [
        ("AAPL", 3e12, "apple inc."),
        ("BRK-B", 9e11, "berkshire hathaway inc."),
    ]


def test_yahoo_size_uses_market_cap_then_fund_assets_then_zero(monkeypatch):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def fast_info(self):
            if self.symbol == "STOCK":
                return {"market_cap": 5e9}
            if self.symbol == "LIAR":
                return {"market_cap": float("inf")}
            if self.symbol == "ETF":
                return {"market_cap": None}
            raise RuntimeError("throttled")

        @property
        def info(self):
            if self.symbol == "ETF":
                return {"totalAssets": 7e9}
            raise RuntimeError("throttled")

    monkeypatch.setattr(build_universe.yf, "Ticker", FakeTicker)
    assert build_universe.yahoo_size("STOCK") == 5e9
    assert build_universe.yahoo_size("ETF") == 7e9
    assert build_universe.yahoo_size("DEAD") == 0.0
    assert build_universe.yahoo_size("LIAR") == 0.0, "an absurd size ranks nothing"


def test_main_reports_the_merge_and_rewrites_the_file_unless_dry_run(monkeypatch, tmp_path, capsys):
    path = tmp_path / "ticker_universe.csv"
    path.write_text("OLD\nDEAD\n", encoding="utf-8")
    monkeypatch.setattr(build_universe, "UNIVERSE_PATH", path)
    monkeypatch.setattr(build_universe, "listed_symbols", lambda: {"OLD", "NEW"})
    monkeypatch.setattr(build_universe, "screener_stocks", lambda: [("OLD", BIG, "old inc"), ("NEW", 2 * BIG, "new")])
    assert build_universe.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Kept 2 (of which 1 no longer listed" in out and "added 1: 3 tickers" in out and "Dry run" in out
    assert path.read_text(encoding="utf-8") == "OLD\nDEAD\n"
    assert build_universe.main([]) == 0
    assert "refresh-data --full" in capsys.readouterr().out
    with open(path, newline="", encoding="utf-8") as handle:
        assert [row[0] for row in csv.reader(handle)] == ["NEW", "OLD", "DEAD"]

    # A failed, empty or malformed feed writes nothing.
    def malformed():
        raise ValueError("the stock screener answered without a list of rows")

    for listed, screener in [(lambda: {"OLD"}, malformed), (lambda: set(), lambda: [("OLD", BIG, "old inc")])]:
        monkeypatch.setattr(build_universe, "listed_symbols", listed)
        monkeypatch.setattr(build_universe, "screener_stocks", screener)
        assert build_universe.main([]) == 1
        assert "Nothing written" in capsys.readouterr().err
    with open(path, newline="", encoding="utf-8") as handle:
        assert [row[0] for row in csv.reader(handle)] == ["NEW", "OLD", "DEAD"]


def test_fetch_refuses_an_oversized_response_and_the_screener_a_malformed_one(monkeypatch):
    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, limit=-1):
            return self.body[:limit]

    huge = b"x" * (build_universe.MAX_RESPONSE_BYTES + 10)
    monkeypatch.setattr(build_universe.urllib.request, "urlopen", lambda *_a, **_k: Response(huge))
    with pytest.raises(ValueError, match="refusing to parse"):
        build_universe.fetch("https://example.invalid/")
    for body in (b'{"data": null}', b"[1, 2]", b'{"data": {"rows": "nope"}}'):
        monkeypatch.setattr(build_universe, "fetch", lambda _url, body=body: body)
        with pytest.raises(ValueError, match="list of rows"):
            build_universe.screener_stocks()


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
    path = pathlib.Path(__file__).resolve().parent.parent / "wolvwealth" / "data" / "ticker_universe.csv"
    with open(path, newline="", encoding="utf-8") as handle:
        tickers = [row[0] for row in csv.reader(handle) if row and row[0].strip()]
    assert len(tickers) == len(set(tickers)), "no ticker twice"
    assert all(re.fullmatch(r"[A-Z0-9-]+", ticker) for ticker in tickers), "Yahoo-style symbols only"
    assert len(tickers) > 1500, "the whole point is breadth"
