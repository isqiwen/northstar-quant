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


def test_lan_host_keeps_csrf_origin_and_live_isolation(monkeypatch) -> None:
    from northstar_quant.web.access import lan_hosts

    monkeypatch.setenv("NORTHSTAR_WEB_HOSTS", "192.168.50.10,192.168.50.20")
    for host in ("core.local", "research.local", "192.168.50.10", "192.168.50.20"):
        app = create_host(
            "LAN workspace", (), allowed_hosts=lan_hosts("research.local") + ("core.local",)
        )
        access = app.state.workspace_access

        @app.post("/api/change")
        def change(request: Request) -> dict:
            access.protect(request)
            return {"ok": True}

        with TestClient(app, base_url=f"http://{host}:18082") as client:
            token = client.get("/api/browser-session").json()["csrf"]
            assert client.post("/api/change").status_code == 403
            headers = {"X-Northstar-CSRF": token, "Origin": f"http://{host}:18082"}
            assert client.post("/api/change", headers=headers).status_code == 200
            for override in (
                {"Origin": "http://evil.example"},
                {"Host": "evil.example"},
                {"Host": "192.168.50.99:18082"},
                {"Host": f"{host}:99999"},
                {"Sec-Fetch-Site": "cross-site"},
                {"X-Forwarded-Host": host},
            ):
                assert client.post("/api/change", headers=headers | override).status_code == 403
        with TestClient(create_host("Northstar Live", ()), base_url=f"http://{host}:18080") as live:
            assert live.get("/api/browser-session").status_code == 403
