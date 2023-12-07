"""Initializer for views module."""
import wolvwealth  # pylint: disable=wrong-import-position
from flask import send_file  # type: ignore

"""Route and code for landing (index) page.

GET /
"""


@wolvwealth.app.route("/", methods=["GET"])
def show_landing():
    """Display / route."""
    return send_file("static/html/index.html", mimetype="text/html")
