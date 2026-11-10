"""WolvWealth development configuration."""

import os
import pathlib
import secrets

# Root of this application, useful if it doesn't occupy an entire domain
APPLICATION_ROOT = '/'

# Secret key for signing session cookies. Set WOLVWEALTH_SECRET_KEY (see
# .env.example) to keep logins across restarts; without it a random key is
# generated at startup, so every restart logs everyone out.
SECRET_KEY = os.environ.get("WOLVWEALTH_SECRET_KEY") or secrets.token_bytes(32)
SESSION_COOKIE_NAME = 'login'

# File Upload to var/uploads/
WOLVWEALTH_ROOT = pathlib.Path(__file__).resolve().parent.parent
UPLOAD_FOLDER = WOLVWEALTH_ROOT/'var'/'uploads'
ALLOWED_EXTENSIONS = set(['png', 'jpg', 'jpeg', 'gif'])
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

# Database file is var/wolvwealth.sqlite3
DATABASE_FILENAME = WOLVWEALTH_ROOT/'var'/'wolvwealth.sqlite3'
