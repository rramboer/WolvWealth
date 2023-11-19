"""WolvWealth development configuration."""

import pathlib

# Root of this application, useful if it doesn't occupy an entire domain
APPLICATION_ROOT = '/'

# Secret key for encrypting cookies
# FIXME: Remove Secret Key from GitHub
SECRET_KEY = b'f\xba\x12\xae\xa7S~\xdaZ\n/\x82\x96\xa7@[K\x1c\xe32z\xb6\x17:'
SESSION_COOKIE_NAME = 'login'

# File Upload to var/uploads/
INSTA485_ROOT = pathlib.Path(__file__).resolve().parent.parent
UPLOAD_FOLDER = INSTA485_ROOT/'var'/'uploads'
ALLOWED_EXTENSIONS = set(['png', 'jpg', 'jpeg', 'gif'])
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

# Database file is var/insta485.sqlite3
DATABASE_FILENAME = INSTA485_ROOT/'var'/'wolvwealth.sqlite3'
