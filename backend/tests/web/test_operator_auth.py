"""Authenticated HTTP boundaries, revocation and bounded password work."""

import time

import pytest
from fastapi import Request

from northstar_quant.web.host import create_host
from northstar_quant.web.passwords import hash_password, verify_password
from northstar_quant.web.protobuf import methods
from tests.apps.browser import WORKSPACE_PASSWORD, ProtocolClient, login_response


def workspace():
    app = create_host("Northstar Research", ())
    app.state.protobuf_methods = methods("research")
    calls = []

    @app.get("/api/private")
    def private(request: Request):
        calls.append(request.state.operator)
        return {"operator": request.state.operator}

    return app, calls


def test_login_rotation_logout_expiry_and_cross_application_cookies():
    app, calls = workspace()
    other, _ = workspace()
    with (
        ProtocolClient(app, base_url="https://localhost") as client,
        ProtocolClient(other, base_url="https://localhost") as stranger,
    ):
        assert client.get("/api/browser-session").json()["authenticated"] is False
        assert not client.cookies
        assert client.get("/api/private").status_code == 401
        assert (
            client.post(
                "/api/login", json={"username": "owner", "password": "incorrect"}
            ).status_code
            == 401
        )
        assert calls == []
        response = login_response(client)
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        first = dict(client.cookies)
        assert client.get("/api/private").json() == {"operator": "owner"}
        stranger.cookies.update(first)
        assert stranger.get("/api/private").status_code == 401
        csrf = login_response(client).json()["csrf"]
        stranger.cookies.update(first)
        # A rotated identity cannot be reused even in its original application.
        old = client.cookies
        client.cookies = first
        assert client.get("/api/private").status_code == 401
        client.cookies = old
        assert client.post("/api/logout", json={}).status_code == 403
        assert (
            client.post("/api/logout", json={}, headers={"X-Northstar-CSRF": csrf}).status_code
            == 200
        )
        assert client.get("/api/private").status_code == 401
        login_response(client)
        access = app.state.workspace_access
        identifier = client.cookies[access.cookie]
        token, _ = access._sessions[identifier]
        access._sessions[identifier] = (token, time.monotonic() - 1)
        assert client.get("/api/browser-session").json()["authenticated"] is False
        assert client.get("/api/private").status_code == 401
        assert calls == ["owner"]


def test_password_hashes_are_salted_and_bad_or_excessive_attempts_do_not_authenticate():
    first, second = hash_password(WORKSPACE_PASSWORD), hash_password(WORKSPACE_PASSWORD)
    assert first != second
    assert WORKSPACE_PASSWORD not in first
    assert verify_password(WORKSPACE_PASSWORD, first)
    assert not verify_password("wrong", first)
    with pytest.raises(ValueError):
        verify_password(WORKSPACE_PASSWORD, first.replace("131072", "999999999"))
    app, calls = workspace()
    with ProtocolClient(app, base_url="http://localhost") as client:
        for _ in range(5):
            response = client.post("/api/login", json={"username": "owner", "password": "wrong"})
            assert response.status_code == 401
            assert "wrong" not in response.text
        assert login_response(client).status_code == 429
        assert client.get("/api/private").status_code == 401
        assert calls == []


def test_first_visit_creates_only_one_durable_account_and_restart_requires_login(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("NORTHSTAR_WORKSPACE_DIR", str(tmp_path / "fresh"))
    app, _ = workspace()
    with ProtocolClient(app, base_url="https://localhost") as client:
        assert client.get("/api/browser-session").json()["setup_required"]
        assert client.get("/api/private").status_code == 401
        credentials = {"username": "qiwen", "password": "private-test-password"}
        assert (
            client.post(
                "/api/setup", json=credentials, headers={"Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )
        assert client.post("/api/setup", json=credentials).status_code == 200
        assert not client.get("/api/browser-session").json()["setup_required"]
        cookies = dict(client.cookies)
        assert (
            client.post("/api/setup", json={"username": "second", "password": "other"}).status_code
            == 409
        )
    restarted, _ = workspace()
    with ProtocolClient(restarted, base_url="https://localhost") as client:
        client.cookies.update(cookies)
        assert client.get("/api/private").status_code == 401
        assert not client.get("/api/browser-session").json()["setup_required"]
        assert client.post("/api/login", json=credentials).status_code == 200
    stored = next((tmp_path / "fresh").glob("*.json"))
    assert "private-test-password" not in stored.read_text()
    assert stored.stat().st_mode & 0o777 == 0o600


def test_concurrent_setup_cannot_replace_the_first_account(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from northstar_quant.web.account import WorkspaceAccount

    account = WorkspaceAccount(tmp_path / "account.json")

    def create(username):
        try:
            account.create(username, "synthetic-test-password")
            return username
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, ("first", "second")))
    winners = [name for name in results if name]
    assert len(winners) == 1
    assert account.read()["username"] == winners[0]
