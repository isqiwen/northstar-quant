"""Exercise the actual server lifecycle and Socket.IO wire boundary, without PG."""

import os
import subprocess
import sys
from pathlib import Path


def test_gui_sessions_reject_cross_client_messages_and_expiry_across_process_restarts(
    tmp_path: Path,
) -> None:
    # NiceGUI's real lifecycle is one server per process, not a resettable app
    # fixture. Both independent processes exercise the same installed Interface.
    script = r"""
import html
import json
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from nicegui import ui
from starlette.websockets import WebSocketDisconnect

from northstar_quant import web_access
from northstar_quant.nicegui_workspace import mount_workspace
from northstar_quant.web_access import COOKIE, LocalWorkspaceMiddleware, WorkspaceAccess

now = 0.0
web_access.time = SimpleNamespace(monotonic=lambda: now)
access = WorkspaceAccess()
actions = []
uploads = []
upload_paths = {}
lifecycle = []

@asynccontextmanager
async def lifespan(app):
    lifecycle.append('start')
    yield
    lifecycle.append('stop')

app = FastAPI(lifespan=lifespan)
app.add_middleware(LocalWorkspaceMiddleware, access=access)

@app.middleware('http')
async def session(request, call_next):
    identifier = access.open(request)
    response = await call_next(request)
    access.set_cookie(request, response, identifier)
    return response

@ui.page('/probe', api_router=app.router)
def probe(request: Request):
    owner = request.state.workspace_session_id
    ui.button('Record synthetic action', on_click=lambda: actions.append(owner))
    uploader = ui.upload(on_upload=lambda event: uploads.append(event.file.name))
    upload_paths[ui.context.client.id] = uploader._props['url']

# No business pages are called; these values must never be used by this probe.
mount_workspace(app, access, lambda: None)

def page(client):
    response = client.get('/probe')
    assert response.status_code == 200
    raw = re.search(r'createApp\(parseElements\(String.raw`(.*?)`\)', response.text, re.S)
    assert raw is not None, response.text
    elements = json.loads(html.unescape(raw[1]))
    button = next((key, value) for key, value in elements.items()
                  if value.get('props', {}).get('label') == 'Record synthetic action')
    client_match = re.search(r"['\"]client_id['\"]\s*:\s*['\"]([^'\"]+)['\"]", response.text)
    assert client_match is not None
    client_id = client_match[1]
    return client_id, client.cookies[COOKIE], int(button[0]), button[1]['events'][0]['listener_id']

def path(identifier):
    return 'ws://127.0.0.1/_nicegui_ws/socket.io/?' + urlencode({
        'EIO': 4, 'transport': 'websocket', 'client_id': identifier,
        'implicit_handshake': 'true', 'document_id': str(uuid4()),
        'tab_id': str(uuid4()), 'next_message_id': 0,
    })

def packet(socket, prefix):
    for _ in range(40):
        value = socket.receive_text()
        if value == '2':
            socket.send_text('3')
        elif value.startswith(prefix):
            return value
    raise AssertionError('expected Socket.IO reply not received')

def connect(socket):
    assert socket.receive_text().startswith('0')
    socket.send_text('40')
    assert packet(socket, '40').startswith('40{')

def event(socket, event_id, target, name='event'):
    identifier, _, button, listener = target
    payload = {'client_id': identifier, 'id': button, 'listener_id': listener, 'args': []}
    socket.send_text('42' + str(event_id) + json.dumps([name, payload]))
    return packet(socket, '43' + str(event_id))

with TestClient(app, base_url='http://127.0.0.1') as client:
    first = page(client)
    client.cookies.clear()
    second = page(client)
    assert first[1] != second[1]
    headers = {'origin': 'http://127.0.0.1', 'cookie': COOKIE + '=' + first[1],
               'upgrade': 'websocket'}
    other_headers = {'origin': 'http://127.0.0.1', 'cookie': COOKIE + '=' + second[1],
                     'upgrade': 'websocket'}
    upload_path = upload_paths[first[0]]
    for rejected in (other_headers, {'origin': 'https://other.invalid'},
                     {'origin': 'http://127.0.0.1', 'cookie': COOKIE + '=missing'}):
        response = client.post(upload_path, files={'file': ('probe.csv', b'bounded')},
                               headers=rejected)
        assert response.status_code == 403, response.text
        assert uploads == []
    response = client.post(upload_path, files={'file': ('large.csv', b'x' * (6 * 1024 * 1024 + 1))},
                           headers=headers)
    assert response.status_code in {400, 413}, response.text
    assert uploads == []
    response = client.post(upload_path, files={'file': ('probe.csv', b'bounded')}, headers=headers)
    assert response.status_code == 200, response.text
    assert uploads == ['probe.csv']
    for rejected in ({'origin': 'https://other.invalid'},
                     {'origin': 'http://127.0.0.1', 'cookie': COOKIE + '=missing'},
                     {'origin': 'http://127.0.0.1', 'host': 'other.invalid'}):
        try:
            with client.websocket_connect(path(first[0]), headers=rejected):
                raise AssertionError('unauthorized transport was accepted')
        except WebSocketDisconnect as error:
            assert error.code == 1008
    with client.websocket_connect(path(first[0]), headers=other_headers) as wrong:
        assert wrong.receive_text().startswith('0')
        wrong.send_text('40')
        assert packet(wrong, '44').startswith('44')
    with client.websocket_connect(path(first[0]), headers=headers) as one:
        connect(one)
        with client.websocket_connect(path(second[0]), headers=other_headers) as two:
            connect(two)
            assert event(one, 1, first) == '431[]'
            assert actions == [first[1]]
            assert event(one, 2, second) == '432[false]'
            assert actions == [first[1]]
            assert event(two, 3, second) == '433[]'
            assert actions == [first[1], second[1]]
            assert event(one, 4, second, 'handshake') == '434[false]'
            for number, name in enumerate(('javascript_response', 'ack', 'log'), 5):
                assert event(one, number, second, name) == '43' + str(number) + '[false]'
            now = 1801.0
            one.send_text('428' + json.dumps(['event', {
                'client_id': first[0], 'id': first[2], 'listener_id': first[3], 'args': [],
            }]))
            assert packet(one, '41') == '41'
            assert actions == [first[1], second[1]]
assert lifecycle == ['start', 'stop']
print('workspace isolation and expiry passed')
"""
    for attempt in range(2):
        environment = {
            **os.environ,
            "NICEGUI_STORAGE_PATH": str(tmp_path / f"storage-{attempt}"),
        }
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "workspace isolation and expiry passed" in result.stdout
