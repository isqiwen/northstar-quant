"""Automatic binding preserves identities across retries and local disk fallback."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

from northstar_quant.data_management.storage_identity import initialize


@pytest.fixture
def binding(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "storage_bindings",
        Path(__file__).resolve().parents[3] / "scripts/operations/storage_bindings.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    volumes = []
    for share in ("source", "market", "research", "backup"):
        root = tmp_path / share
        root.mkdir()
        volumes.append({"type": "bind", "source": str(root), "target": "/storage/" + share})
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    service = {"volumes": volumes}
    config = {
        "services": {
            "initialize": service,
            "maintenance": service,
            "postgres": {"volumes": [{"source": str(pgdata)}]},
        }
    }
    return module, config, tmp_path / "state/bindings/storage.json"


def test_generated_identities_survive_interruption_and_empty_local_fallback(binding):
    module, config, state = binding
    initial, pending = module.bind(config, "database", state)
    assert pending
    assert module.bind(config, "database", state)[0] == initial
    roots = module.directory_map(config, "database")
    for share, root in roots.items():
        initialize(root, initial[f"NORTHSTAR_{share}_STORAGE_ID"])
    module.bind(config, "database", state, complete=True)
    assert module.bind(config, "data_hub", state) == (initial, False)
    previous = roots["SOURCE"].with_name("previous-source")
    roots["SOURCE"].rename(previous)
    assert module.bind(config, "database", state) == (initial, False)
    assert (roots["SOURCE"] / ".northstar-storage-id").read_text().strip() == initial[
        "NORTHSTAR_SOURCE_STORAGE_ID"
    ]


def test_research_discovers_shared_identity_and_rejects_directory_substitution(binding):
    module, config, state = binding
    roots = module.directory_map(config, "research")
    for root in roots.values():
        initialize(root, str(uuid4()))
    initial, pending = module.bind(config, "research", state)
    assert not pending
    assert module.bind(config, "research", state)[0] == initial
    (roots["MARKET"] / ".northstar-storage-id").write_text(str(uuid4()) + "\n")
    with pytest.raises(ValueError, match="identity"):
        module.bind(config, "research", state)


def test_lost_binding_state_is_not_recreated_for_an_existing_database(binding):
    module, config, state = binding
    (Path(config["services"]["postgres"]["volumes"][0]["source"]) / "PG_VERSION").write_text("17")
    with pytest.raises(ValueError, match="persisted storage bindings"):
        module.bind(config, "database", state)
    assert not state.exists()


def test_unidentified_nonempty_storage_is_never_adopted(binding):
    module, config, state = binding
    root = module.directory_map(config, "database")["SOURCE"]
    (root / "retained").write_bytes(b"evidence")
    with pytest.raises(ValueError, match="not initialized"):
        module.bind(config, "database", state)
    assert (root / "retained").read_bytes() == b"evidence"
    assert not state.exists()
