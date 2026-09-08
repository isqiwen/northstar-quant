"""HTTP browser authority remains bounded after replacing the server UI transport."""

from fastapi import Request
from fastapi.testclient import TestClient

from northstar_quant.web.host import create_host


def test_browser_commands_require_same_app_csrf_origin_and_unexpired_session() -> None:
    app = create_host("Northstar Research", ())
    access = app.state.workspace_access
    accepted = []

    @app.post("/api/change")
    async def change(request: Request) -> dict[str, bool]:
        access.protect(request)
        accepted.append(True)
        return {"accepted": True}

    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/change").status_code == 403
        token = client.get("/api/browser-session").json()["csrf"]
        client.headers["X-Northstar-CSRF"] = token
        for headers in (
            {"Origin": "https://untrusted.example"},
            {"Host": "untrusted.example"},
            {"X-Forwarded-Host": "127.0.0.1"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            assert client.post("/api/change", headers=headers).status_code == 403
        assert accepted == []
        assert client.post("/api/change").json() == {"accepted": True}
        access.close()
        assert client.post("/api/change").status_code == 403
        token = client.get("/api/browser-session").json()["csrf"]
        with TestClient(app, base_url="http://127.0.0.1") as other:
            assert other.post("/api/change", headers={"X-Northstar-CSRF": token}).status_code == 403
        assert accepted == [True]
