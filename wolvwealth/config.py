"""WolvWealth local configuration.

Every path is absolute and derived from the install location, so the app,
the API, and the ``wolvwealth`` command work from any working directory.
"""

import os
import pathlib
import secrets

# Root of this application, useful if it doesn't occupy an entire domain
APPLICATION_ROOT = '/'

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
WOLVWEALTH_ROOT = PACKAGE_DIR.parent

# Per-machine state (database, cookie key, price cache). Never in git.
# WOLVWEALTH_VAR overrides the location (used by the tests for a hermetic run).
VAR_FOLDER = pathlib.Path(os.environ.get("WOLVWEALTH_VAR", WOLVWEALTH_ROOT/'var'))
VAR_FOLDER.mkdir(parents=True, exist_ok=True)

# Key for signing the session cookie (which only stores the active workspace
# name). Generated once per install and kept in var/, outside of git, so no
# shared secret ships with the repo.
_SECRET_KEY_FILE = VAR_FOLDER/'secret_key'
if _SECRET_KEY_FILE.exists():
    SECRET_KEY = _SECRET_KEY_FILE.read_bytes()
else:
    SECRET_KEY = secrets.token_bytes(32)
    _SECRET_KEY_FILE.write_bytes(SECRET_KEY)

SESSION_COOKIE_NAME = 'wolvwealth'

# Database file (created on first run from the SQL shipped in the package)
DATABASE_FILENAME = VAR_FOLDER/'wolvwealth.sqlite3'
SQL_DIR = PACKAGE_DIR/'sql'

# Market data. The ticker universe is small and ships inside the package; the
# 40 MB price history lives in var/ (built on first run, or migrated from the
# old in-repo copy if one is present).
TICKER_UNIVERSE_FILE = PACKAGE_DIR/'data'/'ticker_universe.csv'
HISTORICAL_PRICES_FILE = VAR_FOLDER/'historical_prices.csv'
LEGACY_HISTORICAL_PRICES_FILE = WOLVWEALTH_ROOT/'historical_prices.csv'

# On startup, refresh prices automatically if the newest row is older than
# this many days. Set the environment variable WOLVWEALTH_NO_AUTO_REFRESH
# to any value to disable (e.g. offline use, tests).
AUTO_REFRESH_MAX_AGE_DAYS = 7
