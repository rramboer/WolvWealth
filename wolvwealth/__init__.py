"""Initializer for wolvwealth module."""
from flask import Flask

app = Flask(__name__)

app.config.from_object("wolvwealth.config")

# app.config.from_envvar("WOLVWEALTH_SETTINGS", silent=True)

import wolvwealth.api
import wolvwealth.model
import wolvwealth.views
import wolvwealth.templates

state = wolvwealth.api.ApplicationState()  # Preload global resources on startup
