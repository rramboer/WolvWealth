"""Rate limiting: who is asking, how often they may ask, and what they get when they ask too often.

Layers, all enforced before a view runs (so before authentication, parsing
or any database work beyond what the key needs):

* Site-wide, per client IP, shared across every route except static files.
* API-wide, per client IP, shared across every /api/ route. Bounds anonymous
  floods and token guessing, which rotate keys and so evade per-caller limits.
* Per caller on the expensive endpoints (optimize, backtest start/poll),
  keyed by API key or session user, so one account cannot crowd out others
  behind the same NAT and cannot exceed its share from many IPs.
* Login per IP and per submitted username (credential stuffing and
  distributed brute force); registration per IP.

Limit strings ("10 per minute;50 per hour") come from app config at request
time, so they can be tuned through environment variables (wolvwealth.config)
without code changes. Counters live in RATELIMIT_STORAGE_URI: memory:// by
default, which is per process, like the backtest job store; use a shared
backend such as redis:// when running more than one gunicorn worker.

Client IPs are only as trustworthy as the proxy setup: see TrustedProxies.
"""

import hashlib
import time

import flask
from flask_limiter import Limiter
from werkzeug.middleware.proxy_fix import ProxyFix


def client_ip() -> str:
    """The client address as seen by the app (after TrustedProxies has applied X-Forwarded-For)."""
    return flask.request.remote_addr or "unknown"


def caller_key() -> str:
    """Bucket per API key, else per logged-in user, else per IP.

    The API key is hashed so raw tokens never reach the counter storage.
    Rotating fake keys gets a fresh bucket each time, which is why the
    per-IP API limit also applies to every /api/ route.
    """
    api_key = flask.request.headers.get("Authorization")
    if api_key:
        return "key:" + hashlib.sha256(api_key.encode("utf-8", "replace")).hexdigest()[:32]
    username = flask.session.get("username")
    if username:
        return f"user:{username}"
    return f"ip:{client_ip()}"


def login_username_key() -> str:
    """Bucket per username submitted to the login form (case-insensitive)."""
    username = (flask.request.form.get("username") or "").strip().lower()
    return f"login:{username}" if username else f"ip:{client_ip()}"


def configured(name: str):
    """A limit value read from app config on every request."""
    return lambda: flask.current_app.config[name]


def _is_static() -> bool:
    return flask.request.endpoint == "static"


limiter = Limiter(
    key_func=client_ip,
    application_limits=[configured("RATELIMIT_SITE_PER_IP")],
    application_limits_exempt_when=_is_static,
)

# Shared bucket for every /api/ route, keyed by IP. Apply to each API view.
api_per_ip = limiter.shared_limit(configured("RATELIMIT_API_PER_IP"), scope="api", key_func=client_ip)


class TrustedProxies:
    """WSGI middleware applying X-Forwarded-For from exactly TRUSTED_PROXY_COUNT reverse proxies.

    Gunicorn sets REMOTE_ADDR to the socket peer, so behind nginx every client
    looks like 127.0.0.1 and per-IP limits would lump all users into one
    bucket. With the count set to the number of proxies in front of the app,
    the real client address is taken from the header those proxies append.
    With 0 (the default, for direct exposure) the header is ignored, so a
    client cannot spoof its address to escape its bucket.
    """

    def __init__(self, wsgi_app, config) -> None:
        """Wrap `wsgi_app`; read the trusted count from `config` on every request."""
        self.wsgi_app = wsgi_app
        self.config = config
        self._wrapped: dict[int, ProxyFix] = {}

    def __call__(self, environ, start_response):
        """Rewrite REMOTE_ADDR from trusted proxies, then dispatch."""
        count = int(self.config.get("TRUSTED_PROXY_COUNT", 0))
        if count <= 0:
            return self.wsgi_app(environ, start_response)
        if count not in self._wrapped:
            self._wrapped[count] = ProxyFix(self.wsgi_app, x_for=count)
        return self._wrapped[count](environ, start_response)


def retry_after_seconds() -> int:
    """Seconds until the limit that was just breached resets (at least 1).

    Truncated like Flask-Limiter's own Retry-After header, so the message and
    the header (which Flask-Limiter rewrites after this handler) agree.
    """
    current = limiter.current_limit
    if current is None:
        return 60
    return max(1, int(current.reset_at - time.time()))


def init_app(app: flask.Flask) -> None:
    """Attach the limiter, the trusted-proxy middleware and the 429 handler to `app`."""
    app.wsgi_app = TrustedProxies(app.wsgi_app, app.config)
    limiter.init_app(app)

    @app.errorhandler(429)
    def too_many_requests(error):
        seconds = retry_after_seconds()
        message = f"Too many requests. Try again in {seconds} second{'s' if seconds != 1 else ''}."
        if flask.request.path.startswith("/api/"):
            response = flask.jsonify({"error": {"message": message, "status_code": 429}})
        else:
            response = flask.make_response(
                flask.render_template("429.html", message=message, user={"is_authenticated": False})
            )
        response.status_code = 429
        response.headers["Retry-After"] = str(seconds)
        return response
