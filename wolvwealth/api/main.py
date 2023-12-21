"""WolvWealth Main API."""
import flask
import wolvwealth


@wolvwealth.app.route("/api/")
def api_default():
    """WolvWealth API Usage Endpoint."""
    return flask.jsonify(
        {
            "/api/": "API Info",
            "/api/optimize/": "Optimize Portfolio",
            "/api/check/": "View Account Details",
            "/api/delete-account/": "Delete Account",
            "/api/beta-trial/": "Register Account (BETA)",
        }
    )


@wolvwealth.app.route("/api/db/status/")
def db_test():
    """Test db in route."""
    conn = wolvwealth.model.get_db()
    status = None
    try:
        conn.cursor()
        status = True
    except Exception as ex:
        status = False
    return flask.jsonify({"status": ("available" if status else "unavailable")})
