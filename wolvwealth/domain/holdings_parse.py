"""Parse bulk holdings text (pasted lines, CSV, brokerage exports).

Framework-free on purpose: the browser view and the JSON API both feed text
in and get a ParsedHoldings back, then decide how to report problems.

Accepted shapes, detected per input:

- Two columns, no header, any of comma / tab / semicolon / whitespace between
  them: ``AAPL, 10`` or ``AAPL 10``. A ``ticker=shares`` or ``ticker: shares``
  pair also works.
- CSV with a header row. The ticker column is the first header matching
  ``symbol``/``ticker``, the share column the first matching ``quantity``/
  ``shares``/``qty``/``units``. Extra columns (price, value, cost basis...) are
  ignored, so a Fidelity, Schwab, Vanguard, or Robinhood positions export can
  be dropped in as-is.
- Numbers may carry ``$``, thousands separators, and surrounding quotes.

Special rows:

- A ticker of ``CASH`` sets the portfolio's cash instead of a holding.
- Rows with no parseable share count (summary/total lines, Schwab's ``--``)
  are skipped and listed in ``ignored``.
- Blank lines and lines starting with ``#`` are skipped silently.
"""

import csv
import dataclasses
import io
import re

from wolvwealth.domain.numbers import is_amount

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
_NUMBER_CLEAN_RE = re.compile(r"[,$\s]")
_SYMBOL_HEADERS = ("symbol", "ticker", "stock")
_SHARES_HEADERS = ("quantity", "shares", "qty", "units", "position")
_CASH_TICKERS = {"CASH", "USD", "$CASH"}


@dataclasses.dataclass
class ParsedHoldings:
    """Outcome of parsing one block of holdings text."""

    holdings: dict = dataclasses.field(default_factory=dict)  # ticker -> shares
    cash: float = None  # set only when a CASH row was present
    errors: list = dataclasses.field(default_factory=list)  # "line N: ..." strings
    ignored: list = dataclasses.field(default_factory=list)  # "line N: ..." strings
    unknown: list = dataclasses.field(default_factory=list)  # tickers not in the universe

    @property
    def ok(self) -> bool:
        """True when nothing blocks writing the result to a portfolio."""
        return not self.errors and not self.unknown


def parse_number(raw: str):
    """Parse ``1,234.5``, ``$12``, ``"3"`` to a float, or return None."""
    if raw is None:
        return None
    cleaned = _NUMBER_CLEAN_RE.sub("", str(raw)).strip('"').strip("'")
    if cleaned in ("", "-", "--"):
        return None
    if cleaned.startswith("(") and cleaned.endswith(")"):  # accounting negative
        cleaned = "-" + cleaned[1:-1]
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_ticker(raw: str) -> str:
    """Upper-case a symbol and drop brokerage decorations like ``SPAXX**``."""
    return (raw or "").strip().strip('"').strip("'").upper().rstrip("*")


def _header_index(header: list, candidates: tuple):
    """Return the index of the first header cell containing a candidate word."""
    lowered = [cell.strip().strip('"').lower() for cell in header]
    for candidate in candidates:
        for idx, cell in enumerate(lowered):
            if cell == candidate or cell.startswith(candidate + " ") or cell.endswith(" " + candidate):
                return idx
    return None


def _split_line(line: str) -> list:
    """Split one headerless line into cells on comma, tab, semicolon, = or : then whitespace."""
    for sep in (",", "\t", ";", "=", ":"):
        if sep in line:
            return [cell.strip() for cell in line.split(sep)]
    return line.split()


def _rows(text: str):
    """Yield (line_number, cells) for every non-blank, non-comment line.

    Uses the csv module when the text looks like real CSV (so quoted cells with
    embedded commas survive), otherwise a simple per-line split.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if "," in text:
        reader = csv.reader(io.StringIO("\n".join(lines)))
        for number, cells in enumerate(reader, start=1):
            if not cells or not "".join(cells).strip() or cells[0].lstrip().startswith("#"):
                continue
            if len(cells) == 1:  # a tab/space-separated line inside otherwise-CSV text
                cells = _split_line(cells[0])
            yield number, [cell.strip() for cell in cells]
        return
    for number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        yield number, _split_line(line)


def parse_holdings(text: str, universe=None) -> ParsedHoldings:
    """Parse holdings text into a ParsedHoldings.

    ``universe`` (an iterable of valid tickers) is optional; when given, every
    ticker not in it lands in ``.unknown`` and ``.ok`` becomes False.
    Duplicate tickers are summed, since brokerage exports often list the same
    symbol across lots.
    """
    result = ParsedHoldings()
    if not isinstance(text, str) or not text.strip():
        result.errors.append("No holdings given.")
        return result
    known = set(universe) if universe is not None else None

    rows = list(_rows(text))
    symbol_col, shares_col = 0, 1
    if rows:
        first_cells = rows[0][1]
        header_symbol = _header_index(first_cells, _SYMBOL_HEADERS)
        header_shares = _header_index(first_cells, _SHARES_HEADERS)
        if header_symbol is not None and header_shares is not None:
            symbol_col, shares_col = header_symbol, header_shares
            rows = rows[1:]
        elif header_symbol is not None or header_shares is not None:
            result.errors.append(
                "line 1: header found but it needs both a symbol/ticker column and a quantity/shares column."
            )
            return result

    for number, cells in rows:
        _parse_row(result, number, cells, symbol_col, shares_col, known)

    if not result.holdings and result.cash is None and not result.errors and not result.unknown:
        result.errors.append("No holdings found in the input.")
    return result


def _parse_row(result: ParsedHoldings, number: int, cells: list, symbol_col: int, shares_col: int, known) -> None:
    """Classify one data row into a holding, cash, an error, an unknown ticker, or an ignored line."""
    if len(cells) <= max(symbol_col, shares_col):
        reason = "no share count" if len(cells) == 1 and cells[0] else "too few columns"
        result.ignored.append(f"line {number}: {reason}")
        return
    ticker = normalize_ticker(cells[symbol_col])
    shares = parse_number(cells[shares_col])
    if not ticker:
        result.ignored.append(f"line {number}: empty symbol")
        return
    if shares is None:
        result.ignored.append(f"line {number}: no share count for {ticker}")
        return
    if shares < 0:
        result.errors.append(f"line {number}: {ticker} has negative shares ({shares:g}); shorts are not supported")
        return
    if not is_amount(shares):
        result.errors.append(f"line {number}: {ticker} has an unusable amount ({cells[shares_col].strip()})")
        return
    if ticker in _CASH_TICKERS:
        result.cash = round((result.cash or 0.0) + shares, 2)
        return
    if not TICKER_RE.match(ticker):
        result.errors.append(f"line {number}: '{ticker}' is not a valid ticker symbol")
        return
    if known is not None and ticker not in known:
        if ticker not in result.unknown:
            result.unknown.append(ticker)
        return
    result.holdings[ticker] = result.holdings.get(ticker, 0.0) + shares
