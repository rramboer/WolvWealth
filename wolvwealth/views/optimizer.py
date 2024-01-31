"""Route and code for optimizer page.

GET /optimizer/"""

import flask
import wolvwealth
from wolvwealth.views.accounts import is_logged_in


@wolvwealth.app.route("/optimizer/", methods=["GET"])
def show_optimizer():
    """Display /optimizer route."""
    if is_logged_in() is False:
        return flask.redirect(flask.url_for("show_login"))
    context = {"user": {"is_authenticated": is_logged_in()}}
    return flask.render_template("optimizer.html", **context)
