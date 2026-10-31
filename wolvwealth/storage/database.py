"""SQLite connection handling and schema creation."""

import contextlib
import pathlib
import sqlite3

SQL_DIR = pathlib.Path(__file__).resolve().parent / "sql"

#: Columns added after the first release, applied to databases created
#: before them. Each entry is (table, column, ALTER TABLE clause). SQLite
#: cannot add a column inside the CREATE TABLE after the fact, and
#: ``schema.sql`` only runs for brand-new files, so an existing install
#: gets the column here on its next connection.
MIGRATIONS = (("portfolios", "risk_free_rate", "ALTER TABLE portfolios ADD COLUMN risk_free_rate REAL"),)


def _dict_factory(cursor, row):
    """Return rows as dicts keyed on column name."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class Database:
    """One SQLite file. Creates it from sql/ on first use."""

    def __init__(self, path: pathlib.Path) -> None:
        """Remember the path; nothing is opened until needed."""
        self.path = pathlib.Path(path)

    def ensure_schema(self) -> bool:
        """Create the database with schema and seed data if it does not exist. Return True if created."""
        if self.path.exists():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path))
        try:
            connection.executescript((SQL_DIR / "schema.sql").read_text())
            connection.executescript((SQL_DIR / "data.sql").read_text())
            connection.commit()
        finally:
            connection.close()
        return True

    def reset(self) -> None:
        """Delete the database file and rebuild it from the schema and seed."""
        if self.path.exists():
            self.path.unlink()
        self.ensure_schema()

    @staticmethod
    def migrate(connection: sqlite3.Connection) -> list:
        """Add any column in MIGRATIONS the database lacks; return the names added."""
        added = []
        for table, column, statement in MIGRATIONS:
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()  # fixed table names
            # Plain tuples on a fresh connection, dicts once the row factory is set.
            columns = {row["name"] if isinstance(row, dict) else row[1] for row in rows}
            if column not in columns:
                connection.execute(statement)
                added.append(f"{table}.{column}")
        if added:
            connection.commit()
        return added

    def open(self) -> sqlite3.Connection:
        """Open a connection with dict rows and foreign keys on. Caller commits and closes."""
        self.ensure_schema()
        connection = sqlite3.connect(str(self.path))
        self.migrate(connection)
        connection.row_factory = _dict_factory
        connection.execute("PRAGMA foreign_keys = ON")  # per-connection in sqlite
        return connection

    @contextlib.contextmanager
    def connect(self):
        """Context manager: one transaction, committed on success, rolled back on error."""
        connection = self.open()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
