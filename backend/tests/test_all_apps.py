"""Batch ordering and destructive barriers without contacting personal hosts."""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/operations/{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize(
    "action,order",
    [
        ("start", ["database", "data-hub", "research", "live"]),
        ("stop", ["live", "research", "data-hub", "database"]),
        ("status", ["database", "data-hub", "research", "live"]),
    ],
)
def test_batch_order_and_failure_policy(monkeypatch, action, order):
    batch = module("all_apps")
    seen = []

    def execute(command, **kwargs):
        seen.append(command[3])
        return SimpleNamespace(returncode=1 if command[3] == "data-hub" else 0)

    monkeypatch.setattr(batch.subprocess, "run", execute)
    args = SimpleNamespace(action=action, config=Path("hosts.toml"), dry_run=False, follow=False)
    assert batch.run(args, dict.fromkeys(["database", "data-hub", "research", "live"]), ROOT) == 1
    assert seen == (order[:2] if action == "start" else order)


def test_follow_labels_each_application(capsys):
    batch = module("all_apps")
    assert (
        batch.follow({app: [sys.executable, "-c", "print('message')"] for app in ["a", "b"]}) == 0
    )
    output = capsys.readouterr().out
    assert "[a] message" in output and "[b] message" in output


@pytest.fixture
def share(tmp_path, monkeypatch):
    shared = module("purge_shared")
    root = tmp_path / "market"
    root.mkdir()
    monkeypatch.setattr(shared, "MARKET", root)
    monkeypatch.setattr(shared.socket, "getaddrinfo", lambda *args: [])
    marker = root / shared.MARKER
    marker.write_text("76c6aae1-5059-47ba-8b4c-66302848db35")
    (root / "objects").mkdir()
    (root / "objects/data.parquet").write_bytes(b"data")
    mounts = [{"target": str(root), "source": "nas.local:/quant", "fstype": "nfs4"}]
    host = {"run": lambda *args: json.dumps({"filesystems": mounts}), "preflight": lambda: set()}
    return shared, root, host, mounts


def test_clear_removes_only_active_data_and_preserves_snapshot_targets(share, tmp_path):
    shared, root, host, _ = share
    outside = tmp_path / "snapshot"
    outside.mkdir()
    (outside / "old.parquet").write_bytes(b"keep")
    snapshots = root / "@Recently-Snapshot"
    snapshots.mkdir()
    (snapshots / "link").symlink_to(outside, target_is_directory=True)
    (root / "objects/link").symlink_to(outside, target_is_directory=True)
    identity = shared.inspect_share(host, "nas.local")
    shared.clear_share(host, "nas.local", identity)
    assert list(root.iterdir()) == [snapshots]
    assert (outside / "old.parquet").read_bytes() == b"keep"
    shared.clear_share(host, "nas.local", None)


@pytest.mark.parametrize(
    "fault", ["unmounted", "wrong_source", "nested", "foreign", "identity", "running"]
)
def test_clear_refuses_unsafe_targets_before_any_delete(share, fault):
    shared, root, host, mounts = share
    identity = shared.inspect_share(host, "nas.local")
    if fault == "unmounted":
        mounts.clear()
    elif fault == "wrong_source":
        mounts[0]["source"] = "other:/quant"
    elif fault == "nested":
        mounts.append({"target": str(root / "objects/nested")})
    elif fault == "foreign":
        (root / "personal").mkdir()
    elif fault == "identity":
        (root / shared.MARKER).write_text("00000000-0000-0000-0000-000000000001")
    else:
        host["preflight"] = lambda: {"running-container"}
    with pytest.raises(ValueError):
        shared.clear_share(host, "nas.local", identity)
    assert (root / "objects/data.parquet").read_bytes() == b"data"


@pytest.mark.parametrize("failure", [None, "ready", "stopped"])
def test_all_purge_barrier_and_same_host_dedup(monkeypatch, failure):
    purge = module("purge_all")
    events = []
    processes = []

    class Process:
        def __init__(self, command, **kwargs):
            self.host = command[0]
            self.stdin = io.StringIO()
            processes.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self):
            return 0

    def ack(process, phase):
        events.append((process.host, phase))
        if process.host == "research" and phase == failure:
            raise ValueError("failed host")
        return None

    monkeypatch.setattr(purge.subprocess, "Popen", Process)
    monkeypatch.setattr(purge, "ack", ack)
    monkeypatch.setattr(purge, "send", lambda p, command: events.append((p.host, command)))
    settings = {
        k: {"host": v}
        for k, v in [
            ("nfs", "nas"),
            ("data_hub", "hub"),
            ("research", "research"),
            ("live", "live"),
        ]
    }
    configs = {
        app: {"host": h}
        for app, h in [
            ("database", "hub"),
            ("data-hub", "hub"),
            ("research", "research"),
            ("live", "live"),
        ]
    }
    args = SimpleNamespace(dry_run=False)

    def execute():
        return purge.run(args, configs, settings, ROOT, lambda c, *_: [c["host"]])

    if failure:
        with pytest.raises(ValueError):
            execute()
        assert not any(phase in {"clear", "purge"} for _, phase in events)
        if failure == "ready":
            assert not any(phase == "stop" for _, phase in events)
    else:
        assert execute() == 0
        clear = events.index(("hub", "clear"))
        assert all(events.index((h, "stopped")) < clear for h in ["hub", "research", "live"])
        assert all(events.index((h, "purge")) > clear for h in ["hub", "research", "live"])
    assert [p.host for p in processes] == ["hub", "research", "live"]
    assert all(p.stdin.closed for p in processes)


def test_object_is_required_and_all_rejects_ambiguous_options():
    for args in [["stop"], ["deploy", "all", "--env-file", "private.env"], ["purge-host", "all"]]:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/northstarctl.py"), *args],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2


def test_purge_real_remote_protocol_over_disposable_processes(tmp_path):
    purge = module("purge_all")
    market = tmp_path / "market"
    market.mkdir()
    (market / ".northstar-storage-id").write_text("76c6aae1-5059-47ba-8b4c-66302848db35")
    (market / "objects").mkdir()
    (market / "objects/data").write_text("old data")
    events = tmp_path / "events"
    remote = tmp_path / "remote.py"
    remote.write_text(
        (ROOT / "scripts/operations/purge_shared.py")
        .read_text()
        .replace('Path("/opt/northstar/files/market")', f"Path({str(market)!r})")
    )

    def transport(config, program, argument):
        request = json.loads(argument)
        request["host_program"] = f"""
import json
from pathlib import Path
EVENTS = Path({str(events)!r})
MARKET = Path({str(market)!r})
stopped = False

def record(phase):
    with EVENTS.open("a") as output:
        output.write({config["host"]!r} + ":" + phase + "\\n")

def preflight():
    return set() if stopped else {{"owned"}}

def locks(stack):
    record("locked")

def stop(selected):
    global stopped
    stopped = True
    record("stopped")

def run(*args):
    return json.dumps({{"filesystems": [{{
        "target": str(MARKET), "source": "localhost:/quant", "fstype": "nfs4"
    }}]}})

def purge(*, locks_held):
    assert locks_held and stopped
    assert not (MARKET / "objects").exists()
    record("purged")
"""
        return [sys.executable, "-u", str(remote), json.dumps(request)]

    settings = {
        "nfs": {"host": "localhost"},
        "data_hub": {"host": "hub"},
        "research": {"host": "research"},
    }
    configs = {
        app: {"host": host}
        for app, host in [("database", "hub"), ("data-hub", "hub"), ("research", "research")]
    }
    assert purge.run(SimpleNamespace(dry_run=False), configs, settings, ROOT, transport) == 0
    lines = events.read_text().splitlines()
    assert sorted(lines[:2]) == ["hub:locked", "research:locked"]
    assert sorted(lines[2:4]) == ["hub:stopped", "research:stopped"]
    assert sorted(lines[4:]) == ["hub:purged", "research:purged"]
    assert not list(market.iterdir())


def test_failed_follow_stops_other_log_processes():
    batch = module("all_apps")
    assert (
        batch.follow(
            {
                "waiting": [sys.executable, "-c", "import time; time.sleep(30)"],
                "failed": [sys.executable, "-c", "raise SystemExit(2)"],
            }
        )
        == 1
    )
