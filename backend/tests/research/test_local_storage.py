"""SQLite preserves research identities, Paper concurrency and complete recovery."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from northstar_quant.apps.research.maintenance import backup, restore
from northstar_quant.data_management.storage_identity import initialize as identify
from northstar_quant.research.configurations import ConfigurationStore
from northstar_quant.research.factor_catalog import FactorCatalog
from northstar_quant.research.operations import ResearchOperations
from northstar_quant.research.paper import PaperStore
from northstar_quant.research.runs import RunStore
from northstar_quant.research.storage import initialize, open_store
from tests.test_paper import _study


def test_sqlite_research_paper_and_joint_restore(
    postgres_engine, clean_database, tmp_path, monkeypatch
):
    for name in ("MARKET", "RESEARCH"):
        root = tmp_path / name.lower()
        root.mkdir()
        identity = str(uuid4())
        identify(root, identity)
        monkeypatch.setenv(f"NORTHSTAR_{name}_DIR", str(root))
        monkeypatch.setenv(f"NORTHSTAR_{name}_STORAGE_ID", identity)
    library, dataset, config = _study(postgres_engine, tmp_path)
    engine = open_store(tmp_path / "research.sqlite3")
    initialize(engine)
    saved = ConfigurationStore(engine).save_configuration("固定研究", config)
    store = RunStore(engine)
    run_id = ResearchOperations(library, store).run(dataset.snapshot_id, config)
    factor = FactorCatalog(engine, library)
    binding = dict(config.strategy.factors)["momentum"]
    assert factor.calculate(factor.register(binding), dataset.snapshot_id)["status"] == "SUCCEEDED"
    paper = PaperStore(engine, library)
    identity = uuid4()
    paper.create(dataset.snapshot_id, saved["configuration_id"], request_id=identity)
    request = uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: paper.advance(identity, request_id=request), range(2)))
    assert results[0] == results[1]
    assert paper.get(identity)["cursor"] == 1
    with pytest.raises(DBAPIError, match="immutable"):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM research_runs"))
    interrupted = uuid4()
    killed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os,sys
from pathlib import Path
from uuid import UUID
from sqlalchemy import event
from northstar_quant.research.storage import open_store
from northstar_quant.research.paper import PaperStore
from northstar_quant.data_management.publications import PublishedDatasets
engine=open_store(Path(sys.argv[1]))
@event.listens_for(engine, 'after_cursor_execute')
def crash(conn,cursor,statement,parameters,context,many):
    if statement.startswith('UPDATE paper_sessions'):
        os._exit(95)
PaperStore(engine,PublishedDatasets(Path(sys.argv[2]))).advance(UUID(sys.argv[3]),request_id=UUID(sys.argv[4]))
""",
            str(tmp_path / "research.sqlite3"),
            str(library.publications.root),
            str(identity),
            str(interrupted),
        ],
        timeout=30,
    )
    assert killed.returncode == 95
    assert paper.get(identity)["cursor"] == 1
    committed = paper.advance(identity, request_id=interrupted)
    assert paper.advance(identity, request_id=interrupted) == committed
    assert paper.get(identity)["cursor"] == 2
    expected = store.get(run_id)
    target = tmp_path / "backup"
    assert backup(engine, target)["owner"] == "research"
    engine.dispose()
    restarted = open_store(tmp_path / "research.sqlite3")
    assert RunStore(restarted).get(run_id) == expected
    restarted.dispose()
    for name in ("MARKET", "RESEARCH"):
        monkeypatch.setenv(f"NORTHSTAR_{name}_DIR", str(tmp_path / ("restored-" + name)))
        monkeypatch.setenv(f"NORTHSTAR_{name}_STORAGE_ID", str(uuid4()))
    recovered = open_store(tmp_path / "recovered.sqlite3")
    assert restore(recovered, target)["status"] == "restored"
    assert RunStore(recovered).get(run_id) == expected
    recovered.dispose()


def test_queued_task_backup_retains_its_only_market_reference(
    postgres_engine, clean_database, tmp_path, monkeypatch
):
    from northstar_quant.data_management.publications import PublishedDatasets
    from northstar_quant.research.tasks.execution import execute
    from northstar_quant.research.tasks.store import TaskStore

    for name in ("MARKET", "RESEARCH"):
        root = tmp_path / name.lower()
        root.mkdir()
        identity = str(uuid4())
        identify(root, identity)
        monkeypatch.setenv(f"NORTHSTAR_{name}_DIR", str(root))
        monkeypatch.setenv(f"NORTHSTAR_{name}_STORAGE_ID", identity)
    library, dataset, config = _study(postgres_engine, tmp_path)
    engine = open_store(tmp_path / "queued.sqlite3")
    initialize(engine)
    task = TaskStore(engine).submit(
        uuid4(),
        dataset.snapshot_id,
        dataset.content_hash,
        config,
        len(dataset.bars),
        dataset.details.to_dict(),
    )
    destination = tmp_path / "queued-backup"
    backup(engine, destination)
    engine.dispose()
    for name in ("MARKET", "RESEARCH"):
        monkeypatch.setenv(f"NORTHSTAR_{name}_DIR", str(tmp_path / ("restored-" + name)))
        monkeypatch.setenv(f"NORTHSTAR_{name}_STORAGE_ID", str(uuid4()))
    restored = open_store(tmp_path / "restored.sqlite3")
    restore(restored, destination)
    tasks = TaskStore(restored)
    assert tasks.get(task["task_id"])["status"] == "QUEUED"
    tasks.claim()
    execute(tasks, PublishedDatasets.from_environment(), task["task_id"])
    assert tasks.get(task["task_id"])["status"] == "SUCCEEDED"
    restored.dispose()
