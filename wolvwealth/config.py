"""WolvWealth configuration.

Values may be overridden with environment variables (see README "Configuration")
or a settings file pointed to by the WOLVWEALTH_SETTINGS environment variable.
"""

import os
import pathlib

# Root of this application, useful if it doesn't occupy an entire domain
APPLICATION_ROOT = "/"

# Repository root (parent of the wolvwealth package)
WOLVWEALTH_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Secret key for signing session cookies.
#
# The insecure fallback below is for local development ONLY. Production
# deployments must set WOLVWEALTH_SECRET_KEY, e.g. generated with:
#   python -c "import secrets; print(secrets.token_hex(32))"
DEV_SECRET_KEY = "dev-only-insecure-secret-key"
SECRET_KEY = os.environ.get("WOLVWEALTH_SECRET_KEY", DEV_SECRET_KEY)

# Session cookie hardening
SESSION_COOKIE_NAME = "login"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# Reject request bodies larger than 16 MiB with 413 before parsing them.
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

# SQLite database location. Build it with `./bin/db create`.
DATABASE_FILENAME = pathlib.Path(os.environ.get("WOLVWEALTH_DATABASE", WOLVWEALTH_ROOT / "var" / "wolvwealth.sqlite3"))
