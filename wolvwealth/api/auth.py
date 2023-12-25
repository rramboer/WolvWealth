"""Authentication functions for the API."""
import flask
import wolvwealth
import secrets
import bcrypt  # type: ignore
from wolvwealth.api.api_exceptions import InvalidUsage
import wolvwealth.model


@wolvwealth.app.route("/api/status", methods=["POST"])
def check_credentials():
    """Return credentials information."""
    api_key = flask.request.headers.get("Authorization")
    input_json = {}
    username = ""
    try:
        input_json = flask.request.json
    except Exception:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
    if "username" not in input_json:
        raise InvalidUsage("Parse Error. Missing username.")
    if not isinstance(input_json["username"], str):
        raise InvalidUsage("Parse Error. Username must be a string.")
    username = input_json["username"]

    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT * FROM users WHERE username = ?", (username,))
    result_users = cur.fetchone()

    # Check if username exists
    if result_users is None:
        raise InvalidUsage("Authorization Error. Invalid username.", status_code=401)
    cur = connection.execute("SELECT * FROM tokens WHERE owner = ?", (username,))
    result_tokens = cur.fetchone()

    # Check if API key exists
    if api_key is None:
        raise InvalidUsage("Authorization Error. Missing API key.", status_code=403)

    # Check if API key belongs to username
    if result_tokens["token"] != api_key:
        raise InvalidUsage("Authorization Error. Invalid API key.", status_code=403)

    # Fetch expiration time and number of uses
    expiration_time = result_tokens["expires"]
    uses = result_tokens["uses"]

    output_json = {
        "username": username,
        "account_creation_date": result_users["created"],
        "api_key_expiration": expiration_time,
        "api_key_uses": uses,
    }

    return flask.jsonify(output_json)


def generate_api_key(owner: str, tier: str) -> str:
    """Generate a new API key and add it to the database."""
    api_key = secrets.token_urlsafe(32)
    connection = wolvwealth.model.get_db()
    expiration_time = ""
    num_uses = 0
    if tier == "free":
        num_uses = 25
        expiration_time = "+7 days"
    elif tier == "plus":
        num_uses = 100
        expiration_time = "+90 days"
    elif tier == "premium":
        num_uses = 500
        expiration_time = "+365 days"
    elif tier == "beta":
        num_uses = 1000000000
        expiration_time = "+7 days"
    elif tier == "developer" or tier == "lifetime":
        num_uses = 1000000000
        expiration_time = "+100 years"
    else:
        return None
    connection.execute(
        "INSERT INTO tokens (owner, token, expires, uses) VALUES (?, ?, (datetime('now', ?)), ?)",
        (owner, api_key, expiration_time, num_uses),
    )
    return api_key


def check_api_key() -> bool:
    """Check if API key is valid. Returns True if valid, False otherwise."""
    api_key = flask.request.headers.get("Authorization")
    if api_key is None:
        raise InvalidUsage("No API key provided.", status_code=401)
    connection = wolvwealth.model.get_db()

    # Check if API key exists
    cur = connection.execute("SELECT * FROM tokens WHERE token = ?", (api_key,))
    result = cur.fetchone()
    if result is None:
        raise InvalidUsage("Authorization Error. Invalid API key.", status_code=403)

    # Check if API key has expired
    expiration_time = result["expires"]
    cur = connection.execute("SELECT datetime('now') AS now")
    current_time = cur.fetchone()["now"]
    if current_time > expiration_time:
        connection.execute("DELETE FROM tokens WHERE token = ?", (api_key,))
        raise InvalidUsage("Authorization Error. API key has expired.", status_code=403)

    # Check if API key has been used too many times
    uses = result["uses"]
    if uses == 0:
        connection.execute("DELETE FROM tokens WHERE token = ?", (api_key,))
        raise InvalidUsage("Authorization Error. API key has run out of uses.", status_code=403)

    # Decrement uses by 1
    connection.execute(
        "UPDATE tokens SET uses = ? WHERE token = ?",
        (
            uses - 1,
            api_key,
        ),
    )
    return True


def hash_password(_password) -> str:
    """Hash a users password."""
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(_password.encode("utf-8"), salt)
    return hashed_password


def check_user_password(username, password) -> bool:
    """Check if password matches password in database."""
    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT password " "FROM users " "WHERE username = ?", (username,))
    result = cur.fetchone()
    if result is None:
        return False
    hashed_password = result["password"]
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password)


@wolvwealth.app.route("/api/beta-trial", methods=["POST"])
def beta_trial():
    """Register a new user."""
    input_json = {}
    try:
        input_json = flask.request.json
    except Exception:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
    if "username" not in input_json:
        raise InvalidUsage("Parse Error. Missing username.")
    if not isinstance(input_json["username"], str):
        raise InvalidUsage("Parse Error. Username must be a string.")
    if "password" not in input_json:
        raise InvalidUsage("Parse Error. Missing password.")
    if not isinstance(input_json["password"], str):
        raise InvalidUsage("Parse Error. Password must be a string.")
    username = input_json["username"]
    password = input_json["password"]
    if len(username) < 3:
        raise InvalidUsage("Parse Error. Username must be at least 3 characters long.")
    if len(password) < 3:
        raise InvalidUsage("Parse Error. Password must be at least 3 characters long.")
    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT * FROM users WHERE username = ?", (username,))
    result = cur.fetchone()
    if result is not None:
        raise InvalidUsage("Authorization Error. Username already exists.", status_code=401)
    hashed_password = hash_password(password)
    connection.execute(
        "INSERT INTO users (username, password, created) VALUES (?, ?, datetime('now'))",
        (username, hashed_password),
    )
    apikey = generate_api_key(username, "beta")
    return flask.jsonify({"username": username, "api_key": apikey})


@wolvwealth.app.route("/api/delete-account", methods=["POST"])
def delete_account():
    """Delete a user's account."""
    input_json = {}
    try:
        input_json = flask.request.json
    except Exception:
        raise InvalidUsage("Parse Error. Unable to parse request as JSON.")
    if "username" not in input_json:
        raise InvalidUsage("Parse Error. Missing username.")
    if not isinstance(input_json["username"], str):
        raise InvalidUsage("Parse Error. Username must be a string.")
    if "password" not in input_json:
        raise InvalidUsage("Parse Error. Missing password.")
    if not isinstance(input_json["password"], str):
        raise InvalidUsage("Parse Error. Password must be a string.")
    username = input_json["username"]
    password = input_json["password"]
    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT * FROM users WHERE username = ?", (username,))
    result = cur.fetchone()
    if result is None:
        raise InvalidUsage("Authorization Error. Username does not exist.", status_code=401)
    if not check_user_password(username, password):
        raise InvalidUsage("Authorization Error. Incorrect password.", status_code=401)
    connection.execute("DELETE FROM users WHERE username = ?", (username,))
    connection.execute("DELETE FROM tokens WHERE owner = ?", (username,))
    return flask.jsonify({"success": f"Deleted user: {username}."})
