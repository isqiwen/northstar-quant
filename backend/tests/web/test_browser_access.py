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


def test_ip_access_keeps_csrf_origin_and_explicit_app_policy() -> None:
    for host in (
        "core.local",
        "datahub.wangqiwen.me",
        "research.local",
        "quant.wangqiwen.me",
        "192.168.50.10",
        "198.51.100.10",
        "[2001:db8::10]",
    ):
        app = create_host(
            "IP workspace",
            (),
            allowed_hosts=(
                "research.local", "core.local", "quant.wangqiwen.me", "datahub.wangqiwen.me"
            ),
            allow_ip_hosts=True,
        )
        access = app.state.workspace_access

        @app.post("/api/change")
        def change(request: Request) -> dict:
            access.protect(request)
            return {"ok": True}

        with TestClient(
            app, base_url="http://127.0.0.1:18082", headers={"Host": f"{host}:18082"}
        ) as client:
            token = client.get("/api/browser-session").json()["csrf"]
            assert client.post("/api/change").status_code == 403
            headers = {"X-Northstar-CSRF": token, "Origin": f"http://{host}:18082"}
            assert client.post("/api/change", headers=headers).status_code == 200
            for override in (
                {"Origin": "http://evil.example"},
                {"Host": "evil.example"},
                {"Host": "999.999.999.999:18082"},
                {"Host": f"{host}:99999"},
                {"Sec-Fetch-Site": "cross-site"},
                {"X-Forwarded-Host": host},
            ):
                assert client.post("/api/change", headers=headers | override).status_code == 403
        with TestClient(
            create_host("Northstar Live", ()),
            base_url="http://127.0.0.1:18080",
            headers={"Host": f"{host}:18080"},
        ) as live:
            assert live.get("/api/browser-session").status_code == 403
