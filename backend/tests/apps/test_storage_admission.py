"""Missing directory identities must block deployments without recreating storage."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

from northstar_quant.data_management.storage_identity import initialize


@pytest.fixture
def admission(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "check_storage", Path(__file__).resolve().parents[3] / "scripts/operations/check_storage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    environment, volumes = {}, []
    for share in ("source", "market", "research", "backup"):
        root = tmp_path.resolve() / share
        root.mkdir()
        identity = str(uuid4())
        environment[f"NORTHSTAR_{share.upper()}_STORAGE_ID"] = identity
        initialize(root, identity)
        volumes.append(
            {"type": "bind", "source": str(root), "target": f"/var/lib/northstar/{share}"}
        )
    pgdata = tmp_path.resolve() / "pgdata"
    pgdata.mkdir()
    service = {"environment": environment, "volumes": volumes}
    config = {
        "services": {
            "storage-check": service,
            "initialize": service,
            "postgres": {"volumes": [{"source": str(pgdata)}]},
        }
    }
    return module, config, tmp_path.resolve()


def test_directories_need_no_storage_provider_configuration(admission):
    module, config, root = admission
    module.check_directories(config, "data_hub")
    module.check_directories(config, "database")
    missing = root / "source" / ".northstar-storage-id"
    missing.unlink()
    with pytest.raises(ValueError, match="not initialized"):
        module.check_directories(config, "data_hub")
    assert not missing.exists()


def test_first_install_accepts_empty_directories_but_redeploy_does_not(admission):
    module, config, root = admission
    import shutil

    for share in ("source", "market", "research", "backup"):
        shutil.rmtree(root / share)
        (root / share).mkdir()
    module.check_directories(config, "database")
    (root / "pgdata/PG_VERSION").write_text("17")
    with pytest.raises(ValueError, match="not initialized"):
        module.check_directories(config, "database")
    assert list((root / "source").iterdir()) == []


def test_admission_rejects_wrong_identity_overlapping_paths_and_unfinished_restore(admission):
    module, config, root = admission
    service = config["services"]["storage-check"]
    marker = root / "source/.northstar-storage-id"
    original = marker.read_text()
    marker.write_text(str(uuid4()) + "\n")
    with pytest.raises(ValueError, match="identity"):
        module.check_directories(config, "data_hub")
    marker.write_text(original)
    service["volumes"][1]["source"] = str(root / "source")
    with pytest.raises(ValueError, match="overlapping"):
        module.check_directories(config, "data_hub")
    service["volumes"][1]["source"] = str(root / "market")
    (root / "source/.restore-incomplete").touch()
    with pytest.raises(ValueError, match="restore"):
        module.check_directories(config, "data_hub")


def test_missing_and_symlink_directories_are_not_created_or_adopted(admission):
    module, _, root = admission
    missing = root / "missing"
    with pytest.raises(ValueError, match="existing"):
        module.require_directory(missing)
    assert not missing.exists()
    missing.symlink_to(root / "source")
    with pytest.raises(ValueError, match="symlinks"):
        module.require_directory(missing)


def test_private_postgres_directory_does_not_block_existing_storage(admission, monkeypatch):
    module, config, root = admission
    original = Path.iterdir

    def entries(path):
        if path == root / "pgdata":
            raise PermissionError("database owned directory")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", entries)
    module.check_directories(config, "database")
    (root / "source/.northstar-storage-id").unlink()
    with pytest.raises(ValueError, match="not initialized"):
        module.check_directories(config, "database")


def test_missing_runtime_bind_blocks_start_without_creating_replacement(admission):
    module, config, root = admission
    missing = root / "credentials"
    config["services"]["api"] = {
        "volumes": [{"type": "bind", "source": str(missing), "target": "/credentials"}]
    }
    with pytest.raises(ValueError, match="existing"):
        module.check_directories(config, "data_hub")
    with pytest.raises(ValueError, match="existing"):
        module.check_directories(config, "live")
    assert not missing.exists()
