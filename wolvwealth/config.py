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
