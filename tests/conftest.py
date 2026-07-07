"""Shared pytest fixtures.

WOLVWEALTH_DATA_DIR must point at the small fixture CSVs before wolvwealth is
imported, because ApplicationState loads price data at import time.
"""

import os
import pathlib
import sqlite3

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"
REPO_ROOT = pathlib.Path(__file__).parent.parent
os.environ.setdefault("WOLVWEALTH_DATA_DIR", str(FIXTURES_DIR))

import pytest  # noqa: E402

import wolvwealth  # noqa: E402

# Known development seed credentials from sql/data.sql
DEV_USER = "devuser"
DEV_ADMIN = "devadmin"
DEV_PASSWORD = "wolvwealth-dev"
DEV_USER_TOKEN = "insecure-dev-user-token"
DEV_ADMIN_TOKEN = "insecure-dev-admin-token"

CSRF_TOKEN = "test-csrf-token"


@pytest.fixture
def app(tmp_path):
    """Flask app configured with a throwaway database built from sql/."""
    db_path = tmp_path / "wolvwealth-test.sqlite3"
    connection = sqlite3.connect(db_path)
    for script in ("schema.sql", "data.sql"):
        connection.executescript((REPO_ROOT / "sql" / script).read_text())
    connection.commit()
    connection.close()
    wolvwealth.app.config.update(TESTING=True, DATABASE_FILENAME=db_path)
    return wolvwealth.app


@pytest.fixture
def client(app):
    """Flask test client backed by the throwaway database."""
    return app.test_client()


def install_csrf_token(client) -> str:
    """Seed the client's session with a CSRF token and return it."""
    with client.session_transaction() as session:
        session["csrf_token"] = CSRF_TOKEN
    return CSRF_TOKEN


def login(client, username: str = DEV_USER, password: str = DEV_PASSWORD):
    """Log the test client in through the real login route."""
    token = install_csrf_token(client)
    return client.post(
        "/accounts/login/",
        data={"username": username, "password": password, "csrf_token": token},
    )
