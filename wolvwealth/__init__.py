"""Initializer for wolvwealth module."""
from flask import Flask  # type: ignore

app = Flask(__name__)  # pylint: disable=invalid-name

import wolvwealth.api  # noqa: E402  pylint: disable=wrong-import-position
import wolvwealth.views  # noqa: E402  pylint: disable=wrong-import-position
