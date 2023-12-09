"""Initializer for wolvwealth module."""
from flask import Flask

app = Flask(__name__)  # pylint: disable=invalid-name

app.config.from_object('wolvwealth.config')

# app.config.from_envvar('WOLVWEALTH_SETTINGS', silent=True)

import wolvwealth.api  # noqa: E402  pylint: disable=wrong-import-position
import wolvwealth.model  # noqa: E402  pylint: disable=wrong-import-position
import wolvwealth.views  # noqa: E402  pylint: disable=wrong-import-position
