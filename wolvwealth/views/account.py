"""Routes for the account page."""

import arrow
import flask
from flask import render_template
import wolvwealth
from wolvwealth.views.accounts import is_logged_in


@wolvwealth.app.route("/account/", methods=["GET"])
def show_account():
    """Display /account route."""
    if not is_logged_in():
        flask.redirect(flask.redirect("show_login"))

    connection = wolvwealth.model.get_db()
    cur = connection.execute(
        "SELECT username, email, created FROM users WHERE username = ?",
        (flask.session["username"],),
    )
    account = cur.fetchone()

    cur = connection.execute(
        'SELECT uses, expires FROM tokens WHERE owner = ?', (
            flask.session["username"],)
    )
    tokens = cur.fetchone()

    context = {
        "username": account["username"],
        "email": account["email"],
        "created": arrow.get(account["created"]).humanize(),
        "expiration_date": arrow.get(tokens["expires"]).humanize(),
        "uses": tokens["uses"],
    }

    return render_template("account.html", **context)
