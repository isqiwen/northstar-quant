"""Real page/socket behavior with explicitly synthetic broker Module interfaces."""

import os
import subprocess
import sys
from pathlib import Path


def test_stream_page_keeps_fixed_commands_and_disables_unknown_remote_actions(
    tmp_path: Path,
    postgres_engine,
    clean_database,
) -> None:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        env={**os.environ, "NICEGUI_STORAGE_PATH": str(tmp_path / "ui-storage")},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "stream page behavior passed" in result.stdout


def _exercise_page() -> None:
    import html
    import json
    import re
    from copy import deepcopy
    from decimal import Decimal
    from threading import Event
    from urllib.parse import urlencode
    from uuid import uuid4

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    from northstar_quant.data_management.files import SourceFiles
    from northstar_quant.data_management.library import DataLibrary
    from northstar_quant.live import LiveAuth, LiveClient
    from northstar_quant.live import create_app as create_live
    from northstar_quant.nicegui_workspace import mount_workspace
    from northstar_quant.web_access import COOKIE, LocalWorkspaceMiddleware, WorkspaceAccess

    stream_id, query_id, baseline_id, check_id = (uuid4() for _ in range(4))
    report = {
        "status": "RECEIVING",
        "connection": "RECEIVING",
        "paused": True,
        "reason": "OPERATOR_PAUSE",
        "received": 10,
        "cursor": 10,
        "byte_count": 100,
        "market_age_seconds": None,
        "updated_at": "2026-09-07T01:03:00Z",
        "state": {},
        "binding": {
            "request": {"query_batch_id": str(query_id)},
            "profile": {"name": "synthetic-page-test"},
            "account_id": "synthetic-account",
            "instrument": "rb2610",
        },
        "account_progress": {
            "baseline_id": str(baseline_id),
            "through_sequence": 10,
            "pending": 0,
            "status": "READY",
            "reason": None,
            "entry_id": None,
        },
        "steps": [
            {
                "sequence": 5,
                "committed_at": "2026-09-07T01:03:00Z",
                "result": {
                    "bar": None,
                    "reason": None,
                    "intent": {
                        "target_fraction": "0.5",
                        "momentum": "0.01",
                        "valid_until": "2026-09-07T01:04:00Z",
                    },
                },
            }
        ],
        "archives": [],
    }
    budget_calls, controls, catchups = [], [], []
    budget_entered, budget_release = Event(), Event()
    read_failed = False

    def get(identifier):
        assert identifier == stream_id
        if read_failed:
            raise RuntimeError("synthetic read outage")
        return deepcopy(report)

    def create_budget(identifier, sequence, parent, *, limit_price, request_id):
        budget_calls.append((identifier, sequence, parent, limit_price, request_id))
        budget_entered.set()
        assert budget_release.wait(5), "synthetic command was not released"
        return {"budget_id": str(request_id)}

    def control(identifier, action, *, request_id):
        controls.append((identifier, action, request_id))
        report["reason"] = "OPERATOR_" + action
        return {"request_id": str(request_id)}

    def catchup(identifier, baseline, upper):
        catchups.append((identifier, baseline, upper))
        return deepcopy(report["account_progress"])

    engine = create_engine(os.environ["NORTHSTAR_TEST_DATABASE_URL"])
    library = DataLibrary(engine, SourceFiles(Path(os.environ["NICEGUI_STORAGE_PATH"]) / "sources"))
    auth = LiveAuth(read_token="r" * 48, control_token="c" * 48)
    live_app = create_live(engine, library, auth)
    owner = live_app.state.owner
    owner.streams.get, owner.streams.control, owner.streams.catchup_account = get, control, catchup
    owner.broker.ledger_context = lambda _: {"baseline_id": str(baseline_id), "entries": []}
    owner.opening_budgets.context = lambda _: {
        "order_checks": [
            {
                "check_id": str(check_id),
                "recorded_at": "2026-09-07T01:00:00Z",
                "status": "MATCHED",
            }
        ],
        "budgets": [],
    }
    owner.opening_budgets.create = create_budget
    live = LiveClient("http://127.0.0.1", auth, client=TestClient(live_app))
    access, app = WorkspaceAccess(), FastAPI()
    app.add_middleware(LocalWorkspaceMiddleware, access=access)

    @app.middleware("http")
    async def session(request, call_next):
        identifier = access.open(request)
        response = await call_next(request)
        access.set_cookie(request, response, identifier)
        return response

    from northstar_quant.apps.live.pages import register

    app.state.navigation = ()
    mount_workspace(app, access, lambda: register(app, live))

    def page(client):
        response = client.get(f"/streams/{stream_id}")
        assert response.status_code == 200, response.text
        raw = re.search(r"createApp\(parseElements\(String.raw`(.*?)`\)", response.text, re.S)
        elements = json.loads(html.unescape(raw[1]))
        identifier = re.search(r"['\"]client_id['\"]\s*:\s*['\"]([^'\"]+)['\"]", response.text)[1]
        url = "ws://127.0.0.1/_nicegui_ws/socket.io/?" + urlencode(
            {
                "EIO": 4,
                "transport": "websocket",
                "client_id": identifier,
                "implicit_handshake": "true",
                "document_id": str(uuid4()),
                "tab_id": str(uuid4()),
                "next_message_id": 0,
            }
        )
        return elements, identifier, url

    def element(elements, identifier):
        return next(
            (key, value)
            for key, value in elements.items()
            if value.get("props", {}).get("data-testid") == identifier
        )

    def receive(socket, elements):
        packet = socket.receive_text()
        if packet == "2":
            socket.send_text("3")
        elif packet.startswith("42["):
            name, payload = json.loads(packet[2:])
            if name == "update":
                for key, value in payload.items():
                    if key != "_id":
                        if value is None:
                            elements.pop(key, None)
                        else:
                            elements[key] = value
        return packet

    def until(socket, elements, predicate):
        for _ in range(100):
            if predicate():
                return
            receive(socket, elements)
        raise AssertionError("expected page update was not received")

    def connect(socket, elements):
        assert socket.receive_text().startswith("0")
        socket.send_text("40")
        while not receive(socket, elements).startswith("40"):
            pass

    event_number = 0

    def send(socket, elements, client_id, identifier, event_type="click", value=None):
        nonlocal event_number
        event_number += 1
        key, node = element(elements, identifier)
        matches = [event for event in node["events"] if event["type"] == event_type]
        assert len(matches) == 1, (identifier, event_type, node["events"])
        listener = matches[0]
        payload = {
            "client_id": client_id,
            "id": int(key),
            "listener_id": listener["listener_id"],
            "args": [] if event_type == "click" else [json.dumps(value)],
        }
        socket.send_text("42" + str(event_number) + json.dumps(["event", payload]))
        expected = "43" + str(event_number)
        while not receive(socket, elements).startswith(expected):
            pass

    def props(elements, identifier):
        return element(elements, identifier)[1]["props"]

    def text(elements, identifier):
        return element(elements, identifier)[1].get("text", "")

    with TestClient(live_app), TestClient(app, base_url="http://127.0.0.1") as client:
        elements, identifier, url = page(client)
        headers = {
            "origin": "http://127.0.0.1",
            "upgrade": "websocket",
            "cookie": COOKIE + "=" + client.cookies[COOKIE],
        }
        with client.websocket_connect(url, headers=headers) as socket:
            connect(socket, elements)
            original_choices = deepcopy(props(elements, "stream-budget-step")["options"])
            for name in ("stream-budget-step", "stream-budget-check"):
                send(
                    socket,
                    elements,
                    identifier,
                    name,
                    "update:modelValue",
                    props(elements, name)["options"][0],
                )
            exact_price = "3100.10000000000000000001"
            send(socket, elements, identifier, "stream-budget-price", "update:value", exact_price)
            send(socket, elements, identifier, "stream-account-through", "update:value", "7")
            report["received"] = report["cursor"] = 20
            report["account_progress"]["through_sequence"] = 20
            report["steps"].insert(0, {**deepcopy(report["steps"][0]), "sequence": 20})
            until(socket, elements, lambda: text(elements, "stream-received") == "20")
            assert props(elements, "stream-budget-step")["options"] == original_choices
            assert props(elements, "stream-archive-through")["value"] == "10"
            send(socket, elements, identifier, "stream-budget-submit")
            assert budget_entered.wait(2)
            send(socket, elements, identifier, "stream-budget-submit")
            send(socket, elements, identifier, "stream-control-pause")
            assert len(budget_calls) == 1 and controls == []
            budget_release.set()
            until(socket, elements, lambda: "操作已确认" in text(elements, "stream-command-status"))
            send(socket, elements, identifier, "stream-budget-submit")
            assert len(budget_calls) == 1
            assert budget_calls[0][:4] == (stream_id, 5, check_id, Decimal(exact_price))
            send(socket, elements, identifier, "stream-account-submit")
            until(socket, elements, lambda: len(catchups) == 1)
            assert catchups == [(stream_id, baseline_id, 7)]
            send(socket, elements, identifier, "stream-account-through", "update:value", "11")
            send(socket, elements, identifier, "stream-account-submit")
            until(
                socket,
                elements,
                lambda: "本页固定上界 10" in text(elements, "stream-command-status"),
            )
            assert len(catchups) == 1
            # Pin the real race: a second SID can arrive before the old socket
            # closes or runs its disconnect handler. The old page is no longer
            # allowed to run commands through either connection.
            with client.websocket_connect(url, headers=headers) as second:
                connect(second, elements)
                send(second, elements, identifier, "stream-control-pause")
                send(socket, elements, identifier, "stream-control-pause")
                assert controls == []
        # A new socket for the old document is a real reconnect. This action was
        # never executed/cached before, so command deduplication cannot mask a bug.
        with client.websocket_connect(url, headers=headers) as socket:
            connect(socket, elements)
            send(socket, elements, identifier, "stream-control-pause")
            assert controls == []

        elements, identifier, url = page(client)
        with client.websocket_connect(url, headers=headers) as socket:
            connect(socket, elements)
            read_failed = True
            until(socket, elements, lambda: "读取失败" in text(elements, "stream-poll-status"))
            assert props(elements, "stream-control-resume")["disable"] is True
            send(socket, elements, identifier, "stream-control-resume")
            assert controls == []
            send(socket, elements, identifier, "stream-control-pause")
            send(socket, elements, identifier, "stream-control-stop")
            assert props(elements, "stream-control-stop")["disable"] is True
            assert controls == []
        # A fresh document observes the owner again. Its commands remain actual
        # HTTP requests to Live, not direct calls into the Console process.
        read_failed = False
        elements, identifier, url = page(client)
        with client.websocket_connect(url, headers=headers) as socket:
            connect(socket, elements)
            send(socket, elements, identifier, "stream-control-pause")
            until(socket, elements, lambda: len(controls) == 1)
            until(
                socket,
                elements,
                lambda: not props(elements, "stream-control-stop").get("disable", False),
            )
            send(socket, elements, identifier, "stream-control-stop")
            until(socket, elements, lambda: len(controls) == 2)
            assert [action for _, action, _ in controls] == ["PAUSE", "STOP"]
            assert len({command for _, _, command in controls}) == 2
    live.close()
    engine.dispose()
    print("stream page behavior passed")


if __name__ == "__main__":
    _exercise_page()
