"""Routes for the account page."""

import flask
from flask import render_template

import wolvwealth
from wolvwealth.timeutil import utc_to_eastern_display
from wolvwealth.views.accounts import is_logged_in


@wolvwealth.app.route("/account/", methods=["GET"])
def show_account():
    """Display /account route."""
    if not is_logged_in():
        return flask.redirect(flask.url_for("show_login"))

    connection = wolvwealth.model.get_db()
    cur = connection.execute(
        "SELECT username, email, created FROM users WHERE username = ?",
        (flask.session["username"],),
    )
    account = cur.fetchone()

    cur = connection.execute("SELECT * FROM tokens WHERE owner = ?", (flask.session["username"],))
    tokens = cur.fetchone()
    if account is None or tokens is None:
        flask.session.clear()
        return flask.redirect(flask.url_for("show_login"))

    context = {
        "username": account["username"],
        "email": account["email"],
        "created": utc_to_eastern_display(account["created"]),
        "expiration_date": utc_to_eastern_display(tokens["expires"]),
        "uses": tokens["uses"],
        "api_key": tokens["token"],
        "user": {
            "is_authenticated": True,
        },
    }

    return render_template("account.html", **context)
