"""WolvWealth API Init."""

import wolvwealth
from wolvwealth.api.state import ApplicationState  # noqa: F401
import wolvwealth.api.optimize  # noqa: F401
import wolvwealth.api.main  # noqa: F401
import wolvwealth.api.api_exceptions  # noqa: F401

import csv
import yfinance as yf

state = ApplicationState()  # Preload global resources on startup
