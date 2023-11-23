"""Route and code for landing (index) page.

GET /
"""

from flask import render_template
import wolvwealth


@wolvwealth.app.route("/", methods=["GET"])
def show_landing():
    """Display / route."""
    return render_template("index.html")
