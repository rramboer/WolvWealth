"""WolvWealth model (database) API."""
import pathlib
import sqlite3
import flask
import wolvwealth


def dict_factory(cursor, row):
    """Convert database row objects to a dictionary keyed on column name.

    This is useful for building dictionaries which are then used to render a
    template.  Note that this would be inefficient for large queries.
    """
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def ensure_db():
    """Create the database from the packaged SQL if it does not exist yet.

    The database holds per-person workspaces and portfolios, so it lives in
    var/ outside of git and every machine builds its own on first run.
    """
    db_filename = pathlib.Path(wolvwealth.app.config["DATABASE_FILENAME"])
    if db_filename.exists():
        return
    sql_dir = pathlib.Path(wolvwealth.app.config["SQL_DIR"])
    connection = sqlite3.connect(str(db_filename))
    connection.executescript((sql_dir/"schema.sql").read_text())
    connection.executescript((sql_dir/"seed.sql").read_text())
    connection.commit()
    connection.close()


def get_db():
    """Open a new database connection.

    Flask docs:
    https://flask.palletsprojects.com/en/1.0.x/appcontext/#storing-data
    """
    if 'sqlite_db' not in flask.g:
        db_filename = wolvwealth.app.config['DATABASE_FILENAME']
        flask.g.sqlite_db = sqlite3.connect(str(db_filename))
        flask.g.sqlite_db.row_factory = dict_factory

        # Foreign keys have to be enabled per-connection.  This is an sqlite3
        # backwards compatibility thing.
        flask.g.sqlite_db.execute("PRAGMA foreign_keys = ON")

    return flask.g.sqlite_db


@wolvwealth.app.teardown_appcontext
def close_db(error):
    """Close the database at the end of a request.

    Flask docs:
    https://flask.palletsprojects.com/en/1.0.x/appcontext/#storing-data
    """
    assert error or not error  # Needed to avoid superfluous style error
    sqlite_db = flask.g.pop('sqlite_db', None)
    if sqlite_db is not None:
        sqlite_db.commit()
        sqlite_db.close()
