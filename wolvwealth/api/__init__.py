"""WolvWealth API Init."""

import flask
import wolvwealth
from wolvwealth.api.state import ApplicationState
import wolvwealth.api.optimize
import wolvwealth.api.api_exceptions
import wolvwealth.api.auth
import wolvwealth.model

state = ApplicationState()  # Preload global resources on startup


@wolvwealth.app.route("/api/")
def api_default():
    """WolvWealth API Usage Endpoint."""
    return flask.jsonify(
        {
            "/api/": "API Usage Info",
            "/api/optimize/": "Optimize Portfolio",
            "/api/account/": "View Account Details",
        }
    )


@wolvwealth.app.route("/api/db/status/")
def db_test():
    """Test database in route. Requires admin privileges."""
    wolvwealth.api.auth.check_admin_priv(flask.headers.get("Authorization"))
    conn = wolvwealth.model.get_db()
    status = None
    try:
        conn.cursor()
        status = True
    except Exception as ex:
        status = False
    return flask.jsonify({"status": ("available" if status else "unavailable")})
