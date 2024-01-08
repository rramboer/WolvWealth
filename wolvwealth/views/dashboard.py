"""Route and code for dashboard page.

GET /dashboard/"""

import flask
import wolvwealth
from wolvwealth.views.accounts import is_logged_in

@wolvwealth.app.route('/dashboard/', methods=['GET'])
def show_dashboard():
    """Display /dashboard route."""
    if is_logged_in() is False:
        flask.redirect(flask.redirect("show_login"))
    context = {}
    return flask.render_template("dashboard.html", **context)
