"""Successful deployment removes only unused owned versions, never evidence or active mounts."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def cleanup(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/operations/cleanup_versions.py"
    spec = importlib.util.spec_from_file_location("cleanup_versions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "apps/data-hub"
    old, new = "a" * 40, "b" * 40
    for revision in (old, new):
        directory = root / "releases" / revision
        directory.mkdir(parents=True)
        (directory / "source.py").write_text("application")
    (root / "current").symlink_to(root / "releases" / new)
    (root / "successful-revision").write_text(new)
    configuration = root.parent.parent / "config/data-hub.env"
    configuration.parent.mkdir()
    configuration.write_bytes(b"private-test-configuration")
    (root / "deployment.json").write_text(
        json.dumps(
            {
                "phase": "verified",
                "revision": new,
                "configuration_sha256": hashlib.sha256(configuration.read_bytes()).hexdigest(),
            }
        )
    )
    monkeypatch.setattr(module, "output", lambda *args: "")
    return module, root, old, new


def test_success_removes_only_own_old_sources_and_unused_image_tags(cleanup, monkeypatch):
    module, root, old, new = cleanup
    evidence = root.parent.parent / "files/market/snapshot"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("immutable")
    other = root / "releases/notes"
    other.mkdir()
    removed = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kw: removed.append(args))

    def output(*args):
        if args[:3] == ("docker", "image", "ls"):
            return "\n".join(
                (
                    f"northstar-data-hub-backend:{old}",
                    f"northstar-research-backend:{old}",
                    f"northstar-data-hub-backend:{new}",
                    "postgres:17-alpine",
                )
            )
        return ""

    monkeypatch.setattr(module, "output", output)
    module.cleanup("data-hub", root, new)
    assert not (root / "releases" / old).exists()
    assert (root / "current/source.py").read_text() == "application"
    assert evidence.read_text() == "immutable" and other.is_dir()
    assert removed == [["docker", "image", "rm", "--no-prune", f"northstar-data-hub-backend:{old}"]]


def test_failed_deployment_never_removes_previous_release(cleanup):
    module, root, old, new = cleanup
    (root / "successful-revision").write_text(old)
    with pytest.raises(ValueError, match="not verified"):
        module.cleanup("data-hub", root, new)
    assert (root / "releases" / old).is_dir()


def test_active_container_references_are_preserved(cleanup, monkeypatch):
    module, root, old, new = cleanup

    def output(*args):
        if args == ("docker", "ps", "-aq"):
            return "container"
        if args[:2] == ("docker", "inspect"):
            return json.dumps(
                [{"Type": "bind", "Source": str(root / "releases" / old / "source.py")}]
            )
        if args[:3] == ("docker", "image", "ls"):
            return f"northstar-data-hub-backend:{old}"
        return f"northstar-data-hub-backend:{old}"

    monkeypatch.setattr(module, "output", output)
    with pytest.raises(ValueError, match="仍被容器引用"):
        module.cleanup("data-hub", root, new)
    assert (root / "releases" / old / "source.py").is_file()


def test_old_release_symlink_never_deletes_target(cleanup):
    module, root, old, new = cleanup
    target = root / "preserve"
    target.mkdir()
    (root / "releases" / ("c" * 40)).symlink_to(target)
    with pytest.raises(ValueError, match="Unexpected"):
        module.cleanup("data-hub", root, new)
    assert target.is_dir()


def test_changed_configuration_cannot_use_old_success_to_delete_versions(cleanup):
    module, root, old, new = cleanup
    (root.parent.parent / "config/data-hub.env").write_text("changed")
    with pytest.raises(ValueError, match="configuration is not verified"):
        module.cleanup("data-hub", root, new)
    assert (root / "releases" / old).is_dir()
