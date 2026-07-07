"""Accounts pages and helper functions."""

import hmac
import re
import secrets

import flask

import wolvwealth
from wolvwealth.api.auth import (
    Tier,
    check_email_exists,
    check_user_exists,
    check_user_password,
    generate_api_key,
    hash_password,
)

USERNAME_MAX_LENGTH = 20  # matches VARCHAR(20) in sql/schema.sql
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_csrf_token() -> str:
    """Return the session's CSRF token, creating one if needed."""
    if "csrf_token" not in flask.session:
        flask.session["csrf_token"] = secrets.token_hex(16)
    return flask.session["csrf_token"]


@wolvwealth.app.context_processor
def inject_csrf_token():
    """Expose csrf_token() to all templates."""
    return {"csrf_token": get_csrf_token}


def validate_csrf() -> None:
    """Abort with 400 unless the form carries the session's CSRF token."""
    expected = flask.session.get("csrf_token", "")
    submitted = flask.request.form.get("csrf_token", "")
    # Compare bytes: compare_digest() raises TypeError on non-ASCII str, which
    # would turn a bad token into a 500 instead of the intended 400.
    if not expected or not hmac.compare_digest(expected.encode(), submitted.encode()):
        flask.abort(400, description="Invalid or missing CSRF token.")


def is_logged_in() -> bool:
    """Check that the session names a user that still exists."""
    username = flask.session.get("username")
    if username is None:
        return False
    if not check_user_exists(username):
        flask.session.clear()  # stale session for a deleted account
        return False
    return True


@wolvwealth.app.route("/accounts/register/", methods=["POST"])
def accounts_create():
    """Create a new user account."""
    validate_csrf()
    username = (flask.request.form.get("username") or "").strip()
    email = (flask.request.form.get("email") or "").strip()
    password = flask.request.form.get("password") or ""
    if not username or not email or not password:
        flask.flash("All fields are required.", "error")
        return flask.redirect(flask.url_for("show_register"))
    if len(username) > USERNAME_MAX_LENGTH:
        flask.flash(f"Username must be {USERNAME_MAX_LENGTH} characters or fewer.", "error")
        return flask.redirect(flask.url_for("show_register"))
    if not EMAIL_PATTERN.fullmatch(email):
        flask.flash("Please enter a valid email address.", "error")
        return flask.redirect(flask.url_for("show_register"))
    if check_user_exists(username):
        flask.flash("That username is already taken.", "error")
        return flask.redirect(flask.url_for("show_register"))
    if check_email_exists(email):
        flask.flash("An account with that email already exists.", "error")
        return flask.redirect(flask.url_for("show_register"))
    connection = wolvwealth.model.get_db()
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
    validate_csrf()
    if is_logged_in():
        return flask.redirect(flask.url_for("show_landing"))
    user = (flask.request.form.get("username") or "").strip()
    pwd = flask.request.form.get("password") or ""
    if not user or not pwd:
        flask.flash("Username and password are required.", "error")
        return flask.redirect(flask.url_for("show_login"))
    if not check_user_exists(user) or not check_user_password(user, pwd):
        flask.flash("Incorrect username or password.", "error")
        return flask.redirect(flask.url_for("show_login"))
    flask.session["username"] = user
    return flask.redirect(flask.url_for("show_landing"))


@wolvwealth.app.route("/accounts/logout/", methods=["POST"])
def logout():
    """Logout a user."""
    validate_csrf()
    if not is_logged_in():
        return flask.redirect(flask.url_for("show_landing"))
    flask.session.pop("username")
    return flask.redirect(flask.url_for("show_landing"))


@wolvwealth.app.route("/accounts/delete/", methods=["POST"])
def accounts_delete():
    """Delete a user account."""
    validate_csrf()
    if not is_logged_in():
        return flask.redirect(flask.url_for("show_landing"))
    username = flask.session["username"]
    connection = wolvwealth.model.get_db()
    connection.execute("DELETE FROM users WHERE username = ?", (username,))
    flask.session.clear()
    return flask.redirect(flask.url_for("show_landing"))
