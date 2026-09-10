"""Make host-resolved mDNS publication endpoints reachable from Docker containers."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit


@contextmanager
def publication_hosts(config: dict) -> Iterator[list[str]]:
    services = {}
    addresses = {}
    for name, service in config["services"].items():
        url = service.get("environment", {}).get("NORTHSTAR_DATA_HUB_URL")
        host = urlsplit(url).hostname if url else None
        # Docker DNS does not inherit the host's mDNS resolver. Ordinary DNS and
        # literal addresses retain their normal resolution and failover behavior.
        if not host or not host.endswith(".local"):
            continue
        if host not in addresses:
            try:
                addresses[host] = socket.getaddrinfo(
                    host, None, socket.AF_INET, socket.SOCK_STREAM
                )[0][4][0]
            except OSError as error:
                raise ValueError(
                    f"无法在部署主机解析发布服务 {host}；请先检查主机网络解析"
                ) from error
        services[name] = {"extra_hosts": {host: addresses[host]}}
    if not services:
        yield []
        return
    # Only host mappings go in this file, never the resolved service environment.
    with TemporaryDirectory(prefix="northstar-publication-") as directory:
        path = Path(directory) / "hosts.json"
        path.write_text(json.dumps({"services": services}))
        yield ["-f", str(path)]
