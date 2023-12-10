import uuid
import hashlib
import flask
import wolvwealth

def hashpass():
    """Hash a users password."""
    password = flask.request.form.get('password')
    algorithm = 'sha512'
    salt = uuid.uuid4().hex
    hash_obj = hashlib.new(algorithm)
    password_salted = salt + password
    hash_obj.update(password_salted.encode('utf-8'))
    password_hash = hash_obj.hexdigest()
    return "$".join([algorithm, salt, password_hash])

def check_user_password(username, password):
    """Check if password matches password in databse."""
    connection = wolvwealth.model.get_db()
    cur = connection.execute(
        "SELECT password "
        "FROM users "
        "WHERE username = ?",
        (username,)
    )
    # converts password into salt + hash form
    correct_hashed_password = cur.fetchall()[0]['password']
    hashed_password_components = correct_hashed_password.split('$')
    algorithm = hashed_password_components[0]
    hash_obj = hashlib.new(algorithm)
    salt = hashed_password_components[1]
    password_salted = salt + password
    hash_obj.update(password_salted.encode('utf-8'))
    password_hash = hash_obj.hexdigest()
    password_db_string = "$".join([algorithm, salt, password_hash])
    # checks if salt+hash of input pw == salt+hash of stored pw
    if password_db_string != correct_hashed_password:
        return False
    return True