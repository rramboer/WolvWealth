import uuid
import hashlib
import flask
import wolvwealth
import secrets
from wolvwealth.api.api_exceptions import InvalidUsage


def generate_api_key():
    """Generate a new API key."""
    api_key = secrets.token_urlsafe(32)
    # Add api key to database
    return api_key


def check_api_key():
    """Check if API key is valid."""
    api_key = flask.request.headers.get("Authorization")
    if api_key is None:
        raise InvalidUsage("No API key provided.", status_code=401)
    # Check if api key is in database, if not, reject
    if False:
        raise InvalidUsage("Invalid API key.", status_code=401)
    # If uses == 0, delete api key from database, reject
    if False:
        raise InvalidUsage("API key has no uses remaining.", status_code=401)
    # Otherwise, decrement uses by 1
    return True


def hashpass():
    """Hash a users password."""
    password = flask.request.form.get("password")
    algorithm = "sha512"
    salt = uuid.uuid4().hex
    hash_obj = hashlib.new(algorithm)
    password_salted = salt + password
    hash_obj.update(password_salted.encode("utf-8"))
    password_hash = hash_obj.hexdigest()
    return "$".join([algorithm, salt, password_hash])


def check_user_password(username, password):
    """Check if password matches password in databse."""
    connection = wolvwealth.model.get_db()
    cur = connection.execute("SELECT password " "FROM users " "WHERE username = ?", (username,))
    # converts password into salt + hash form
    correct_hashed_password = cur.fetchall()[0]["password"]
    hashed_password_components = correct_hashed_password.split("$")
    algorithm = hashed_password_components[0]
    hash_obj = hashlib.new(algorithm)
    salt = hashed_password_components[1]
    password_salted = salt + password
    hash_obj.update(password_salted.encode("utf-8"))
    password_hash = hash_obj.hexdigest()
    password_db_string = "$".join([algorithm, salt, password_hash])
    # checks if salt+hash of input pw == salt+hash of stored pw
    if password_db_string != correct_hashed_password:
        return False
    return True
