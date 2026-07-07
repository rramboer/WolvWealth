"""Admin API endpoints.

Every route in this module requires a valid admin API token in the
Authorization header; requests without one fail closed with a 403.
"""

import flask

import wolvwealth
from wolvwealth.api.api_exceptions import InvalidUsage
from wolvwealth.timeutil import eastern_to_utc_storage, utc_to_eastern_display


def check_admin_priv(api_key: str | None) -> bool:
    """Return true if the API key belongs to an admin."""
    if api_key is None:
        return False
    connection = wolvwealth.model.get_db()
    username_result = connection.execute("SELECT * FROM tokens WHERE token = ?", (api_key,)).fetchone()
    if username_result is None:
        return False
    username = username_result["owner"]
    is_admin = connection.execute("SELECT username FROM admins WHERE username = ?", (username,)).fetchone()
    return is_admin is not None


def require_admin() -> None:
    """Abort with 403 unless the request carries a valid admin API key."""
    api_key = flask.request.headers.get("Authorization")
    if not check_admin_priv(api_key):
        raise InvalidUsage("Authorization Error. Admin privileges required.", status_code=403)


def parse_username_json() -> dict:
    """Parse the request body as JSON and validate the required username field."""
    try:
        input_json = flask.request.json
    except Exception as err:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.") from err
    if input_json is None or "username" not in input_json:
        raise InvalidUsage("Parse Error. Username required.")
    if not isinstance(input_json["username"], str):
        raise InvalidUsage("Parse Error. Username must be a string.")
    return input_json


@wolvwealth.app.route("/api/admin/add/", methods=["POST"])
def add_admin():
    """Add user to admin list."""
    require_admin()
    username = parse_username_json()["username"]
    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT username FROM users WHERE username = ?", (username,))
    if cur.fetchone() is None:
        raise InvalidUsage("Username does not exist. User must be registered.")
    cur = connection.execute("SELECT username FROM admins WHERE username = ?", (username,))
    if cur.fetchone() is not None:
        raise InvalidUsage("User is already an admin.")
    connection.execute("INSERT INTO admins (username) VALUES (?)", (username,))
    return flask.jsonify({"success": f"User {username} added to admin list."})


@wolvwealth.app.route("/api/admin/remove/", methods=["POST"])
def remove_admin():
    """Remove user from admin list."""
    require_admin()
    username = parse_username_json()["username"]
    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT username FROM admins WHERE username = ?", (username,))
    if cur.fetchone() is None:
        raise InvalidUsage("User is not an admin.")
    connection.execute("DELETE FROM admins WHERE username = ?", (username,))
    return flask.jsonify({"success": f"User {username} removed from admin list."})


@wolvwealth.app.route("/api/db/dump/", methods=["POST"])
def db_dump():
    """Summarize database contents. Requires admin privileges.

    Password hashes and API token values are deliberately excluded.
    """
    require_admin()
    conn = wolvwealth.model.get_db()
    cur = conn.cursor()
    cur.execute("SELECT username, email, created FROM users")
    users = cur.fetchall()
    cur.execute("SELECT owner, expires, uses FROM tokens")
    tokens = cur.fetchall()
    cur.execute("SELECT username FROM admins")
    admins = [row["username"] for row in cur.fetchall()]
    return flask.jsonify({"users": users, "tokens": tokens, "admins": admins})


@wolvwealth.app.route("/api/db/status/", methods=["POST"])
def db_test():
    """Test database in route. Requires admin privileges."""
    require_admin()
    conn = wolvwealth.model.get_db()
    try:
        conn.cursor()
        status = True
    except Exception:
        status = False
    return flask.jsonify({"status": ("available" if status else "unavailable")})


@wolvwealth.app.route("/api/admin/user-info/", methods=["POST"])
def user_info():
    """Get user info."""
    require_admin()
    username = parse_username_json()["username"]
    connection = wolvwealth.model.get_db()
    cur = connection.execute(
        "SELECT username, email, created FROM users WHERE username = ?",
        (username,),
    )
    account = cur.fetchone()
    if account is None:
        raise InvalidUsage("Username does not exist.", status_code=404)
    cur = connection.execute("SELECT * FROM tokens WHERE owner = ?", (username,))
    tokens = cur.fetchone()
    if tokens is None:
        raise InvalidUsage("User has no API token.", status_code=404)
    result = {
        "username": account["username"],
        "email": account["email"],
        "account_created": utc_to_eastern_display(account["created"]),
        "access_expires": utc_to_eastern_display(tokens["expires"]),
        "optimizations_remaining": tokens["uses"],
        "api_key": tokens["token"],
        "admin": connection.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone() is not None,
    }
    return flask.jsonify(result)


@wolvwealth.app.route("/api/admin/update-user/", methods=["POST"])
def update_user():
    """Update a user's information."""
    require_admin()
    input_json = parse_username_json()
    username = input_json["username"]
    connection = wolvwealth.model.get_db()
    if "uses" in input_json:
        if not isinstance(input_json["uses"], int):
            raise InvalidUsage("Parse Error. Uses must be an integer.")
        uses = input_json["uses"]
        if uses < 0:
            raise InvalidUsage("Parse Error. Uses must be greater than or equal to 0.")
        connection.execute("UPDATE tokens SET uses = ? WHERE owner = ?", (uses, username))
    if "expires" in input_json:
        if not isinstance(input_json["expires"], str):
            raise InvalidUsage("Parse Error. Expiration must be a string.")
        try:
            expires_utc = eastern_to_utc_storage(input_json["expires"])
        except ValueError as err:
            raise InvalidUsage("Parse Error. Expiration must be in the format YYYY-MM-DD HH:MM:SS.") from err
        connection.execute("UPDATE tokens SET expires = ? WHERE owner = ?", (expires_utc, username))
    if "email" in input_json:
        if not isinstance(input_json["email"], str):
            raise InvalidUsage("Parse Error. Email must be a string.")
        if wolvwealth.api.auth.check_email_exists(input_json["email"]):
            raise InvalidUsage("Email already exists.")
        connection.execute("UPDATE users SET email = ? WHERE username = ?", (input_json["email"], username))
    return flask.jsonify({"success": f"User {username} updated."})


@wolvwealth.app.route("/api/admin/delete-user/", methods=["POST"])
def delete_user():
    """Delete a user."""
    require_admin()
    username = parse_username_json()["username"]
    if not wolvwealth.api.auth.check_user_exists(username):
        raise InvalidUsage("Username does not exist.")
    connection = wolvwealth.model.get_db()
    connection.execute("DELETE FROM users WHERE username = ?", (username,))
    return flask.jsonify({"success": f"User {username} deleted."})
