"""Accounts pages and helper functions."""

import flask
import wolvwealth
from wolvwealth.api.auth import check_user_exists, check_user_password, generate_api_key


@wolvwealth.app.route("/accounts/register/", methods=["POST"])
def accounts_create():
    """Create a new user account."""
    connection = wolvwealth.model.get_db()
    username = flask.request.form.get("username")
    email = flask.request.form.get("email")
    password = flask.request.form.get("password")
    if username is None or password is None or email is None:
        flask.abort(400)  # TODO: Prompt user saying that required field is missing
    if check_user_exists(username):
        flask.abort(400)  # TODO: Prompt user that username already exists
    connection.execute("INSERT INTO users (username, email, password) VALUES (?, ?, ?)", (username, email, password))
    flask.session["username"] = username
    return flask.redirect(flask.request.args["target"])


@wolvwealth.app.route("/accounts/login/", methods=["POST"])
def login():
    """Login a user."""
    target = flask.request.args["target"]
    if is_logged_in() is True:
        return flask.redirect(target)
    user = flask.request.form.get("username")
    pwd = flask.request.form.get("password")
    if user is None or pwd is None:
        flask.abort(400)  # TODO: Prompt user to re-enter username and password
    if check_user_exists(user) is False:
        flask.abort(403)  # Prompt user saying that username does not exist
    if check_user_password(user, pwd) is False:
        flask.abort(403)  # Prompt user saying that username or password is incorrect
    flask.session["username"] = user
    return flask.redirect(target)


@wolvwealth.app.route("/accounts/logout/", methods=["GET", "POST"])
def logout():
    """Logout a user."""
    flask.session.pop("username")
    return flask.redirect(flask.url_for("show_login"))


@wolvwealth.app.route("/accounts/delete/", methods=["GET", "POST"])
def accounts_delete():
    """Delete a user account."""
    username = flask.session["username"]
    connection = wolvwealth.model.get_db()
    connection.execute("DELETE FROM users WHERE username = ?", (username,))
    connection.execute("DELETE FROM tokens WHERE owner = ?", (username,))
    return flask.redirect(flask.url_for("show_landing"))


# HELPER FUNCTIONS BELOW:
def is_logged_in() -> bool:
    """Check if session contains username."""
    if "username" not in flask.session:
        return False
    return True
