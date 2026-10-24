"""Initializer for wolvwealth module."""
from flask import Flask

app = Flask(__name__)

app.config.from_object("wolvwealth.config")

import wolvwealth.model

wolvwealth.model.ensure_db()  # Build var/wolvwealth.sqlite3 on first run

import wolvwealth.api
import wolvwealth.views

state = wolvwealth.api.ApplicationState()  # Preload global resources on startup


def _auto_refresh_prices():
    """Refresh stale prices on startup, unless disabled or offline."""
    import os
    import sys

    if os.environ.get("WOLVWEALTH_NO_AUTO_REFRESH"):
        return
    age = state.prices_age_days()
    if age <= app.config["AUTO_REFRESH_MAX_AGE_DAYS"]:
        return
    print(f"wolvwealth: cached prices are {age} days old, refreshing (set WOLVWEALTH_NO_AUTO_REFRESH=1 to skip)...")
    try:
        state.update_historical_prices()
        print(f"wolvwealth: prices now end {state.HISTORICAL_PRICES.index[-1].date()}")
    except Exception as err:  # Never make startup fail over market data
        print(f"wolvwealth: price refresh failed ({err}); continuing with cached prices", file=sys.stderr)


_auto_refresh_prices()
