"""Destructive cleanup is exercised only against disposable paths and Docker doubles."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def host(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "purge_host", ROOT / "scripts/operations/purge_host.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path / "northstar")
    monkeypatch.setattr(module, "UNITS", tmp_path / "units")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    module.ROOT.mkdir()
    module.UNITS.mkdir()
    (module.ROOT / "fact.sqlite").write_bytes(b"local facts")
    external = tmp_path / "nas"
    external.mkdir()
    (external / "published.parquet").write_bytes(b"published facts")
    (module.ROOT / "external-link").symlink_to(external, target_is_directory=True)
    state = {
        "mounted": [],
        "containers": {"ours": "northstar-live", "other": "other-app"},
        "calls": [],
        "fail_unmount": False,
    }

    def run(*args):
        state["calls"].append(args)
        if args[:2] == ("findmnt", "--json"):
            return json.dumps({"filesystems": [{"target": str(p)} for p in state["mounted"]]})
        if args[:3] == ("docker", "ps", "-aq"):
            if "--filter" in args:
                project = args[-1].split("=", 2)[-1]
                return "\n".join(k for k, v in state["containers"].items() if v == project)
            return "\n".join(state["containers"])
        if args[:2] == ("docker", "inspect"):
            return json.dumps(state.get("foreign_mounts", []))
        if args[:2] == ("docker", "rm"):
            for k in args[2:]:
                state["containers"].pop(k)
        if args[0] == "umount":
            if state["fail_unmount"]:
                raise subprocess.CalledProcessError(1, args)
            state["mounted"].clear()
        if args[:4] == ("docker", "image", "ls", "--format"):
            return "northstar-live-backend:abc\npostgres:17-alpine\nother:latest"
        return ""

    monkeypatch.setattr(module, "run", run)
    return module, state, external


def test_purge_is_idempotent_and_preserves_external_files_and_other_containers(host):
    module, state, external = host
    module.purge()
    module.purge()
    assert not module.ROOT.exists()
    assert (external / "published.parquet").read_bytes() == b"published facts"
    assert state["containers"] == {"other": "other-app"}
    assert ("docker", "stop", "--time", "60", "ours") in state["calls"]
    removed = [c for c in state["calls"] if c[:3] == ("docker", "image", "rm")]
    assert all(c[-1] == "northstar-live-backend:abc" for c in removed)
    assert not any("prune" in c or "ssh" in c for c in state["calls"])


@pytest.mark.parametrize("failed", [False, True])
def test_unmount_precedes_delete_and_failed_unmount_preserves_data(host, failed):
    module, state, external = host
    market = module.ROOT / "files/market"
    state["mounted"] = [market]
    state["fail_unmount"] = failed
    dropin = module.UNITS / "docker.service.d/northstar-market.conf"
    dropin.parent.mkdir()
    dropin.write_text("Requires=opt-northstar-files-market.mount")
    if failed:
        with pytest.raises(subprocess.CalledProcessError):
            module.purge()
        assert (module.ROOT / "fact.sqlite").read_bytes() == b"local facts"
    else:
        module.purge()
        assert not module.ROOT.exists()
    assert not dropin.exists()
    calls = state["calls"]
    assert calls.index(("systemctl", "daemon-reload")) < calls.index(("umount", str(market)))
    assert (external / "published.parquet").exists()


@pytest.mark.parametrize("source", ["child", "ancestor"])
def test_foreign_container_using_data_prevents_any_stop_or_delete(host, source):
    module, state, _ = host
    path = module.ROOT / "state" if source == "child" else module.ROOT.parent
    state["foreign_mounts"] = [{"Type": "bind", "Source": str(path)}]
    with pytest.raises(ValueError, match="其他容器"):
        module.purge()
    assert module.ROOT.exists()
    assert not any(c[:2] == ("docker", "stop") for c in state["calls"])


def test_nested_mount_prevents_cleanup_before_stopping(host):
    module, state, _ = host
    state["mounted"] = [module.ROOT / "files/market/nested"]
    with pytest.raises(ValueError, match="其他挂载"):
        module.purge()
    assert not any(c[:2] == ("docker", "stop") for c in state["calls"])
    assert module.ROOT.exists()


def test_busy_deployment_is_not_interrupted(host):
    import fcntl

    module, state, _ = host
    app = module.ROOT / "apps/live"
    app.mkdir(parents=True)
    with (app / ".deployment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            module.purge()
    assert not any(c[:2] == ("docker", "stop") for c in state["calls"])


def test_symlink_root_is_never_followed(host):
    module, state, external = host
    module.ROOT = module.ROOT / "external-link"
    with pytest.raises(ValueError, match="符号链接"):
        module.purge()
    assert (external / "published.parquet").exists()
    assert not state["calls"]


def test_shared_resource_removal_failure_preserves_files_for_retry(host, monkeypatch):
    module, state, _ = host
    original = module.run

    def fail(*args):
        if args[:3] == ("docker", "image", "rm"):
            raise subprocess.CalledProcessError(1, args)
        return original(*args)

    monkeypatch.setattr(module, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        module.purge()
    assert (module.ROOT / "fact.sqlite").exists()
    assert state["containers"] == {"other": "other-app"}
    monkeypatch.setattr(module, "run", original)
    module.purge()
    assert not module.ROOT.exists()


def test_external_volume_plugin_is_not_asked_to_delete_remote_data(host, monkeypatch):
    module, state, _ = host
    monkeypatch.setattr(
        module, "owned", lambda kind: {"remote-volume"} if kind == "volume" else set()
    )
    with pytest.raises(ValueError, match="外部存储驱动"):
        module.purge()
    assert ("docker", "volume", "rm", "remote-volume") not in state["calls"]
    assert module.ROOT.exists()


def test_firewall_removes_only_owned_hooks_and_chains(host, monkeypatch):
    module, state, _ = host
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/sbin/iptables")
    original = module.run

    def rules(*args):
        if args == ("iptables", "-w", "-S"):
            return (
                "-N NS-LIVE-WEB\n-N OTHER\n"
                "-A INPUT -p tcp --dport 18080 -j NS-LIVE-WEB\n"
                "-A INPUT -p tcp --dport 9000 -j OTHER"
            )
        return original(*args)

    monkeypatch.setattr(module, "run", rules)
    module.firewall()
    assert all("OTHER" not in call for call in state["calls"])
    assert ("iptables", "-w", "-X", "NS-LIVE-WEB") in state["calls"]
