"""Authentication functions for the API."""
import flask
import wolvwealth
import secrets
import bcrypt
import datetime
from wolvwealth.api.api_exceptions import InvalidUsage


class Tier:
    """Tier class."""

    class Free:
        """Free trial."""

        price = 0
        uses = 15
        expiration = "+7 days"

    class Plus:
        """Plus tier."""

        price = 5
        uses = 300
        expiration = "+90 days"

    class Premium:
        """Premium tier."""

        price = 15
        uses = 1500
        expiration = "+1 year"

    class Lifetime:
        """Lifetime tier."""

        price = 100
        uses = 1000000000
        expiration = "+100 years"

    class Developer:
        """Developer tier."""

        price = 0
        uses = 1000000000
        expiration = "+100 years"


@wolvwealth.app.route("/api/account", methods=["POST"])
def api_account_info():
    """Return credentials information."""
    api_key = flask.request.headers.get("Authorization")
    input_json = {}
    username = ""
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
    uses = result_tokens["uses"]

    created_et = (
        datetime.datetime.strptime(result_users["created"], "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=5)
    ).strftime("%Y-%m-%d %I:%M %p") + " ET"

    expiration_et = (
        datetime.datetime.strptime(result_tokens["expires"], "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=5)
    ).strftime("%Y-%m-%d %I:%M %p") + " ET"

    output_json = {
        "username": result_users["username"],
        "email": result_users["email"],
        "account_created": created_et,
        "access_expires": expiration_et,
        "optimizations_remaining": uses,
        "api_key": api_key,
    }

    return flask.jsonify(output_json)


def generate_api_key(owner: str, tier: Tier) -> str:
    """Generate a new API key and add it to the database."""
    api_key = secrets.token_urlsafe(16)
    connection = wolvwealth.model.get_db()
    connection.execute(
        "INSERT INTO tokens (owner, token, expires, uses) VALUES (?, ?, (datetime('now', ?)), ?)",
        (owner, api_key, tier.expiration, tier.uses),
    )
    return api_key


def check_api_key() -> bool:
    """Check if API key is valid. Decrements API key and returns true if valid. Otherwise errors."""
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
        raise InvalidUsage("Authorization Error. API key has expired.", status_code=403)

    # Check if API key has been used too many times
    uses = result["uses"]
    if uses == 0:
        raise InvalidUsage("Authorization Error. API key has run out of uses. ", status_code=403)

    # Decrement uses by 1
    connection.execute(
        "UPDATE tokens SET uses = ? WHERE token = ?",
        (
            uses - 1,
            api_key,
        ),
    )
    return True


def hash_password(_password: str) -> str:
    """Hash a users password."""
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(_password.encode("utf-8"), salt)
    return hashed_password.decode("utf-8")


def check_user_password(username: str, password: str) -> bool:
    """Check if plaintext password matches password in database."""
    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT password FROM users WHERE username = ?", (username,))
    result = cur.fetchone()
    if result is None:
        return False
    hashed_password = result["password"].encode("utf-8")
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password)


def check_user_exists(username: str) -> bool:
    """Return true if username exists."""
    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT * FROM users WHERE username = ?", (username,))
    result = cur.fetchone()
    if result is None:
        return False
    return True


def check_email_exists(email: str) -> bool:
    """Return true if email exists."""
    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT * FROM users WHERE email = ?", (email,))
    result = cur.fetchone()
    if result is None:
        return False
    return True
