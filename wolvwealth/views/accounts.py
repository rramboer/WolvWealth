"""Accounts pages and helper functions."""

import flask
import wolvwealth

@wolvwealth.app.route("/accounts/register/", methods=["POST"])
def accounts_create():
    """Create a new user account."""
    connection = wolvwealth.model.get_db()
    username = flask.request.form.get('username')
    email = flask.request.form.get('email')
    password = flask.request.form.get('password')
    if (username is None or password is None or email is None):
        flask.abort(400)
    if check_user_exists(username) is True:
        flask.abort(400)
    connection.execute(
        "INSERT INTO users "
        "(username, email, password) "
        "VALUES "
        "(?, ?, ?)",
        (username, email, password)
    )
    flask.session['username'] = username
    return flask.redirect(flask.request.args["target"])

@wolvwealth.app.route("/accounts/login/", methods=["POST"])
def login():
    """Login a user."""
    print("Running login().")
    target = flask.request.args["target"]
    if is_logged_in() is True:
        print("Already logged in.")
        return flask.redirect(target)
    user = flask.request.form.get("username")
    pwd = flask.request.form.get("password")
    if user is None or pwd is None:
        flask.abort(400)
    if wolvwealth.views.accounts.check_user_exists(user) is False:
        flask.abort(403)
    if wolvwealth.views.accounts.check_user_pwd(user, pwd) is False:
        flask.abort(403)
    flask.session["username"] = user
    return flask.redirect(target)

@wolvwealth.app.route("/accounts/delete/", methods=["GET", "POST"])
def accounts_delete():
    """Delete a user account."""
    return flask.redirect(flask.url_for("show_landing"))

# HELPER FUNCTIONS BELOW:
def is_logged_in():
    """Check if session contains username."""
    if 'username' not in flask.session:
        return False
    return True

def check_user_exists(username):
    """Check if username exists."""
    connection = wolvwealth.model.get_db()
    cur = connection.execute(
        "SELECT COUNT(*) "
        "FROM users "
        "WHERE username = ?",
        (username,)
    )
    user_exists = cur.fetchall()[0]['COUNT(*)']
    if user_exists == 0:
        return False
    return True

def check_user_pwd(user, pwd):
    """Check if a user and pwd exist in the DB."""
    conn = wolvwealth.model.get_db()
    cur = conn.execute(
        "SELECT COUNT(*) FROM users WHERE username = ? AND password = ?",
        (user, pwd)
    )
    if cur.fetchall()[0]['COUNT(*)'] == 0:
        return False
    return True
