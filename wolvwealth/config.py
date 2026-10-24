"""WolvWealth local configuration."""

import pathlib
import secrets

# Root of this application, useful if it doesn't occupy an entire domain
APPLICATION_ROOT = '/'

WOLVWEALTH_ROOT = pathlib.Path(__file__).resolve().parent.parent
VAR_FOLDER = WOLVWEALTH_ROOT/'var'
VAR_FOLDER.mkdir(exist_ok=True)

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

# Database file is var/wolvwealth.sqlite3 (created on first run, not in git)
DATABASE_FILENAME = VAR_FOLDER/'wolvwealth.sqlite3'

# Market data. Absolute paths so the app and the refresh command work from
# any working directory, not just the repo root. The ticker universe is small
# and ships with the repo; the 40 MB price history lives in var/ outside of
# git (built on first run, or migrated from the old in-repo copy if present).
TICKER_UNIVERSE_FILE = WOLVWEALTH_ROOT/'ticker_universe.csv'
HISTORICAL_PRICES_FILE = VAR_FOLDER/'historical_prices.csv'
LEGACY_HISTORICAL_PRICES_FILE = WOLVWEALTH_ROOT/'historical_prices.csv'

# On startup, refresh prices automatically if the newest row is older than
# this many days. Set the environment variable WOLVWEALTH_NO_AUTO_REFRESH
# to any value to disable (e.g. offline use, tests).
AUTO_REFRESH_MAX_AGE_DAYS = 7
