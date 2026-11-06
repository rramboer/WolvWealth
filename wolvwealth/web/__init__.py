"""Web layer: the Flask app factory, HTML views, and JSON API.

Thin adapters only. Views and API routes parse the request, call a service,
and render or serialize the answer. Nothing here does SQL or maths.
"""

import flask
import werkzeug.exceptions

from wolvwealth.domain.errors import DataError
from wolvwealth.services.container import Services
from wolvwealth.storage.secrets import load_or_create_secret_key
from wolvwealth.web import context, security


def create_app(services: Services = None) -> flask.Flask:
    """Build the Flask app around a Services object (built from the environment by default)."""
    services = services or Services.build()
    app = flask.Flask(__name__)  # templates/ and static/ live next to this file
    app.config["SECRET_KEY"] = load_or_create_secret_key(services.settings.secret_key_path)
    app.config["SESSION_COOKIE_NAME"] = "wolvwealth"
    app.extensions["wolvwealth"] = services
    security.install(app)  # localhost is reachable by any page in the browser; see web/security.py

    from wolvwealth.web.api import blueprint as api_blueprint  # pylint: disable=import-outside-toplevel
    from wolvwealth.web.views import blueprint as views_blueprint  # pylint: disable=import-outside-toplevel

    app.register_blueprint(api_blueprint, url_prefix="/api")
    app.register_blueprint(views_blueprint)
    app.teardown_appcontext(context.close_connection)
    app.register_error_handler(DataError, _unreadable_data_page)
    app.register_error_handler(werkzeug.exceptions.HTTPException, _http_error)
    return app


def _http_error(error: werkzeug.exceptions.HTTPException):
    """JSON for anything under /api/, Werkzeug's own page elsewhere.

    An unknown /api/ path fails routing before Flask knows which blueprint
    owns it, so the API blueprint's handlers never see it.
    """
    if flask.request.path.startswith("/api/"):
        return flask.jsonify({"error": {"message": error.description, "status_code": error.code}}), error.code
    return error


def _unreadable_data_page(error: DataError):
    """Answer the browser with what is wrong and how to recover when stored data cannot be read."""
    return flask.render_template("data_error.html", message=error.message), 500
