"""WolvWealth API Init."""

import flask
import wolvwealth
import wolvwealth.api.admin
import wolvwealth.api.api_exceptions
import wolvwealth.api.auth
import wolvwealth.api.optimize
from wolvwealth.api.state import ApplicationState


@wolvwealth.app.route("/api/", methods=["GET", "POST"])
def api_default():
    """WolvWealth API Usage Endpoint."""
    api_key = flask.request.headers.get("Authorization")
    if wolvwealth.api.admin.check_admin_priv(api_key):
        return flask.jsonify(
            {
                "/api/": "API Usage Info",
                "/api/optimize/": "Optimize Portfolio",
                "/api/account/": "View Account Details",
                "/api/db/status/": "Test Database Connection",
                "/api/db/dump/": "Dump Database",
                "/api/admin/add/": "Add Admin",
                "/api/admin/remove/": "Remove Admin",
                "/api/admin/user-info/": "Fetch User Info",
                "/api/admin/update-user/": "Update User Info",
                "/api/admin/delete-user/": "Delete User",
            }
        )
    return flask.jsonify(
        {
            "/api/": "API Usage Info",
            "/api/optimize/": "Optimize Portfolio",
            "/api/account/": "View Account Details",
        }
    )
