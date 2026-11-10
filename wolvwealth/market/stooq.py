"""Daily closes from stooq.com: the second opinion on a flagged price move.

One price series cannot say whether a 6x day was a real repricing or a
corrupt print; two independent ones can. Stooq runs its own data pipeline,
serves plain CSV without a key or an account, and carries decades of US
daily history -- which makes it the cheapest possible second witness. It is
consulted only for the handful of tickers a refresh flags, never for bulk
downloads, so its informal rate limits are never approached.

Everything about this source is advisory: a request that fails, a ticker it
does not carry, or a payload that does not parse all mean "no answer",
never an error, and the caller falls back to judging on one source alone.
"""

import io
import urllib.request

import pandas as pd

from wolvwealth.domain.market import clean_prices

TIMEOUT_SECONDS = 15
#: Decades of daily rows for one ticker is ~100 KB of CSV; anything within
#: sight of this cap is not a price file and is not worth parsing.
MAX_BYTES = 4_000_000


def download_closes(ticker: str):
    """Return ``ticker``'s daily closes per stooq.com, oldest first, or None if unavailable. Never raises.

    Stooq names US listings ``aapl.us`` and writes share classes with a
    dash (``brk-b.us`` for BRK.B).
    """
    symbol = str(ticker).strip().lower().replace(".", "-")
    url = f"https://stooq.com/q/d/l/?s={symbol}.us&i=d"
    request = urllib.request.Request(url, headers={"User-Agent": "WolvWealth (one-ticker price verification)"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = response.read(MAX_BYTES + 1)
    except Exception:  # pylint: disable=broad-except  # DNS, TLS, HTTP, timeout: all mean the same "no answer"
        return None
    return parse_closes(payload, ticker)


def parse_closes(payload: bytes, ticker: str):
    """Return the closes in a downloaded CSV payload, or None if it is not a usable price file. Never raises.

    The same rules every price from the network passes (``clean_prices``):
    dated rows, positive finite closes, nothing absurd. A second opinion
    built from numbers that would not be accepted as a first one is worth
    nothing.
    """
    if not payload or len(payload) > MAX_BYTES:
        return None
    try:
        frame = pd.read_csv(io.BytesIO(payload))
    except Exception:  # pylint: disable=broad-except  # an HTML error page, an empty body: not a price file
        return None
    if "Date" not in frame.columns or "Close" not in frame.columns:
        return None
    try:
        cleaned = clean_prices(frame.set_index("Date")[["Close"]].rename(columns={"Close": str(ticker).upper()}))
    except (ValueError, TypeError):
        return None
    return cleaned[str(ticker).upper()]


#: Named in logs and exclusion reasons, so a reader knows which source disagreed.
download_closes.source_name = "stooq"
