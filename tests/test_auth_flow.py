"""Tests for register/login/logout/delete account flows."""

from tests.conftest import CSRF_TOKEN, install_csrf_token, login


def register(client, username="newuser", email="newuser@example.com", password="hunter2!"):
    token = install_csrf_token(client)
    return client.post(
        "/accounts/register/",
        data={"username": username, "email": email, "password": password, "csrf_token": token},
    )


def test_register_creates_account_and_logs_in(client):
    response = register(client)
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["username"] == "newuser"
    # New account gets an API key and can view its account page
    response = client.get("/account/")
    assert response.status_code == 200
    assert b"newuser@example.com" in response.data


def test_register_rejects_missing_fields(client):
    response = register(client, username="", email="", password="")
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert "username" not in session


def test_register_rejects_long_username(client):
    response = register(client, username="a" * 21)
    with client.session_transaction() as session:
        assert "username" not in session
    assert response.status_code == 302


def test_register_rejects_invalid_email(client):
    register(client, email="not-an-email")
    with client.session_transaction() as session:
        assert "username" not in session


def test_register_rejects_duplicate_username(client):
    register(client)
    with client.session_transaction() as session:
        session.clear()
    response = register(client, email="other@example.com")
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert "username" not in session


def test_login_success(client):
    response = login(client)
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["username"] == "devuser"


def test_login_wrong_password(client):
    response = login(client, password="wrong-password")
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert "username" not in session


def test_login_requires_csrf_token(client):
    response = client.post("/accounts/login/", data={"username": "devuser", "password": "wolvwealth-dev"})
    assert response.status_code == 400


def test_non_ascii_csrf_token_rejected_not_500(client):
    """A non-ASCII csrf_token must 400, not crash compare_digest."""
    install_csrf_token(client)
    response = client.post(
        "/accounts/login/",
        data={"username": "devuser", "password": "wolvwealth-dev", "csrf_token": "café"},
    )
    assert response.status_code == 400


def test_logout_requires_post(client):
    login(client)
    assert client.get("/accounts/logout/").status_code == 405


def test_logout_clears_session(client):
    login(client)
    response = client.post("/accounts/logout/", data={"csrf_token": CSRF_TOKEN})
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert "username" not in session


def test_delete_account_requires_post(client):
    login(client)
    assert client.get("/accounts/delete/").status_code == 405


def test_delete_account_clears_session_and_user(client):
    register(client)
    response = client.post("/accounts/delete/", data={"csrf_token": CSRF_TOKEN})
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert "username" not in session
    # The user is really gone: logging in again fails
    response = login(client, username="newuser", password="hunter2!")
    with client.session_transaction() as session:
        assert "username" not in session
    # And visiting the account page redirects instead of crashing
    assert client.get("/account/").status_code == 302


def test_stale_session_for_deleted_user_is_dropped(client):
    """A session naming a user that no longer exists must not stay logged in."""
    register(client)
    client.post("/accounts/delete/", data={"csrf_token": CSRF_TOKEN})
    with client.session_transaction() as session:
        session["username"] = "newuser"  # simulate a stale cookie
    assert client.get("/account/").status_code == 302
    with client.session_transaction() as session:
        assert "username" not in session
