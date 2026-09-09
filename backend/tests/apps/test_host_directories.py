"""Host setup preserves storage, private configuration and PostgreSQL ownership."""

import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def host(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[3] / "scripts/operations/host_directories.py"
    spec = importlib.util.spec_from_file_location("host_directories", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    for share in ("source", "market", "research", "backup"):
        (tmp_path / "files" / share).mkdir(parents=True)
    return module, tmp_path, {"app": "database", "uid": os.getuid(), "gid": os.getgid()}


def test_preparation_preserves_pgdata_and_refuses_missing_persistent_directory(host):
    module, root, request = host
    module.prepare(request)
    pgdata = root / "state/data-hub/postgresql"
    pgdata.chmod(0o750)
    (pgdata / "PG_VERSION").write_text("17")
    module.prepare(request)
    assert pgdata.stat().st_mode & 0o777 == 0o750
    assert (pgdata / "PG_VERSION").read_text() == "17"
    (pgdata / "PG_VERSION").unlink()
    pgdata.rmdir()
    with pytest.raises(ValueError, match="持久目录丢失"):
        module.prepare(request)
    assert not pgdata.exists()


def test_missing_share_uses_local_disk_and_existing_files_are_preserved(host):
    module, root, request = host
    share = root / "files/market"
    share.rmdir()
    module.prepare(request)
    assert share.is_dir()
    assert share.stat().st_mode & 0o777 == 0o750
    (share / "data").write_bytes(b"retained")
    share.chmod(0o755)
    module.prepare(request)
    assert (share / "data").read_bytes() == b"retained"
    assert share.stat().st_mode & 0o777 == 0o755


def test_symlinked_share_is_not_modified(host):
    module, root, request = host
    share = root / "files/market"
    share.rmdir()
    share.symlink_to(root / "files/research")
    with pytest.raises(ValueError, match="符号链接"):
        module.prepare(request)
    assert not (root / "apps").exists()


def test_credentials_are_private_without_changing_existing_content(host):
    module, root, request = host
    request["app"] = "live"
    (root / "config").mkdir()
    private = root / "config/live.env"
    private.write_text("private-content")
    private.chmod(0o644)
    module.prepare(request)
    assert private.read_text() == "private-content"
    assert private.stat().st_mode & 0o777 == 0o600
    assert (root / "credentials/live").stat().st_mode & 0o777 == 0o700
    assert (root / "state/live/sources").is_dir()
