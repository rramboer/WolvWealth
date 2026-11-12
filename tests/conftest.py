"""Shared test setup: run from the repo root and give tests an API client."""
import os
import pathlib
import sqlite3
import sys

import pytest

# The app reads ticker_universe.csv and historical_prices.csv from the
# working directory when it's imported, so move there first.
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import wolvwealth  # noqa: E402  (must come after the chdir)

API_KEY = "test-key"


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    """A Flask test client backed by a throwaway database with one API key."""
    db = tmp_path_factory.mktemp("db") / "wolvwealth.sqlite3"
    connection = sqlite3.connect(db)
    connection.executescript((ROOT / "sql" / "schema.sql").read_text())
    connection.executescript((ROOT / "sql" / "data.sql").read_text())
    connection.execute("INSERT INTO users(username, email, password) VALUES ('tester', 'tester@example.com', 'x')")
    connection.execute(
        "INSERT INTO tokens(owner, token, expires, uses) VALUES ('tester', ?, datetime('now', '+1 day'), 100000)",
        (API_KEY,),
    )
    connection.commit()
    connection.close()
    wolvwealth.app.config["DATABASE_FILENAME"] = db
    return wolvwealth.app.test_client()


@pytest.fixture(scope="session")
def optimize(client):
    """POST a body to /api/optimize/ and return (status code, parsed JSON)."""

    def post(body):
        response = client.post("/api/optimize/", json=body, headers={"Authorization": API_KEY})
        return response.status_code, response.get_json()

    return post
