"""Initializer for wolvwealth module."""

from flask import Flask

app = Flask(__name__)

app.config.from_object("wolvwealth.config")

# Optional overrides from a settings file, e.g. WOLVWEALTH_SETTINGS=/etc/wolvwealth.cfg
app.config.from_envvar("WOLVWEALTH_SETTINGS", silent=True)

if app.config["SECRET_KEY"] == app.config["DEV_SECRET_KEY"]:
    app.logger.warning(
        "Using the insecure development SECRET_KEY. Set WOLVWEALTH_SECRET_KEY before deploying to production."
    )

import wolvwealth.api  # noqa: E402  (route registration requires the app object above)
import wolvwealth.model  # noqa: E402,F401
import wolvwealth.views  # noqa: E402,F401

state = wolvwealth.api.ApplicationState()  # Preload global resources on startup
if not state.HISTORICAL_PRICES.empty:
    app.logger.info("Historical price data loaded through %s", state.HISTORICAL_PRICES.index[-1].date())
