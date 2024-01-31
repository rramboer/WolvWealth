"""Accounts pages and helper functions."""

import flask
import wolvwealth
from wolvwealth.api.auth import (
    check_user_exists,
    check_email_exists,
    check_user_password,
    generate_api_key,
    hash_password,
    Tier,
)


@wolvwealth.app.route("/accounts/register/", methods=["POST"])
def accounts_create():
    """Create a new user account."""
    connection = wolvwealth.model.get_db()
    username = flask.request.form.get("username")
    email = flask.request.form.get("email")
    password = flask.request.form.get("password")
    if username is None or password is None or email is None:
        return flask.redirect(flask.url_for("show_register"))  # TODO: required field is missing
    if check_user_exists(username):
        return flask.redirect(flask.url_for("show_register"))  # TODO: username already exists
    if check_email_exists(email):
        return flask.redirect(flask.url_for("show_register"))  # TODO: email already exists
    connection.execute(
        "INSERT INTO users (username, email, password, created) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
        (username, email, hash_password(password)),
    )
    generate_api_key(username, Tier.Free)
    flask.session["username"] = username
    return flask.redirect(flask.url_for("show_landing"))


@wolvwealth.app.route("/accounts/login/", methods=["POST"])
def login():
    """Login a user."""
    if is_logged_in() is True:
        return flask.redirect(flask.url_for("show_landing"))
    user = flask.request.form.get("username")
    pwd = flask.request.form.get("password")
    if user is None or pwd is None:
        return flask.redirect(flask.url_for("show_login"))  # TODO: required field is missing
    if not check_user_exists(user) or not check_user_password(user, pwd):
        return flask.redirect(flask.url_for("show_login"))  # TODO: username or password is incorrect
    flask.session["username"] = user
    return flask.redirect(flask.url_for("show_landing"))


@wolvwealth.app.route("/accounts/logout/", methods=["GET", "POST"])
def logout():
    """Logout a user."""
    if is_logged_in() is False:
        return flask.redirect(flask.url_for("show_landing"))
    flask.session.pop("username")
    return flask.redirect(flask.url_for("show_landing"))


@wolvwealth.app.route("/accounts/delete/", methods=["GET", "POST"])
def accounts_delete():
    """Delete a user account."""
    if is_logged_in() is False:
        return flask.redirect(flask.url_for("show_landing"))
    username = flask.session["username"]
    connection = wolvwealth.model.get_db()
    connection.execute("DELETE FROM users WHERE username = ?", (username,))
    return flask.redirect(flask.url_for("show_landing"))


def is_logged_in() -> bool:
    """Check if session contains username."""
    return "username" in flask.session
