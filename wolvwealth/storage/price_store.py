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
import json
import os
import pathlib

import pandas as pd

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

    def load_universe(self) -> list:
        """Tickers, one per line, in file order (descending market cap)."""
        with open(self.universe_path, newline="", encoding="utf-8") as handle:
            return [row[0].strip() for row in csv.reader(handle) if row and row[0].strip()]

    def save_universe(self, tickers) -> None:
        """Overwrite the ticker list."""
        with open(self.universe_path, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows([t] for t in tickers)

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
                self.save_prices(pd.read_csv(legacy, parse_dates=True, index_col="Date"))
                return True
        return False

    def load_prices(self) -> pd.DataFrame:
        """Read the cache into a date-indexed DataFrame."""
        return pd.read_parquet(self.prices_path)

    def save_prices(self, prices: pd.DataFrame) -> None:
        """Overwrite the cache."""
        _replace(self.prices_path, lambda path: prices.to_parquet(path, index=True))

    def load_tbill(self):
        """Return ``{"rate": fraction, "as_of": "YYYY-MM-DD"}`` for the cached T-bill yield, or None."""
        if not self.tbill_path.exists():
            return None
        try:
            data = json.loads(self.tbill_path.read_text(encoding="utf-8"))
            return {"rate": float(data["rate"]), "as_of": str(data["as_of"])}
        except (ValueError, KeyError, TypeError):
            return None

    def save_tbill(self, rate: float, as_of: str) -> None:
        """Overwrite the cached T-bill yield."""
        payload = json.dumps({"rate": rate, "as_of": as_of})
        _replace(self.tbill_path, lambda path: path.write_text(payload, encoding="utf-8"))

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
