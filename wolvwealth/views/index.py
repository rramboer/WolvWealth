"""Route and code for landing (index) page.

GET /
"""

from flask import render_template
import wolvwealth
from wolvwealth.views.workspaces import current_workspace


@wolvwealth.app.route("/", methods=["GET"])
def show_landing():
    """Display / route."""
    context = {"workspace": current_workspace()}
    return render_template("index.html", **context)
