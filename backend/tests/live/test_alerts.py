"""An alert receiver cannot redirect credentials or force an unbounded body read."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from northstar_quant.live.alerts import DeliveryUnavailable, Webhook


def test_receiver_acknowledgement_is_bounded_and_never_redirected():
    requests = []
    status = [302]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            requests.append((self.path, self.headers["authorization"]))
            self.rfile.read(int(self.headers["content-length"]))
            self.send_response(status[0])
            self.send_header("Location", "/redirected-secret")
            # No body follows. The sender must inspect only the acknowledgement.
            self.send_header("Content-Length", "1000000000")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sink = Webhook(f"http://127.0.0.1:{server.server_port}/alerts", "private-receiver-token")
    event = {"event_id": "fixed-event", "status": "DEGRADED"}
    try:
        with pytest.raises(DeliveryUnavailable) as failure:
            sink(event)
        assert "private-receiver-token" not in str(failure.value)
        status[0] = 200
        sink(event)
        assert requests == [("/alerts", "Bearer private-receiver-token")] * 2
    finally:
        sink.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("url", "token"),
    [
        ("http://public.example/alerts", ""),
        ("https://user:secret@example.org/alerts", ""),
        ("https://example.org:bad/alerts", ""),
        ("https://example.org/alerts", "secret\r\nInjected: value"),
    ],
)
def test_receiver_configuration_rejects_credential_exposure_without_echoing_it(url, token):
    with pytest.raises(ValueError) as failure:
        Webhook(url, token)
    assert "secret" not in str(failure.value)
