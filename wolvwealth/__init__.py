"""Initializer for wolvwealth module."""
from flask import Flask

app = Flask(__name__)

app.config.from_object("wolvwealth.config")

import wolvwealth.model

wolvwealth.model.ensure_db()  # Build var/wolvwealth.sqlite3 on first run

import wolvwealth.api
import wolvwealth.views

state = wolvwealth.api.ApplicationState()  # Preload global resources on startup
