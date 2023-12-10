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
        }
    )


@wolvwealth.app.route("/api/db/")
def db_test():
    """Test db in route."""
    conn = wolvwealth.model.get_db()
    cur = conn.execute(
        "SELECT username, email FROM users WHERE username = ?;",
        ('awdeorio',)
    )
    stuff = cur.fetchall()
    return flask.jsonify({
        "users": stuff
    })
