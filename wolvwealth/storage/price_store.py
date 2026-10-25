"""PriceStore: the on-disk price history and ticker universe.

Owns the file format. If the cache ever moves from CSV to Parquet, this is
the only module that changes.
"""
import csv
import pathlib
import shutil

import pandas as pd


class PriceStore:
    """Read and write the price cache and the ticker list."""

    def __init__(self, prices_path: pathlib.Path, universe_path: pathlib.Path, legacy_prices_path=None) -> None:
        """Remember paths; nothing is read until asked."""
        self.prices_path = pathlib.Path(prices_path)
        self.universe_path = pathlib.Path(universe_path)
        self.legacy_prices_path = pathlib.Path(legacy_prices_path) if legacy_prices_path else None

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

    def migrate_legacy_prices(self) -> bool:
        """Copy a pre-existing in-repo price file into the cache location, byte for byte."""
        if self.has_prices() or not self.legacy_prices_path or not self.legacy_prices_path.exists():
            return False
        self.prices_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.legacy_prices_path, self.prices_path)
        return True

    def load_prices(self) -> pd.DataFrame:
        """Read the cache into a date-indexed DataFrame."""
        return pd.read_csv(self.prices_path, parse_dates=True, index_col="Date")

    def save_prices(self, prices: pd.DataFrame) -> None:
        """Overwrite the cache."""
        self.prices_path.parent.mkdir(parents=True, exist_ok=True)
        prices.to_csv(self.prices_path, index=True)
