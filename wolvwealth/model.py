"""WolvWealth model (database) API."""

import sqlite3

import flask

import wolvwealth


def dict_factory(cursor, row):
    """Convert database row objects to a dictionary keyed on column name.

    This is useful for building dictionaries which are then used to render a
    template.  Note that this would be inefficient for large queries.
    """
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_db():
    """Open a new database connection.

    Flask docs:
    https://flask.palletsprojects.com/en/stable/appcontext/#storing-data
    """
    if "sqlite_db" not in flask.g:
        db_filename = wolvwealth.app.config["DATABASE_FILENAME"]
        flask.g.sqlite_db = sqlite3.connect(str(db_filename))
        flask.g.sqlite_db.row_factory = dict_factory

        # Foreign keys have to be enabled per-connection.  This is an sqlite3
        # backwards compatibility thing.
        flask.g.sqlite_db.execute("PRAGMA foreign_keys = ON")

    return flask.g.sqlite_db


@wolvwealth.app.teardown_appcontext
def close_db(error):
    """Close the database at the end of a request.

    Commits only if the request finished without an exception; otherwise the
    transaction is rolled back so failed handlers don't persist partial writes.
    """
    sqlite_db = flask.g.pop("sqlite_db", None)
    if sqlite_db is not None:
        if error is None:
            sqlite_db.commit()
        else:
            sqlite_db.rollback()
        sqlite_db.close()
