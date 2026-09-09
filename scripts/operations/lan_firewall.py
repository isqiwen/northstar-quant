"""Own only each LAN frontend's IPv4 INPUT and Docker forwarding rules."""

from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

APPS = {"data-hub": "NS-DATA-WEB", "research": "NS-RESEARCH-WEB"}
PRIVATE = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, text=True, **kwargs)


def networks(addresses: list[dict], defaults: list[dict]) -> list[tuple[str, str]]:
    """Use physical/default-route LAN interfaces, excluding container and tunnel networks."""
    interfaces = {route.get("dev") for route in defaults}
    result = set()
    for item in addresses:
        name = item["ifname"]
        if name.startswith(("lo", "docker", "br-", "veth", "tun", "tap", "wg")):
            continue
        if name not in interfaces and not Path(f"/sys/class/net/{name}/device").exists():
            continue
        if "UP" not in item.get("flags", []):
            continue
        for address in item.get("addr_info", []):
            if address.get("family") != "inet" or address.get("scope") != "global":
                continue
            network = ipaddress.ip_network(
                f"{address['local']}/{address['prefixlen']}", strict=False
            )
            if any(network.subnet_of(private) for private in PRIVATE):
                result.add((name, str(network)))
    if not result:
        raise ValueError("未识别到可用的 IPv4 私有局域网，拒绝开放 Web 端口")
    return sorted(result)


def apply(app: str, port: int) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("无效 Web 端口")
    addresses = json.loads(
        run("ip", "-j", "-4", "addr", "show", "scope", "global", capture_output=True).stdout
    )
    defaults = json.loads(
        run("ip", "-j", "-4", "route", "show", "default", capture_output=True).stdout
    )
    allowed = networks(addresses, defaults)
    chain = APPS[app]
    # Refuse unsupported Docker firewall backends before touching any rules.
    run("iptables", "-w", "-S", "DOCKER-USER", stdout=subprocess.DEVNULL)
    with Path("/run/lock/northstar-web-firewall.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        exists = (
            subprocess.run(["iptables", "-w", "-S", chain], capture_output=True).returncode == 0
        )
        if not exists:
            run("iptables", "-w", "-N", chain)
        # Restore atomically replaces only this application's chain, never UFW/Docker rules.
        rules = ["*filter", f"-F {chain}", f"-A {chain} -i lo -j ACCEPT"]
        rules += [
            f"-A {chain} -i {interface} -s {network} -j ACCEPT" for interface, network in allowed
        ]
        rules += [f"-A {chain} -j DROP", "COMMIT", ""]
        run("iptables-restore", "--wait", "--noflush", input="\n".join(rules))
        hooks = [
            ("INPUT", ["-p", "tcp", "--dport", str(port), "-j", chain]),
            (
                "DOCKER-USER",
                [
                    "-p",
                    "tcp",
                    "-m",
                    "conntrack",
                    "--ctdir",
                    "ORIGINAL",
                    "--ctstate",
                    "DNAT",
                    "--ctorigdstport",
                    str(port),
                    "-j",
                    chain,
                ],
            ),
        ]
        for parent, rule in hooks:
            existing = run("iptables", "-w", "-S", parent, capture_output=True).stdout
            for line in existing.splitlines():
                parts = shlex.split(line)
                if parts[-2:] != ["-j", chain]:
                    continue
                key = "--dport" if parent == "INPUT" else "--ctorigdstport"
                if key in parts and parts[parts.index(key) + 1] != str(port):
                    run("iptables", "-w", "-D", *parts[1:])
            if subprocess.run(
                ["iptables", "-w", "-C", parent, *rule], capture_output=True
            ).returncode:
                run("iptables", "-w", "-I", parent, "1", *rule)
    print(
        f"{app} TCP {port} 仅允许局域网：{', '.join(network for _, network in allowed)}", flush=True
    )


def write(path: Path, content: str, mode: int) -> None:
    if path.exists() and path.read_text() == content:
        return
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".northstar-firewall-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def install(app: str, port: int) -> None:
    apply(app, port)
    script = Path(f"/opt/northstar/apps/{app}/lan_firewall.py")
    script.parent.mkdir(parents=True, exist_ok=True)
    write(script, Path(__file__).read_text(), 0o644)
    unit = f"northstar-{app}-firewall.service"
    write(
        Path("/etc/systemd/system") / unit,
        f"""[Unit]
Description=Northstar {app} LAN Web firewall
After=network-online.target docker.service ufw.service
Wants=network-online.target
PartOf=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {script} apply {app} {port}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target docker.service
""",
        0o644,
    )
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", unit)
    run("systemctl", "restart", unit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "apply"))
    parser.add_argument("app", choices=APPS)
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ValueError("防火墙配置需要 root 权限")
        (install if args.action == "install" else apply)(args.app, args.port)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"局域网防火墙配置失败：{error}\n")
