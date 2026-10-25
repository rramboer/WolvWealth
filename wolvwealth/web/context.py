"""Per-request access to services and a database connection."""
import flask

from wolvwealth.services.container import Services
from wolvwealth.storage.repository import PortfolioRepository


def services() -> Services:
    """Return the Services object the app was created with."""
    return flask.current_app.extensions["wolvwealth"]


def repo() -> PortfolioRepository:
    """Return a repository on this request's connection (opened on first use, committed at teardown)."""
    if "wolvwealth_db" not in flask.g:
        flask.g.wolvwealth_db = services().database.open()
    return services().repository(flask.g.wolvwealth_db)


def market():
    """Return the current MarketData snapshot."""
    return services().market.data


def close_connection(error=None) -> None:
    """Commit (or roll back on error) and close the request's connection."""
    connection = flask.g.pop("wolvwealth_db", None)
    if connection is None:
        return
    if error is None:
        connection.commit()
    else:
        connection.rollback()
    connection.close()


def current_workspace():
    """Return the workspace open in the session, or None (dropping it if it was deleted meanwhile)."""
    name = flask.session.get("workspace")
    if name is None:
        return None
    if not repo().workspace_exists(name):
        flask.session.pop("workspace", None)
        return None
    return name
