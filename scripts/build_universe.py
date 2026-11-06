"""Rebuild wolvwealth/data/ticker_universe.csv from today's US listings.

Usage: python scripts/build_universe.py [--dry-run]

The universe file is one ticker per line in descending market-cap order --
the order *is* the ranking that ``top500`` and friends slice -- so rebuilding
it needs both a current list of listed symbols and a size to sort by. Three
sources, all fetched live:

* The NASDAQ Trader symbol directories (nasdaqlisted/otherlisted): the
  authoritative set of everything currently listed, with ETF and test-issue
  flags. A ticker must appear here to be added.
* The NASDAQ stock screener: market cap for every listed common stock, which
  provides the ordering and the admission cut for new names.
* Yahoo (the app's own price source), only for the handful of existing
  tickers the screener cannot size: ETFs (sized by fund assets) and a few
  stocks the screener lists without a cap.

Rules, in the spirit of AGENTS.md's "never prune the universe":

* **Nothing already in the file is ever dropped.** Delisted names keep their
  price history and any stored holdings; ``MarketData.tradeable`` already
  filters them from live use. They sink to the bottom of the file (no
  current size), keeping their old relative order.
* **New names need a market cap of at least NEW_TICKER_CAP_FLOOR**, must be
  listed in the symbol directories, and must not be a warrant, right, unit,
  preferred, or when-issued line.
* **One line per company.** The file has always carried one share class per
  issuer (GOOGL but not GOOG, BRK-B but not BRK-A), and an optimizer that
  saw both would happily "diversify" across two claims on the same cash
  flows. A new name whose issuer already appears (by security name) is
  skipped; a brand-new issuer contributes only its largest class.

Newly added tickers have no price history in anyone's cache until
``wolvwealth refresh-data --full``; until then they are simply untradeable,
which every layer already tolerates.
"""

import csv
import io
import json
import pathlib
import re
import sys
import urllib.request

import yfinance as yf

from wolvwealth.domain.holdings_parse import TICKER_RE
from wolvwealth.domain.numbers import is_number

UNIVERSE_PATH = pathlib.Path(__file__).resolve().parent.parent / "wolvwealth" / "data" / "ticker_universe.csv"
SYMBOL_DIRECTORY_URLS = (
    # name|...|Test Issue|...|ETF|... pipe-delimited, one line per listing.
    ("https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt", 0, 3, 6),  # symbol, test, etf columns
    ("https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt", 0, 6, 4),
)
SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks?limit=25000&download=true"
#: Every feed is untrusted input that ends up in the shipped ticker list.
#: Responses are size-capped before parsing (the real ones are a few MB),
#: symbols must look like tickers, and sizes must be believable.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
#: Above the largest company or fund ever; a bigger "size" is garbage.
MAX_BELIEVABLE_SIZE = 1e14
#: Smallest market cap a ticker not already in the file needs to join it.
#: Roughly reproduces the original file's reach (~2,000 names in 2024).
NEW_TICKER_CAP_FLOOR = 2e9
#: Security-name fragments that mark non-common-stock lines. Word-ish
#: boundaries so e.g. "Wright" or "Unitil" are not caught.
EXCLUDED_NAME = re.compile(r"\b(warrants?|rights?|units?|when issued|preferred|preference)\b", re.IGNORECASE)
#: Name suffixes cut off to identify the issuer behind a share class, so
#: "Alphabet Inc. Class C Capital Stock" and "Alphabet Inc. Depositary
#: Shares representing ..." both reduce to "alphabet inc."
CLASS_MARKERS = (" class ", " depositary shares", " american depositary shares", " common stock", " ordinary shares")


def fetch(url: str) -> bytes:
    """GET one source; api.nasdaq.com refuses clients without a browser-ish User-Agent."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError(f"{url} sent more than {MAX_RESPONSE_BYTES} bytes; refusing to parse it")
    return body


def to_yahoo_symbol(symbol: str) -> str:
    """NASDAQ writes share classes as BRK/B or BRK.B; Yahoo (and this app) use BRK-B."""
    return symbol.strip().upper().replace("/", "-").replace(".", "-")


def believable_size(value) -> float:
    """A market cap or fund size from a feed as a float, or 0.0 if it is missing, garbled or absurd."""
    try:
        size = float(value)
    except (TypeError, ValueError):
        return 0.0
    return size if is_number(size) and 0 < size <= MAX_BELIEVABLE_SIZE else 0.0


def listed_symbols() -> set:
    """Everything currently listed per the symbol directories, minus test issues."""
    listed = set()
    for url, symbol_column, test_column, _etf_column in SYMBOL_DIRECTORY_URLS:
        for line in fetch(url).decode("utf-8", errors="replace").splitlines()[1:]:
            parts = line.split("|")
            if len(parts) < 7 or parts[0].startswith("File Creation"):
                continue
            symbol = to_yahoo_symbol(parts[symbol_column])
            if parts[test_column] != "Y" and TICKER_RE.match(symbol):
                listed.add(symbol)
    return listed


def issuer_of(name: str) -> str:
    """The company behind a security name, share-class suffixes stripped."""
    lowered = f" {name.strip().lower()}"
    cut = len(lowered)
    for marker in CLASS_MARKERS:
        found = lowered.find(marker)
        if found != -1:
            cut = min(cut, found)
    return lowered[:cut].strip()


def screener_stocks() -> list:
    """Return ``(symbol, cap, issuer)`` for every sizeable common-stock line in the screener."""
    body = json.loads(fetch(SCREENER_URL))
    rows = body.get("data", {}).get("rows") if isinstance(body, dict) and isinstance(body.get("data"), dict) else None
    if not isinstance(rows, list):
        raise ValueError("the stock screener answered without a list of rows")
    stocks = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
            continue
        raw, name = row["symbol"].strip(), row.get("name") if isinstance(row.get("name"), str) else ""
        if "^" in raw or EXCLUDED_NAME.search(name):
            continue  # preferreds, warrants, units, when-issued lines
        symbol, cap = to_yahoo_symbol(raw), believable_size(row.get("marketCap"))
        if cap > 0 and TICKER_RE.match(symbol):
            stocks.append((symbol, cap, issuer_of(name)))
    return stocks


def yahoo_size(ticker: str) -> float:
    """Size an existing ticker the screener cannot: market cap, else fund assets, else 0."""
    try:
        cap = believable_size(yf.Ticker(ticker).fast_info["market_cap"])
        if cap:
            return cap
    except Exception:  # pylint: disable=broad-except  # missing key, delisted, throttled: all mean "no cap here"
        pass
    try:
        return believable_size(yf.Ticker(ticker).info.get("totalAssets"))
    except Exception:  # pylint: disable=broad-except
        return 0.0


def build(old: list, listed: set, stocks: list, size_lookup=yahoo_size) -> tuple:
    """Merge today's listings into the old universe; return ``(tickers, summary)``.

    Pure given its inputs, so the merge rules are testable without the
    network: ``stocks`` is the screener's ``(symbol, cap, issuer)`` rows and
    ``size_lookup`` sizes the stragglers the screener cannot.
    """
    old_order = {ticker: position for position, ticker in enumerate(old)}
    sizes = {symbol: cap for symbol, cap, _ in stocks if symbol in old_order}
    issuers_present = {issuer for symbol, _, issuer in stocks if symbol in old_order}

    additions = {}
    best_of_new_issuer = {}
    for symbol, cap, issuer in sorted(stocks, key=lambda row: -row[1]):
        if symbol in old_order or symbol not in listed or cap < NEW_TICKER_CAP_FLOOR:
            continue
        if issuer in issuers_present or issuer in best_of_new_issuer:
            continue  # one line per company: an existing class, or a larger new one, already covers it
        best_of_new_issuer[issuer] = symbol
        additions[symbol] = cap
    sizes.update(additions)

    for ticker in old:
        if ticker not in sizes and ticker in listed:
            sizes[ticker] = size_lookup(ticker)  # ETFs and screener gaps

    merged = list(old_order) + list(additions)
    # Size first; dead and unsizeable names sink, keeping their old order.
    merged.sort(key=lambda t: (-sizes.get(t, 0.0), old_order.get(t, len(old_order))))
    summary = {
        "kept": len(old),
        "added": len(additions),
        "delisted_retained": sum(1 for t in old if t not in listed),
        "total": len(merged),
    }
    return merged, summary


def main(argv=None) -> int:
    """Fetch, merge, report, and (unless --dry-run) overwrite the universe file."""
    dry_run = "--dry-run" in (argv or sys.argv[1:])
    with open(UNIVERSE_PATH, newline="", encoding="utf-8") as handle:
        old = [row[0].strip() for row in csv.reader(handle) if row and row[0].strip()]
    print(f"Current universe: {len(old)} tickers. Fetching today's listings...")
    try:
        listed, stocks = listed_symbols(), screener_stocks()
    except (OSError, ValueError) as err:  # network failure, oversized or malformed feed
        print(f"Could not fetch today's listings ({err}). Nothing written.", file=sys.stderr)
        return 1
    if not listed or not stocks:
        print("A listing feed came back empty. Nothing written.", file=sys.stderr)
        return 1
    merged, summary = build(old, listed, stocks)
    print(
        f"Kept {summary['kept']} (of which {summary['delisted_retained']} no longer listed, retained at the "
        f"bottom), added {summary['added']}: {summary['total']} tickers."
    )
    print("Top 20:", ", ".join(merged[:20]))
    if dry_run:
        print("Dry run; nothing written.")
        return 0
    buffer = io.StringIO()
    csv.writer(buffer).writerows([ticker] for ticker in merged)
    UNIVERSE_PATH.write_text(buffer.getvalue(), encoding="utf-8")
    print(f"Wrote {UNIVERSE_PATH}.")
    print("New tickers have no cached prices yet: run `wolvwealth refresh-data --full` to download their history.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
