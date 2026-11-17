"""Initializer for wolvwealth module."""

from flask import Flask
from flask.json.provider import DefaultJSONProvider


class StrictJSONProvider(DefaultJSONProvider):
    """Reject the non-standard NaN / Infinity / -Infinity literals in request bodies.

    Python's json module accepts them by default; they then pass every
    numeric range check (NaN compares false to everything), so they must be
    refused before any handler sees them.
    """

    def loads(self, s, **kwargs):
        """Parse JSON, raising ValueError on non-finite literals."""
        kwargs.setdefault("parse_constant", _reject_non_finite)
        return super().loads(s, **kwargs)


def _reject_non_finite(token: str):
    raise ValueError(f"Non-finite number {token} is not valid JSON.")


app = Flask(__name__)
app.json = StrictJSONProvider(app)

app.config.from_object("wolvwealth.config")

# Optional overrides from a settings file, e.g. WOLVWEALTH_SETTINGS=/etc/wolvwealth.cfg
app.config.from_envvar("WOLVWEALTH_SETTINGS", silent=True)

if app.config["SECRET_KEY"] == app.config["DEV_SECRET_KEY"]:
    app.logger.warning(
        "Using the insecure development SECRET_KEY. Set WOLVWEALTH_SECRET_KEY before deploying to production."
    )

import wolvwealth.ratelimit  # noqa: E402  (must be initialized before routes register their limits)

wolvwealth.ratelimit.init_app(app)

import wolvwealth.api  # noqa: E402  (route registration requires the app object above)
import wolvwealth.model  # noqa: E402,F401
import wolvwealth.views  # noqa: E402,F401

state = wolvwealth.api.ApplicationState()  # Preload global resources on startup
if not state.HISTORICAL_PRICES.empty:
    app.logger.info("Historical price data loaded through %s", state.HISTORICAL_PRICES.index[-1].date())
