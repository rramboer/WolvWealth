"""PriceStore: the on-disk price history, precomputed statistics, and ticker universe.

Owns the file formats. Prices and statistics are Parquet (the old CSV cache
took seconds to parse on every process start); a cache left behind by a CSV
install is converted in place on first load. The statistics cache is a pair
of files: the matrix itself, and a small JSON sidecar recording which prices
it was computed from. The sidecar is written last, so a crash mid-save leaves
no sidecar and the cache reads as absent rather than stale.

Every write lands through a temporary file and an atomic rename. A refresh
can run in the background while the web app serves requests and while an
agent invokes the CLI from another process, and none of them may ever read a
half-written price history.
"""

import csv
import io
import json
import os
import pathlib

import pandas as pd

from wolvwealth.domain.errors import DataError
from wolvwealth.domain.market import clean_prices
from wolvwealth.domain.holdings_parse import TICKER_RE
from wolvwealth.domain.numbers import is_number, is_plausible_tbill_rate

#: Column holding mu in the stats file; lowercase so it cannot collide with a ticker.
EXPECTED_RETURN_COLUMN = "expected_return"


def _replace(path: pathlib.Path, write) -> None:
    """Build the file beside its destination, then move it into place atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.partial")
    try:
        write(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _clean_day(value):
    """Return ``value`` as a ``YYYY-MM-DD`` string, or None if it is not a date.

    ``pd.Timestamp(None)`` is NaT rather than an error, and ``str`` of that
    is the plausible-looking ``"NaT"``, so missing has to be rejected
    explicitly or an absent field becomes a date that blanks nothing.
    """
    if value is None:
        return None
    try:
        stamp = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(stamp) else str(stamp.date())


def _clean_exclusion(entry: dict) -> dict:
    """Return one quarantine entry with only well-formed fields kept (see ``PriceStore.load_quarantine``)."""
    clean = {
        "reason": str(entry.get("reason", "")),
        "detected": str(entry.get("detected", "")),
        # Unknown or absent reads as the strongest scope: a record from
        # before partial exclusions existed excluded the whole ticker.
        "scope": "partial" if entry.get("scope") == "partial" else "ticker",
    }
    valuation = _clean_valuation(entry.get("valuation"))
    if valuation is not None:
        clean["valuation"] = valuation
    if clean["scope"] == "partial":
        before = _clean_day(entry.get("untrusted_before"))
        if before is not None:
            clean["untrusted_before"] = before
        days = entry.get("masked_days")
        masked = sorted({d for d in (_clean_day(v) for v in days) if d}) if isinstance(days, list) else []
        if masked:
            clean["masked_days"] = masked
        if "untrusted_before" not in clean and not masked:
            # A partial exclusion that excludes nothing would blank nothing
            # and hide nothing; treat the entry as unusable, not as a licence.
            clean["scope"] = "ticker"
    return clean


def _clean_valuation(valuation):
    """Return ``{"close", "as_of"}`` if it is a believable dated close, else None."""
    if not isinstance(valuation, dict):
        return None
    close, as_of = valuation.get("close"), valuation.get("as_of")
    if not is_number(close) or close <= 0:
        return None
    try:
        day = pd.Timestamp(as_of)
    except (ValueError, TypeError):
        return None
    return {"close": float(close), "as_of": str(day.date())}


class PriceStore:  # pylint: disable=too-many-instance-attributes  # one attribute per file it owns; see below
    """Read and write the price cache, the statistics cache, and the ticker list.

    Every attribute is the location of one file this store owns, named
    after it. Folding them into a mapping would only trade a named
    attribute for a string key at every call site.
    """

    def __init__(self, prices_path: pathlib.Path, universe_path: pathlib.Path, legacy_csv_paths=()) -> None:
        """Remember paths; nothing is read until asked."""
        self.prices_path = pathlib.Path(prices_path)
        self.universe_path = pathlib.Path(universe_path)
        self.legacy_csv_paths = tuple(pathlib.Path(p) for p in legacy_csv_paths)
        self.stats_path = self.prices_path.with_name("market_stats.parquet")
        self.stats_meta_path = self.prices_path.with_name("market_stats.json")
        self.tbill_path = self.prices_path.with_name("tbill_rate.json")
        self.quarantine_path = self.prices_path.with_name("quarantine.json")
        self.readjustments_path = self.prices_path.with_name("readjustments.json")
        self.accepted_path = self.prices_path.with_name("accepted_moves.json")

    def load_universe(self) -> list:
        """Tickers, one per line, in file order (descending market cap); upper-cased, first occurrence wins.

        Raises DataError if the file is missing, unreadable, or lists nothing:
        every price, statistic and alias hangs off this list.
        """
        remedy = "Restore it from the WolvWealth checkout (`git checkout -- wolvwealth/data/ticker_universe.csv`)."
        try:
            with open(self.universe_path, newline="", encoding="utf-8") as handle:
                cells = [row[0] for row in csv.reader(handle) if row]
        except (OSError, UnicodeDecodeError, csv.Error) as err:
            raise DataError(f"The ticker list {self.universe_path} is unreadable ({err}). {remedy}") from err
        # Only symbol-shaped entries: the list is rebuilt from downloaded feeds
        # and flows into requests, file names in errors, and every page.
        tickers = list(dict.fromkeys(t for t in (cell.strip().upper() for cell in cells) if TICKER_RE.match(t)))
        if not tickers:
            raise DataError(f"The ticker list {self.universe_path} is empty. {remedy}")
        return tickers

    def save_universe(self, tickers) -> None:
        """Overwrite the ticker list."""
        buffer = io.StringIO(newline="")
        csv.writer(buffer).writerows([t] for t in tickers)
        _replace(self.universe_path, lambda path: path.write_text(buffer.getvalue(), encoding="utf-8", newline=""))

    def has_prices(self) -> bool:
        """Return true if a price cache exists."""
        return self.prices_path.exists()

    def prices_stamp(self):
        """Return an identity for the price file as it is on disk right now, or None if there is none.

        Modification time plus size: enough to notice that another process
        (the web app, ``refresh-data``, a second MCP server) replaced the
        cache, which a long-lived process must notice to stop serving stale
        prices. Both change on every atomic replace.
        """
        try:
            stat = self.prices_path.stat()
        except FileNotFoundError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def migrate_legacy_prices(self) -> bool:
        """Convert the first price CSV left behind by an older install (numbers preserved, no re-download)."""
        if self.has_prices():
            return False
        for legacy in self.legacy_csv_paths:
            if legacy.exists():
                try:
                    prices = clean_prices(pd.read_csv(legacy, parse_dates=True, index_col="Date"))
                except (OSError, ValueError, UnicodeDecodeError) as err:
                    raise DataError(
                        f"The old price file {legacy} cannot be converted ({err}). Delete it and run "
                        "`wolvwealth refresh-data` to download the history again."
                    ) from err
                self.save_prices(prices)
                return True
        return False

    def load_prices(self) -> pd.DataFrame:
        """Read the cache into a cleaned, date-indexed DataFrame (see ``clean_prices``).

        Raises DataError if the file cannot be read or holds no usable
        history; deleting it makes the next start download a fresh one.
        """
        remedy = f"Delete {self.prices_path} and run `wolvwealth refresh-data` to download it again."
        try:
            prices = pd.read_parquet(self.prices_path)
        except Exception as err:  # pylint: disable=broad-except  # pyarrow raises its own types for every way to fail
            raise DataError(f"The price history is unreadable ({err}). {remedy}") from err
        try:
            return clean_prices(prices)
        except ValueError as err:
            raise DataError(f"The price history is unusable: {err} {remedy}") from err

    def save_prices(self, prices: pd.DataFrame) -> None:
        """Overwrite the cache."""
        _replace(self.prices_path, lambda path: prices.to_parquet(path, index=True))

    def load_tbill(self):
        """Return ``{"rate": fraction, "as_of": "YYYY-MM-DD"}`` for the cached T-bill yield, or None."""
        if not self.tbill_path.exists():
            return None
        try:
            data = json.loads(self.tbill_path.read_text(encoding="utf-8"))
            rate, as_of = data["rate"], data["as_of"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not (is_plausible_tbill_rate(rate) and isinstance(as_of, str)):
            return None  # hand-edited or from a buggy build: absent, so the next refresh rewrites it
        return {"rate": float(rate), "as_of": as_of}

    def save_tbill(self, rate: float, as_of: str) -> None:
        """Overwrite the cached T-bill yield."""
        payload = json.dumps({"rate": rate, "as_of": as_of})
        _replace(self.tbill_path, lambda path: path.write_text(payload, encoding="utf-8"))

    def load_quarantine(self) -> dict:
        """Return ``{ticker: entry}`` for excluded prices; {} if none or unreadable.

        An entry always has ``reason``, ``detected`` and ``scope``, and
        optionally:

        - ``valuation`` (``{"close", "as_of"}``) -- a price on the *current*
          adjustment basis for a ticker excluded because a split/dividend
          re-download failed, whose cached history is therefore on a
          superseded basis and cannot be used to value a holding.
        - ``untrusted_before`` / ``masked_days`` -- the partial exclusions
          (see ``domain.market.classify_implausible``).

        ``scope`` defaults to ``"ticker"``, so a record written before
        partial exclusions existed keeps meaning exactly what it did. Every
        field is dropped unless it is well formed, so a hand-edited or
        truncated file degrades to a weaker exclusion rather than to a
        wrong one.
        """
        try:
            data = json.loads(self.quarantine_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        record = {}
        for ticker, entry in data.items():
            if not (isinstance(ticker, str) and TICKER_RE.match(ticker) and isinstance(entry, dict)):
                continue
            record[ticker] = _clean_exclusion(entry)
        return record

    def save_quarantine(self, record: dict) -> None:
        """Overwrite the quarantine list (removing the file when it is empty)."""
        if not record:
            self.quarantine_path.unlink(missing_ok=True)
            return
        payload = json.dumps(record, indent=2, sort_keys=True)
        _replace(self.quarantine_path, lambda path: path.write_text(payload, encoding="utf-8"))

    def load_accepted_moves(self) -> dict:
        """Return ``{ticker: {date: who decided}}`` of price moves judged real; {} if none or unreadable.

        The adjudication layer over a heuristic that cannot be right every
        time: a genuine one-day collapse or repricing is indistinguishable
        from corruption in a single series, so whoever settled it -- a person
        via ``accept-price``, or a second price source that showed the same
        move -- records that here, once, with the note saying which, and no
        later refresh re-decides it. Deleting a line un-decides it. A file
        from before the notes existed (``{ticker: [date, ...]}``) reads as
        the same dates with empty notes.
        """
        try:
            data = json.loads(self.accepted_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        accepted = {}
        for ticker, days in data.items():
            if not (isinstance(ticker, str) and TICKER_RE.match(ticker)):
                continue
            if isinstance(days, list):
                days = {day: "" for day in days}
            if not isinstance(days, dict):
                continue
            clean = {day: str(note) for day, note in ((_clean_day(d), n) for d, n in days.items()) if day}
            if clean:
                accepted[ticker] = dict(sorted(clean.items()))
        return accepted

    def save_accepted_moves(self, accepted: dict) -> None:
        """Overwrite the accepted-move list (removing the file when it is empty)."""
        if not accepted:
            self.accepted_path.unlink(missing_ok=True)
            return
        payload = json.dumps(accepted, indent=2, sort_keys=True)
        _replace(self.accepted_path, lambda path: path.write_text(payload, encoding="utf-8"))

    def load_readjustments(self) -> dict:
        """Return ``{ticker: {"detected", "outcome"}}`` for recent split/dividend re-downloads; {} if unreadable."""
        try:
            data = json.loads(self.readjustments_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            ticker: {"detected": str(entry.get("detected", "")), "outcome": str(entry.get("outcome", ""))}
            for ticker, entry in data.items()
            if isinstance(ticker, str) and TICKER_RE.match(ticker) and isinstance(entry, dict)
        }

    def save_readjustments(self, record: dict) -> None:
        """Overwrite the re-adjustment notes (removing the file when there are none)."""
        if not record:
            self.readjustments_path.unlink(missing_ok=True)
            return
        payload = json.dumps(record, indent=2, sort_keys=True)
        _replace(self.readjustments_path, lambda path: path.write_text(payload, encoding="utf-8"))

    def load_stats(self):
        """Return ``(meta, mu, cov)`` from the statistics cache, or None if it is absent."""
        if not (self.stats_path.exists() and self.stats_meta_path.exists()):
            return None
        meta = json.loads(self.stats_meta_path.read_text(encoding="utf-8"))
        frame = pd.read_parquet(self.stats_path)
        return meta, frame[EXPECTED_RETURN_COLUMN], frame.drop(columns=[EXPECTED_RETURN_COLUMN])

    def save_stats(self, mu: pd.Series, cov: pd.DataFrame, meta: dict) -> None:
        """Overwrite the statistics cache; ``meta`` names the prices it was computed from."""
        frame = cov.copy()
        frame[EXPECTED_RETURN_COLUMN] = mu
        # Sidecar last: until it lands, load_stats() reports the cache as absent.
        _replace(self.stats_path, lambda path: frame.to_parquet(path, index=True))
        _replace(self.stats_meta_path, lambda path: path.write_text(json.dumps(meta), encoding="utf-8"))
