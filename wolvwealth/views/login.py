"""Route and code for login page.

GET /login/
"""

from flask import render_template

import wolvwealth


@wolvwealth.app.route("/login/", methods=["GET"])
def show_login():
    """Display /login route."""
    context = {}
    return render_template("login.html", **context)
