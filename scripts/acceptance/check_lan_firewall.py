"""Exercise LAN filtering with real TCP/DNAT inside a disposable Linux network namespace.

Run: sudo unshare --net python3 scripts/acceptance/check_lan_firewall.py
"""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path


def run(*args: str, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs)


def verify() -> None:
    if os.geteuid() != 0 or os.readlink("/proc/self/ns/net") == os.readlink("/proc/1/ns/net"):
        raise SystemExit("Run as root inside an isolated network namespace, never the host network")
    source = Path(__file__).resolve().parents[1] / "operations/lan_firewall.py"
    children = []
    with tempfile.TemporaryDirectory(prefix="northstar-firewall-check-") as folder:
        copied = Path(folder) / "firewall.py"
        copied.write_text(
            source.read_text().replace("/run/lock/northstar-web-firewall.lock", f"{folder}/lock")
        )
        firewall = runpy.run_path(str(copied))
        try:
            run("ip", "link", "set", "lo", "up")
            for name, host_ip, peer_ip in (
                ("lan0", "192.168.50.10", "192.168.50.20"),
                ("backend0", "172.18.0.1", "172.18.0.2"),
            ):
                child = subprocess.Popen(["unshare", "--net", "sleep", "120"])
                children.append(child)
                # Wait until the child has entered its own namespace.
                import time

                for _ in range(100):
                    if os.readlink(f"/proc/{child.pid}/ns/net") != os.readlink("/proc/self/ns/net"):
                        break
                    time.sleep(0.01)
                else:
                    raise RuntimeError("child namespace did not start")
                run("ip", "link", "add", name, "type", "veth", "peer", "name", "peer0")
                run("ip", "link", "set", "peer0", "netns", str(child.pid))
                run("ip", "addr", "add", f"{host_ip}/24", "dev", name)
                run("ip", "link", "set", name, "up")
                prefix = ["nsenter", "-t", str(child.pid), "-n"]
                run(*prefix, "ip", "link", "set", "lo", "up")
                run(*prefix, "ip", "addr", "add", f"{peer_ip}/24", "dev", "peer0")
                run(*prefix, "ip", "link", "set", "peer0", "up")
                run(*prefix, "ip", "route", "add", "default", "via", host_ip)
            client, backend = children
            client_prefix = ["nsenter", "-t", str(client.pid), "-n"]
            run(*client_prefix, "ip", "addr", "add", "198.51.100.20/24", "dev", "peer0")
            run("ip", "addr", "add", "198.51.100.10/24", "dev", "lan0")
            run("ip", "route", "add", "default", "via", "192.168.50.20")
            run("sysctl", "-w", "net.ipv4.ip_forward=1")
            run("iptables", "-N", "DOCKER-USER")
            run("iptables", "-A", "FORWARD", "-j", "DOCKER-USER")
            run("iptables", "-A", "FORWARD", "-j", "ACCEPT")
            run("iptables", "-P", "INPUT", "DROP")
            firewall["apply"]("data-hub", 18082)

            def rules():
                return [
                    line
                    for line in run("iptables-save").stdout.splitlines()
                    if not line.startswith("#")
                ]

            before = rules()
            firewall["apply"]("data-hub", 18082)
            assert rules() == before, "repeat application changed rules"
            servers = [([], 18082), (["nsenter", "-t", str(backend.pid), "-n"], 3000)]
            for prefix, port in servers:
                server = subprocess.Popen(
                    [
                        *prefix,
                        sys.executable,
                        "-u",
                        "-c",
                        "import socket; s=socket.socket(); s.bind(('0.0.0.0'," + str(port) + ")); "
                        "s.listen(); print('ready',flush=True);\nwhile True:\n"
                        " c,_=s.accept(); c.sendall(b'ok'); c.close()",
                    ],
                    stdout=subprocess.PIPE,
                    text=True,
                )
                children.append(server)
                assert server.stdout.readline().strip() == "ready"

            def probe(source_ip: str, allowed: bool):
                code = (
                    "import socket; s=socket.socket(); s.settimeout(1); "
                    f"s.bind(({source_ip!r},0)); s.connect(('192.168.50.10',18082)); "
                    "assert s.recv(2)==b'ok'"
                )
                result = subprocess.run(
                    [*client_prefix, sys.executable, "-c", code], capture_output=True
                )
                assert (result.returncode == 0) == allowed, result.stderr.decode()

            for dnat in (False, True):
                if dnat:
                    run(
                        "iptables",
                        "-t",
                        "nat",
                        "-A",
                        "PREROUTING",
                        "-p",
                        "tcp",
                        "--dport",
                        "18082",
                        "-j",
                        "DNAT",
                        "--to-destination",
                        "172.18.0.2:3000",
                    )
                probe("192.168.50.20", True)
                probe("198.51.100.20", False)
            firewall["apply"]("research", 18084)
            assert "NS-DATA-WEB" in run("iptables", "-S", "DOCKER-USER").stdout
            firewall["apply"]("data-hub", 18083)
            hooks = run("iptables", "-S", "DOCKER-USER").stdout
            assert "--ctorigdstport 18082" not in hooks and "--ctorigdstport 18083" in hooks
            print(
                "PASS: INPUT/Docker DNAT LAN allow, non-LAN deny, repeat/update and app isolation"
            )
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)


if __name__ == "__main__":
    verify()
