"""Device cookies: remember which accounts have logged in successfully from this browser.

Implements the OWASP "device cookie" pattern for login throttling. A
per-account limit on failed logins stops distributed brute force, but on its
own it lets anyone who knows a username lock the owner out. With a device
cookie, that per-account limit applies only to clients that have never logged
in to the account; the owner's known browsers are throttled per device
instead, so an attacker cannot exhaust their allowance.

The cookie is a signed (not encrypted) record of a random device id and the
lower-cased usernames that have logged in from it. It is signed with the app's
SECRET_KEY, so it cannot be forged, and is scoped to /accounts/ so it is only
sent with login requests.
"""

import secrets

import flask
from itsdangerous import BadSignature, URLSafeTimedSerializer

COOKIE_NAME = "wolvwealth_device"
COOKIE_PATH = "/accounts/"
MAX_AGE_SECONDS = 365 * 24 * 60 * 60
MAX_ACCOUNTS = 5  # Most recently used accounts remembered per browser.
_SALT = "wolvwealth-device-cookie"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(flask.current_app.secret_key, salt=_SALT)


def normalize(username: str) -> str:
    """Usernames compare case-insensitively for throttling purposes."""
    return username.strip().lower()


def read_device() -> dict | None:
    """The request's valid device record, or None if absent, expired, tampered with or malformed."""
    raw = flask.request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        data = _serializer().loads(raw, max_age=MAX_AGE_SECONDS)
    except BadSignature:  # Includes SignatureExpired.
        return None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("id"), str)
        or not isinstance(data.get("accounts"), list)
        or not all(isinstance(account, str) for account in data["accounts"])
    ):
        return None
    return data


def trusted_device_id(username: str) -> str | None:
    """The device id if this browser has logged in to `username` before, else None."""
    device = read_device()
    if device is not None and normalize(username) in device["accounts"]:
        return device["id"]
    return None


def remember_login(response: flask.Response, username: str) -> flask.Response:
    """Mark this browser as a known device for `username` (called after a successful login)."""
    device = read_device() or {"id": secrets.token_urlsafe(16), "accounts": []}
    name = normalize(username)
    device["accounts"] = ([account for account in device["accounts"] if account != name] + [name])[-MAX_ACCOUNTS:]
    response.set_cookie(
        COOKIE_NAME,
        _serializer().dumps(device),
        max_age=MAX_AGE_SECONDS,
        path=COOKIE_PATH,
        httponly=True,
        samesite="Lax",
        secure=bool(flask.current_app.config.get("SESSION_COOKIE_SECURE", False)),
    )
    return response
