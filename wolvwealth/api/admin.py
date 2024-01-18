import flask
import wolvwealth
import datetime
from wolvwealth.api.api_exceptions import InvalidUsage


@wolvwealth.app.route("/api/admin/add/", methods=["POST"])
def add_admin(username: str):
    """Add user to admin list."""
    check_admin_priv(flask.request.headers.get("Authorization"))
    input_json = {}
    try:
        input_json = flask.request.json
    except Exception:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
    if "username" not in input_json:
        raise InvalidUsage("Parse Error. Username required.")
    if not isinstance(input_json["username"], str):
        raise InvalidUsage("Parse Error. Username must be a string.")
    username = input_json["username"]
    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT * FROM users WHERE username = ?", (username,))
    result = cur.fetchone()
    if result is None:
        raise InvalidUsage("Username does not exist. User must be registered.")
    cur = connection.execute("SELECT * FROM admins WHERE username = ?", (username,))
    result = cur.fetchone()
    if result is not None:
        raise InvalidUsage("User is already an admin.")
    connection.execute("INSERT INTO admins (username) VALUES (?)", (username,))
    return flask.jsonify({"success": f"User {username} added to admin list."})


@wolvwealth.app.route("/api/admin/remove/", methods=["POST"])
def remove_admin(username: str):
    """Remove user from admin list."""
    check_admin_priv(flask.request.headers.get("Authorization"))
    input_json = {}
    try:
        input_json = flask.request.json
    except Exception:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
    if "username" not in input_json:
        raise InvalidUsage("Parse Error. Username required.")
    if not isinstance(input_json["username"], str):
        raise InvalidUsage("Parse Error. Username must be a string.")
    username = input_json["username"]
    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT * FROM admins WHERE username = ?", (username,))
    result = cur.fetchone()
    if result is None:
        raise InvalidUsage("User is not an admin.")
    connection.execute("DELETE FROM admins WHERE username = ?", (username,))
    return flask.jsonify({"success": f"User {username} removed from admin list."})


@wolvwealth.app.route("/api/db/dump/", methods=["GET", "POST"])
def db_dump():
    """Dump database in route. Requires admin privileges."""
    api_key = flask.request.headers.get("Authorization")
    wolvwealth.api.admin.check_admin_priv(api_key)
    conn = wolvwealth.model.get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users")
    users = cur.fetchall()
    cur.execute("SELECT * FROM tokens")
    tokens = cur.fetchall()
    cur.execute("SELECT * FROM admins")
    admins = cur.fetchall()
    output_json = {"users": users, "tokens": tokens, "admins": []}
    for user in admins:
        output_json["admins"].append(user["username"])
    return flask.jsonify(output_json)


@wolvwealth.app.route("/api/db/status/", methods=["POST"])
def db_test():
    """Test database in route. Requires admin privileges."""
    api_key = flask.request.headers.get("Authorization")
    wolvwealth.api.admin.check_admin_priv(api_key)
    conn = wolvwealth.model.get_db()
    status = None
    try:
        conn.cursor()
        status = True
    except Exception as ex:
        status = False
    return flask.jsonify({"status": ("available" if status else "unavailable")})


@wolvwealth.app.route("/api/admin/user-info", methods=["POST"])
def user_info():
    """Get user info."""
    api_key = flask.request.headers.get("Authorization")
    wolvwealth.api.admin.check_admin_priv(api_key)
    input_json = {}
    try:
        input_json = flask.request.json
    except Exception:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
    if "username" not in input_json:
        raise InvalidUsage("Parse Error. Username required.")
    if not isinstance(input_json["username"], str):
        raise InvalidUsage("Parse Error. Username must be a string.")
    username = input_json["username"]
    connection = wolvwealth.model.get_db()
    cur = connection.execute(
        "SELECT username, email, created FROM users WHERE username = ?",
        (username,),
    )
    account = cur.fetchone()
    cur = connection.execute("SELECT * FROM tokens WHERE owner = ?", (username,))
    tokens = cur.fetchone()
    created_et = (
        datetime.datetime.strptime(account["created"], "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=5)
    ).strftime("%Y-%m-%d %I:%M %p") + " ET"
    expiration_et = (
        datetime.datetime.strptime(tokens["expires"], "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=5)
    ).strftime("%Y-%m-%d %I:%M %p") + " ET"
    result = {
        "username": account["username"],
        "email": account["email"],
        "account_created": created_et,
        "access_expires": expiration_et,
        "optimizations_remaining": tokens["uses"],
        "api_key": tokens["token"],
        "admin": connection.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone() is not None,
    }
    return flask.jsonify(result)


@wolvwealth.app.route("/api/admin/update-user/", methods=["POST"])
def update_user():
    """Update a user's information."""
    api_key = flask.request.headers.get("Authorization")
    check_admin_priv(api_key)
    input_json = {}
    try:
        input_json = flask.request.json
    except Exception:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
    if "username" not in input_json:
        raise InvalidUsage("Parse Error. Username required.")
    if not isinstance(input_json["username"], str):
        raise InvalidUsage("Parse Error. Username must be a string.")
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
            expires_utc = (
                datetime.datetime.strptime(input_json["expires"], "%Y-%m-%d %H:%M:%S") + datetime.timedelta(hours=5)
            ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            raise InvalidUsage("Parse Error. Expiration must be in the format YYYY-MM-DD HH:MM:SS.")
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
    api_key = flask.request.headers.get("Authorization")
    check_admin_priv(api_key)
    input_json = {}
    try:
        input_json = flask.request.json
    except Exception:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
    if "username" not in input_json:
        raise InvalidUsage("Parse Error. Username required.")
    if not isinstance(input_json["username"], str):
        raise InvalidUsage("Parse Error. Username must be a string.")
    username = input_json["username"]
    if not wolvwealth.api.auth.check_user_exists(username):
        raise InvalidUsage("Username does not exist.")
    connection = wolvwealth.model.get_db()
    connection.execute("DELETE FROM users WHERE username = ?", (username,))
    return flask.jsonify({"success": f"User {username} deleted."})


def check_admin_priv(api_key: str) -> bool:
    """Return true if user is an admin."""
    if api_key is None:
        return False
    connection = wolvwealth.model.get_db()
    username_result = connection.execute("SELECT * FROM tokens WHERE token = ?", (api_key,)).fetchone()
    if username_result is None:
        return False
    username = username_result["owner"]
    is_admin = connection.execute("SELECT username FROM admins WHERE username = ?", (username,)).fetchone()
    if is_admin is None:
        return False
    return True
