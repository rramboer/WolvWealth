"""Smoke tests for the server-rendered pages."""

from tests.conftest import login


def test_index_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"WolvWealth" in response.data


def test_login_page_loads(client):
    response = client.get("/login/")
    assert response.status_code == 200
    assert b"Sign In" in response.data


def test_register_page_loads(client):
    response = client.get("/register/")
    assert response.status_code == 200
    assert b"Create an account" in response.data


def test_optimizer_requires_login(client):
    response = client.get("/optimizer/")
    assert response.status_code == 302
    assert "/login/" in response.headers["Location"]


def test_account_requires_login(client):
    response = client.get("/account/")
    assert response.status_code == 302
    assert "/login/" in response.headers["Location"]


def test_optimizer_loads_when_logged_in(client):
    login(client)
    response = client.get("/optimizer/")
    assert response.status_code == 200
    assert b"reactEntry" in response.data


def test_account_page_shows_user_details(client):
    login(client)
    response = client.get("/account/")
    assert response.status_code == 200
    assert b"devuser" in response.data
    assert b"insecure-dev-user-token" in response.data
