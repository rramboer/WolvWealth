"""Route and code for landing (index) page.

GET /
"""

from flask import render_template
import wolvwealth
from wolvwealth.views.accounts import is_logged_in


@wolvwealth.app.route("/", methods=["GET"])
def show_landing():
    """Display / route."""
    context = {
        "user": {
            "is_authenticated": is_logged_in(),
        },
    }
    return render_template("index.html", **context)
