"""Server-rendered pages. One blueprint; each module registers its routes on it."""

import flask

blueprint = flask.Blueprint("views", __name__)

# Imported last, and only for the side effect of registering routes: each view
# module does `from wolvwealth.web.views import blueprint`, so the blueprint
# above has to exist before any of them are imported.
# pylint: disable=wrong-import-position
from wolvwealth.web.views import index, optimizer, portfolios, workspaces  # noqa: E402,F401
