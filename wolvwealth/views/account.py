"""Routes for the profile page."""

import flask
from flask import render_template
import wolvwealth
from wolvwealth.views.accounts import is_logged_in


@wolvwealth.app.route("/account/", methods=["GET"])
def show_account():
    """Display /account route."""
    if not is_logged_in():
        flask.redirect(flask.redirect("show_login"))
    context = {}
    return render_template("account.html", **context)
