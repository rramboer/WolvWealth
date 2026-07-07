"""Route and code for login page.

GET /login/
"""

from flask import render_template

import wolvwealth


@wolvwealth.app.route("/register/", methods=["GET"])
def show_register():
    """Display /register route."""
    context = {}
    return render_template("register.html", **context)
