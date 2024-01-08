"""Initializer for wolvwealth module."""
from flask import Flask

app = Flask(__name__)  # pylint: disable=invalid-name

app.config.from_object("wolvwealth.config")

# app.config.from_envvar("WOLVWEALTH_SETTINGS", silent=True)

import wolvwealth.api
import wolvwealth.model
import wolvwealth.views
import wolvwealth.templates
