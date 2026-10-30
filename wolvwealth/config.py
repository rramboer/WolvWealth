"""Settings: where files live and a few tunables. No side effects at import.

Everything is a plain dataclass built from the environment by
``Settings.from_env()``. Nothing here touches the filesystem or network; the
storage layer creates directories when it first needs them.
"""
import dataclasses
import os
import pathlib

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent


@dataclasses.dataclass(frozen=True)
class Settings:
    """Paths and tunables for one install."""

    #: Local state (database, secret key, price cache). Defaults to var/ next
    #: to the package; WOLVWEALTH_VAR overrides it so tests and scratch runs
    #: can use a throwaway folder without touching the real one.
    var_dir: pathlib.Path = REPO_ROOT / "var"
    #: Ticker symbols, one per line, descending market cap. Ships inside the package.
    ticker_universe_path: pathlib.Path = PACKAGE_DIR / "data" / "ticker_universe.csv"
    #: On startup, refresh prices if the newest cached row is older than this.
    auto_refresh_max_age_days: int = 7
    #: WOLVWEALTH_NO_AUTO_REFRESH disables the startup refresh (offline use, tests).
    auto_refresh: bool = True
    #: Earliest date in the price history.
    price_history_start: str = "2006-01-01"

    @property
    def database_path(self) -> pathlib.Path:
        """Path of the SQLite file holding workspaces, portfolios, and holdings."""
        return self.var_dir / "wolvwealth.sqlite3"

    @property
    def secret_key_path(self) -> pathlib.Path:
        """Per-install key that signs the session cookie."""
        return self.var_dir / "secret_key"

    @property
    def prices_path(self) -> pathlib.Path:
        """Cached daily adjusted closes for the whole universe (Parquet)."""
        return self.var_dir / "historical_prices.parquet"

    @property
    def legacy_csv_paths(self) -> tuple:
        """Price caches from older CSV installs, converted to Parquet on first load.

        Oldest history last: the var/ cache (before the Parquet switch), then
        the in-repo file (before the cache moved out of git).
        """
        return (self.var_dir / "historical_prices.csv", REPO_ROOT / "historical_prices.csv")

    @classmethod
    def from_env(cls, environ=None) -> "Settings":
        """Build settings from environment variables (WOLVWEALTH_VAR, WOLVWEALTH_NO_AUTO_REFRESH)."""
        environ = os.environ if environ is None else environ
        overrides = {}
        if environ.get("WOLVWEALTH_VAR"):
            overrides["var_dir"] = pathlib.Path(environ["WOLVWEALTH_VAR"])
        if environ.get("WOLVWEALTH_NO_AUTO_REFRESH"):
            overrides["auto_refresh"] = False
        return cls(**overrides)
