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

# Backtest API limits: one request may ask for at most this much work.
BACKTEST_MAX_REBALANCES = int(os.environ.get("WOLVWEALTH_BACKTEST_MAX_REBALANCES", 120))
BACKTEST_MAX_UNIVERSE_SIZE = int(os.environ.get("WOLVWEALTH_BACKTEST_MAX_UNIVERSE_SIZE", 100))
BACKTEST_CACHE_SIZE = int(os.environ.get("WOLVWEALTH_BACKTEST_CACHE_SIZE", 64))

# Backtest jobs run on a background thread pool and are polled for progress.
# Jobs live in process memory: keep GUNICORN_PROCESSES=1 (or add sticky routing).
BACKTEST_MAX_WORKERS = int(os.environ.get("WOLVWEALTH_BACKTEST_MAX_WORKERS", 2))
BACKTEST_MAX_PENDING_JOBS = int(os.environ.get("WOLVWEALTH_BACKTEST_MAX_PENDING_JOBS", 20))
BACKTEST_MAX_JOBS_PER_USER = int(os.environ.get("WOLVWEALTH_BACKTEST_MAX_JOBS_PER_USER", 2))
BACKTEST_JOB_TTL_SECONDS = int(os.environ.get("WOLVWEALTH_BACKTEST_JOB_TTL_SECONDS", 900))

# Rate limiting (see wolvwealth/ratelimit.py). Limit strings use the "N per
# period;M per period" syntax of the `limits` library and are read per request.
RATELIMIT_ENABLED = os.environ.get("WOLVWEALTH_RATELIMIT_ENABLED", "true").lower() not in ("0", "false", "no")
# memory:// is per process. With GUNICORN_PROCESSES > 1 use a shared store, e.g. redis://localhost:6379.
RATELIMIT_STORAGE_URI = os.environ.get("WOLVWEALTH_RATELIMIT_STORAGE_URI", "memory://")
RATELIMIT_STRATEGY = "moving-window"
RATELIMIT_HEADERS_ENABLED = True  # X-RateLimit-Limit / -Remaining / -Reset and Retry-After
RATELIMIT_KEY_PREFIX = "wolvwealth"
RATELIMIT_SITE_PER_IP = os.environ.get("WOLVWEALTH_RATELIMIT_SITE_PER_IP", "600 per minute")
RATELIMIT_API_PER_IP = os.environ.get("WOLVWEALTH_RATELIMIT_API_PER_IP", "300 per minute")
# Login: all attempts per IP bound bcrypt CPU; the FAILURES limits count wrong credentials only.
RATELIMIT_LOGIN_PER_IP = os.environ.get("WOLVWEALTH_RATELIMIT_LOGIN_PER_IP", "30 per minute")
RATELIMIT_LOGIN_FAILURES_PER_IP = os.environ.get(
    "WOLVWEALTH_RATELIMIT_LOGIN_FAILURES_PER_IP", "10 per minute;50 per hour"
)
RATELIMIT_LOGIN_FAILURES_PER_ACCOUNT_CLIENT = os.environ.get(
    "WOLVWEALTH_RATELIMIT_LOGIN_FAILURES_PER_ACCOUNT_CLIENT", "5 per minute;20 per hour"
)
# Shared by all clients that have never logged in to the account; the owner's known devices are exempt.
RATELIMIT_LOGIN_FAILURES_PER_ACCOUNT = os.environ.get("WOLVWEALTH_RATELIMIT_LOGIN_FAILURES_PER_ACCOUNT", "20 per hour")
RATELIMIT_REGISTER_PER_IP = os.environ.get("WOLVWEALTH_RATELIMIT_REGISTER_PER_IP", "5 per hour")
RATELIMIT_ACCOUNT_PER_IP = os.environ.get("WOLVWEALTH_RATELIMIT_ACCOUNT_PER_IP", "30 per minute")
RATELIMIT_OPTIMIZE_PER_CALLER = os.environ.get("WOLVWEALTH_RATELIMIT_OPTIMIZE_PER_CALLER", "20 per minute;200 per hour")
RATELIMIT_BACKTEST_START_PER_CALLER = os.environ.get(
    "WOLVWEALTH_RATELIMIT_BACKTEST_START_PER_CALLER", "10 per minute;60 per hour"
)
# The dashboard polls once a second; this leaves room for a few open tabs.
RATELIMIT_BACKTEST_STATUS_PER_CALLER = os.environ.get(
    "WOLVWEALTH_RATELIMIT_BACKTEST_STATUS_PER_CALLER", "180 per minute"
)

# Number of reverse proxies in front of the app whose X-Forwarded-For can be
# trusted. 0 (default) ignores the header: right when gunicorn is exposed
# directly, wrong behind nginx (every client would share 127.0.0.1's limits).
TRUSTED_PROXY_COUNT = int(os.environ.get("WOLVWEALTH_TRUSTED_PROXIES", 0))

# SQLite database location. Build it with `./bin/db create`.
DATABASE_FILENAME = pathlib.Path(os.environ.get("WOLVWEALTH_DATABASE", WOLVWEALTH_ROOT / "var" / "wolvwealth.sqlite3"))
