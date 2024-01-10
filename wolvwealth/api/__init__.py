"""WolvWealth API Init."""

import flask
import wolvwealth
from wolvwealth.api.state import ApplicationState
import wolvwealth.api.optimize
import wolvwealth.api.api_exceptions
import wolvwealth.api.auth
import wolvwealth.model

state = ApplicationState()  # Preload global resources on startup


@wolvwealth.app.route("/api/", methods=["GET", "POST"])
def api_default():
    """WolvWealth API Usage Endpoint."""
    api_key = flask.request.headers.get("Authorization")
    if wolvwealth.api.auth.check_admin_priv(api_key):
        return flask.jsonify(
            {
                "/api/": "API Usage Info",
                "/api/optimize/": "Optimize Portfolio",
                "/api/account/": "View Account Details",
                "/api/db/status/": "Test Database Connection",
                "/api/db/dump/": "Dump Database",
            }
        )
    return flask.jsonify(
        {
            "/api/": "API Usage Info",
            "/api/optimize/": "Optimize Portfolio",
            "/api/account/": "View Account Details",
        }
    )


@wolvwealth.app.route("/api/db/status/", methods=["GET", "POST"])
def db_test():
    """Test database in route. Requires admin privileges."""
    api_key = flask.request.headers.get("Authorization")
    wolvwealth.api.auth.check_admin_priv(api_key)
    conn = wolvwealth.model.get_db()
    status = None
    try:
        conn.cursor()
        status = True
    except Exception as ex:
        status = False
    return flask.jsonify({"status": ("available" if status else "unavailable")})


@wolvwealth.app.route("/api/db/dump/", methods=["GET", "POST"])
def db_dump():
    """Dump database in route. Requires admin privileges."""
    api_key = flask.request.headers.get("Authorization")
    wolvwealth.api.auth.check_admin_priv(api_key)
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
