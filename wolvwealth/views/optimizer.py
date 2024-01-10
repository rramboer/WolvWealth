"""Route and code for dashboard page.

GET /optimizer/"""

import flask
import wolvwealth
from wolvwealth.views.accounts import is_logged_in


@wolvwealth.app.route("/optimizer/", methods=["GET"])
def show_optimizer():
    """Display /optimizer route."""
    if is_logged_in() is False:
        return flask.redirect(flask.redirect("show_login"))
    context = {}
    return flask.render_template("optimizer.html", **context)
