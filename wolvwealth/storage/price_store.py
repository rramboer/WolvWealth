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
from wolvwealth.domain.numbers import is_plausible_tbill_rate

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


class PriceStore:
    """Read and write the price cache, the statistics cache, and the ticker list."""

    def __init__(self, prices_path: pathlib.Path, universe_path: pathlib.Path, legacy_csv_paths=()) -> None:
        """Remember paths; nothing is read until asked."""
        self.prices_path = pathlib.Path(prices_path)
        self.universe_path = pathlib.Path(universe_path)
        self.legacy_csv_paths = tuple(pathlib.Path(p) for p in legacy_csv_paths)
        self.stats_path = self.prices_path.with_name("market_stats.parquet")
        self.stats_meta_path = self.prices_path.with_name("market_stats.json")
        self.tbill_path = self.prices_path.with_name("tbill_rate.json")
        self.quarantine_path = self.prices_path.with_name("quarantine.json")

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
        """Return ``{ticker: {"reason", "detected"}}`` for tickers excluded as implausible; {} if none or unreadable."""
        try:
            data = json.loads(self.quarantine_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            ticker: {"reason": str(entry.get("reason", "")), "detected": str(entry.get("detected", ""))}
            for ticker, entry in data.items()
            if isinstance(ticker, str) and TICKER_RE.match(ticker) and isinstance(entry, dict)
        }

    def save_quarantine(self, record: dict) -> None:
        """Overwrite the quarantine list (removing the file when it is empty)."""
        if not record:
            self.quarantine_path.unlink(missing_ok=True)
            return
        payload = json.dumps(record, indent=2, sort_keys=True)
        _replace(self.quarantine_path, lambda path: path.write_text(payload, encoding="utf-8"))

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
