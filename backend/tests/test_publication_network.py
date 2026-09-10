"""mDNS deployment mappings retain the configured publication host and isolate secrets."""

import importlib.util
import json
import socket
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "publication_network",
    Path(__file__).resolve().parents[2] / "scripts/operations/publication_network.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_mdns_mapping_uses_deploy_host_resolution_without_copying_credentials(monkeypatch):
    calls = []

    def resolve(host, *args):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.50.10", 0))]

    monkeypatch.setattr(module.socket, "getaddrinfo", resolve)
    config = {
        "services": {
            "api": {
                "environment": {
                    "NORTHSTAR_DATA_HUB_URL": "https://core.local:19090",
                    "SECRET": "private",
                }
            },
            "worker": {"environment": {"NORTHSTAR_DATA_HUB_URL": "https://core.local:19090"}},
            "frontend": {"environment": {}},
        }
    }
    with module.publication_hosts(config) as arguments:
        path = Path(arguments[1])
        value = json.loads(path.read_text())
        assert value == {
            "services": {
                name: {"extra_hosts": {"core.local": "192.168.50.10"}} for name in ("api", "worker")
            }
        }
        assert "private" not in path.read_text()
    assert not path.exists()
    assert calls == ["core.local"]
    assert (
        config["services"]["api"]["environment"]["NORTHSTAR_DATA_HUB_URL"]
        == "https://core.local:19090"
    )


def test_unresolved_mdns_refuses_container_start(monkeypatch):
    def fail(*args):
        raise socket.gaierror("unavailable")

    monkeypatch.setattr(module.socket, "getaddrinfo", fail)
    config = {
        "services": {
            "api": {"environment": {"NORTHSTAR_DATA_HUB_URL": "http://missing.local:19090"}}
        }
    }
    with pytest.raises(ValueError, match="missing.local"):
        with module.publication_hosts(config):
            pytest.fail("must not launch containers with unresolved host")
