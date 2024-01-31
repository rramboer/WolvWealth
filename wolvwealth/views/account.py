"""Routes for the account page."""

import datetime
import flask
from flask import render_template
import wolvwealth
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

    created_et = (
        datetime.datetime.strptime(account["created"], "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=5)
    ).strftime("%Y-%m-%d %I:%M %p") + " ET"

    expiration_et = (
        datetime.datetime.strptime(tokens["expires"], "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=5)
    ).strftime("%Y-%m-%d %I:%M %p") + " ET"

    context = {
        "username": account["username"],
        "email": account["email"],
        "created": created_et,
        "expiration_date": expiration_et,
        "uses": tokens["uses"],
        "api_key": tokens["token"],
        "user": {
            "is_authenticated": is_logged_in(),
        },
    }

    return render_template("account.html", **context)
