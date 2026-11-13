"""Route and code for optimizer page.

GET /optimizer/"""

import flask
import wolvwealth
from wolvwealth.views.accounts import is_logged_in


@wolvwealth.app.route("/optimizer/", methods=["GET"])
def show_optimizer():
    """Display /optimizer route. The page calls /api/optimize/ with the user's API key."""
    if is_logged_in() is False:
        return flask.redirect(flask.url_for("show_login"))
    connection = wolvwealth.model.get_db()
    token = connection.execute(
        "SELECT token, uses FROM tokens WHERE owner = ?", (flask.session["username"],)
    ).fetchone()
    context = {
        "user": {"is_authenticated": True},
        "api_key": token["token"] if token else "",
        "uses": token["uses"] if token else 0,
        "price_date": wolvwealth.state.price_date(),
    }
    return flask.render_template("optimizer.html", **context)
