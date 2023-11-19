"""
wolvwealth account creation, deletion, and edit view.

URLs include:
/accounts/login/
/accounts/logout/
/accounts/create/
/accounts/delete/
/accounts/edit/
/accounts/password/
/accounts/auth/
"""

import uuid
import hashlib
import os
import flask
import wolvwealth


@wolvwealth.app.route('/accounts/login/')
def show_login():
    """Display /accounts/login/ route."""
    print("IMPLEMENT CHECK TO MAKE SURE SOMEONE IS LOGGED IN")
    # if username is not in session, load the login page
    if 'username' not in flask.session:
        context = {"page_type": "login"}
        context['url'] = flask.url_for("show_index")
        return flask.render_template("account.html", **context)
    # else, load index page
    print(flask.session)
    return flask.redirect(flask.url_for("show_index"))


@wolvwealth.app.route('/accounts/create/')
def show_create():
    """Perform create account."""
    # if username is in session, load the edit page
    if 'username' in flask.session:
        print(flask.session)
        return flask.redirect(flask.url_for("show_edit"))
    # otherwise, load create page
    context = {"page_type": "create"}
    context['url'] = flask.url_for("show_index")
    return flask.render_template("account.html", **context)


@wolvwealth.app.route('/accounts/delete/')
def show_delete():
    """Perform delete account."""
    if wolvwealth.views.helpers.check_login():
        return flask.redirect(flask.url_for("show_login"))
    logname = flask.session['username']
    context = {"page_type": "delete"}
    context["logname"] = logname
    context['url'] = flask.url_for("show_create")
    return flask.render_template("account.html", **context)


@wolvwealth.app.route('/accounts/edit/')
def show_edit():
    """Perform edit account."""
    if wolvwealth.views.helpers.check_login():
        return flask.redirect(flask.url_for("show_login"))
    logname = flask.session['username']
    # Connect to database
    connection = wolvwealth.model.get_db()
    # Query database
    filename = connection.execute(
        "SELECT filename "
        "FROM users "
        "WHERE username = ?",
        (logname,),
    )
    filename = filename.fetchall()
    filename = filename[0]['filename']
    fullname = connection.execute(
        "SELECT fullname "
        "FROM users "
        "WHERE username = ?",
        (logname,),
    )
    fullname = fullname.fetchall()
    fullname = fullname[0]['fullname']
    email = connection.execute(
        "SELECT email "
        "FROM users "
        "WHERE username = ?",
        (logname,),
    )
    email = email.fetchall()
    email = email[0]['email']
    context = {"filename": filename,
               "logname": logname,
               "fullname": fullname,
               "email": email,
               "page_type": "edit", }
    context['url'] = flask.url_for("show_edit")
    return flask.render_template("account.html", **context)


@wolvwealth.app.route('/accounts/password/')
def show_password():
    """Perform change password."""
    if wolvwealth.views.helpers.check_login():
        return flask.redirect(flask.url_for("show_login"))
    logname = flask.session['username']
    context = {"page_type": "password"}
    context["logname"] = logname
    context['url'] = flask.url_for("show_edit")
    return flask.render_template("account.html", **context)


@wolvwealth.app.route('/accounts/logout/', methods=['POST'])
def logout():
    """Logout user, redirect to login page."""
    flask.session.pop('username')
    return flask.redirect(flask.url_for("show_login"))


@wolvwealth.app.route('/accounts/auth/')
def auth():
    """Perform authorize logged in."""
    if 'username' not in flask.session:
        flask.abort(403)
    response = flask.make_response()
    response.status_code = 200
    return response


def login():
    """Log a user in."""
    username = flask.request.form.get('username')
    password = flask.request.form.get('password')
    if username is None or password is None:
        flask.abort(400)

    # checks if user exists, and aborts if doesn't
    if wolvwealth.views.helpers.check_user_exists(username) is False:
        flask.abort(403)

    # checks if the user password matches up
    if wolvwealth.views.helpers.check_user_password(username, password) is False:
        flask.abort(403)
    else:
        flask.session['username'] = username


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


def create():
    """Create a new user."""
    username = flask.request.form.get('username')
    fileobj = flask.request.files["file"]
    uuid_basename = wolvwealth.views.helpers.file_save(fileobj.filename)
    path = wolvwealth.app.config["UPLOAD_FOLDER"]/uuid_basename
    fileobj.save(path)
    fullname = flask.request.form.get('fullname')
    email = flask.request.form.get('email')
    password_db_string = hashpass()
    password = flask.request.form.get('password')
    if (username is None or password is None or fullname is None):
        flask.abort(400)
    if (email is None or fileobj is None):
        flask.abort(400)
    connection = wolvwealth.model.get_db()

    if wolvwealth.views.helpers.check_user_exists(username) is True:
        flask.abort(409)
    connection.execute(
        "INSERT INTO users "
        "(username, fullname, email, filename, password, created) "
        "VALUES "
        "(?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (username, fullname, email, uuid_basename, password_db_string)
    )
    flask.session['username'] = username


def edit():
    """Edit a users information."""
    if 'username' not in flask.session:
        flask.abort(403)
    fullname = flask.request.form.get('fullname')
    username = flask.session['username']
    email = flask.request.form.get('email')
    if fullname is None or email is None:
        flask.abort(400)
    connection = wolvwealth.model.get_db()
    fileobj = flask.request.files["file"]
    filename = fileobj.filename
    if filename != '':
        # deletes post from filesystem
        cur = connection.execute(
            "SELECT filename "
            "FROM users "
            "WHERE username = ?",
            (username,)
        )
        user_filename = cur.fetchall()[0]['filename']
        os.remove(wolvwealth.app.config['UPLOAD_FOLDER']/user_filename)
        uuid_basename = wolvwealth.views.helpers.file_save(filename)
        path = wolvwealth.app.config["UPLOAD_FOLDER"]/uuid_basename
        fileobj.save(path)
        cur = connection.execute(
            "UPDATE users "
            "SET filename = ? "
            "WHERE filename = ? ",
            (uuid_basename, user_filename)
        )
    cur = connection.execute(
        "UPDATE users "
        "SET fullname = ?, email = ? "
        "WHERE username = ? ",
        (fullname, email, username)
        )


def delete():
    """Delete a users account."""
    if 'username' not in flask.session:
        flask.abort(403)
    username = flask.session['username']
    connection = wolvwealth.model.get_db()
    flask.session.clear()
    # deletes profile pic from os
    cur = connection.execute(
        "SELECT filename "
        "FROM users "
        "WHERE username = ?",
        (username,)
    )
    profile_pic = cur.fetchall()[0]['filename']
    os.remove(wolvwealth.app.config['UPLOAD_FOLDER']/profile_pic)
    # finds and removes all related post images from filesystem
    cur = connection.execute(
        "SELECT filename "
        "FROM posts "
        "WHERE owner = ?",
        (username,)
    )
    posts = cur.fetchall()
    for post in posts:
        post_pic = post['filename']
        os.remove(wolvwealth.app.config['UPLOAD_FOLDER']/post_pic)
    connection.execute(
        "DELETE FROM users "
        "WHERE username = ?",
        (username,),
    )


def change_pass():
    """Change a users password."""
    username = flask.session['username']
    if 'username' not in flask.session:
        flask.abort(403)
    password = flask.request.form.get('password')
    new_password = flask.request.form.get('new_password1')
    password_check = flask.request.form.get('new_password2')
    if password is None or new_password is None or password_check is None:
        flask.abort(400)
    connection = wolvwealth.model.get_db()

    if check_user_password(username, password) is False:
        print("password authentication failed")
        flask.abort(403)
    if new_password != password_check:
        flask.abort(401)
    else:
        algorithm = 'sha512'
        salt = uuid.uuid4().hex
        hash_obj = hashlib.new(algorithm)
        password_salted = salt + new_password
        hash_obj.update(password_salted.encode('utf-8'))
        password_hash = hash_obj.hexdigest()
        password_db_string = "$".join([algorithm, salt, password_hash])
        connection.execute(
            "UPDATE users "
            "SET password = ? "
            "WHERE username = ? ",
            (password_db_string, username)
        )


@wolvwealth.app.route('/accounts/', methods=['POST'])
def accounts():
    """Perform account POST actions like create/delete/edit accounts."""
    operation = flask.request.form.get('operation')
    target = flask.request.args.get('target')
    if operation == 'login':
        login()
    elif operation == 'create':
        create()
    elif operation == 'edit_account':
        edit()
    elif operation == 'delete':
        delete()
    elif operation == 'update_password':
        change_pass()
    if target is None:
        target = "/"
    return flask.redirect(target)

def check_auth():
    """Log a user in."""
    # checks if basic auth is included in request
    if flask.request.authorization is None:
        return False
    # checks if username and password are provided
    username = flask.request.authorization.get('username')
    password = flask.request.authorization.get('password')
    if username is None or password is None:
        return False

    # checks if user exists, and aborts if doesn't
    if check_user_exists(username) is False:
        return False
    # checks if user password matches up
    if check_user_password(username, password) is False:
        return False
    return True


def check_login():
    """Check if user is logged in."""
    if 'username' not in flask.session:
        return True
    return False

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

def check_auth_and_login():
    """Check both auth and login for P3."""
    if (check_login() and not
            wolvwealth.api.auth_helper.check_auth()):
        raise InvalidUsage("Forbidden", status_code=403)
    if flask.request.authorization is None:
        logname = flask.session['username']
    else:
        logname = flask.request.authorization.get('username')
    return logname
