"""WolvWealth development configuration."""

import pathlib

# Root of this application, useful if it doesn't occupy an entire domain
APPLICATION_ROOT = '/'

# Secret key for encrypting cookies
SECRET_KEY = b'\xb9oc.\xd0em\x8e\xc3W\xfey\xd5\xf4/\x04\xccZ\x12?\xe4G\xc2\x96'
SESSION_COOKIE_NAME = 'login'

# File Upload to var/uploads/
WOLVWEALTH_ROOT = pathlib.Path(__file__).resolve().parent.parent
UPLOAD_FOLDER = WOLVWEALTH_ROOT/'var'/'uploads'
ALLOWED_EXTENSIONS = set(['png', 'jpg', 'jpeg', 'gif'])
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

# Database file is var/insta485.sqlite3
DATABASE_FILENAME = WOLVWEALTH_ROOT/'var'/'wolvwealth.sqlite3'
