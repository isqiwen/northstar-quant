"""Held-out admission, immutable selection and restart using real owner stores."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.research.artifacts import ResearchUsages
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.experiments import Experiments
from northstar_quant.research.storage import initialize, open_store
from northstar_quant.research.tasks.execution import execute
from northstar_quant.research.tasks.store import TaskStore
from northstar_quant.strategies.configuration import StrategyConfig
from tests.data_management.test_research import _csv, _receive, _spec


def _inputs(engine, root):
    library = DataLibrary(engine, SourceFiles(root / "sources"))
    snapshots = []
    for offset in range(3):
        spec = _spec()
        shift = timedelta(days=offset)
        spec = replace(
            spec,
            trading_day=spec.trading_day + shift,
            session_open=spec.session_open + shift,
            session_close=spec.session_close + shift,
        )
        path = _csv(root / f"bars-{offset}.csv")
        path.write_text(path.read_text().replace("2026-01-07", spec.trading_day.isoformat()))
        snapshots.append(_receive(library, path, spec).snapshot_id)
    configs = [
        ResearchConfig(strategy=StrategyConfig.create(supplied={"threshold": v}))
        for v in ("0.001", "0.1")
    ]
    return library, tuple(snapshots), configs


def test_selection_is_durable_before_test_and_recovery_does_not_search_test(
    postgres_engine,
    clean_database,
    tmp_path,
):
    library, snapshots, configs = _inputs(postgres_engine, tmp_path)
    path = tmp_path / "research.sqlite3"
    engine = open_store(path)
    initialize(engine)
    experiments = Experiments(engine)
    identity = uuid4()
    created = experiments.submit(identity, "fixed momentum thresholds", snapshots, configs, library)
    assert experiments.pending() == (str(identity),)
    assert not TaskStore(engine).list()  # admission is persisted before scheduling
    assert len(ResearchUsages(engine).list(snapshots)) == 3  # includes unexecuted test input
    assert (
        experiments.submit(identity, "fixed momentum thresholds", snapshots, configs, library)
        == created
    )
    with pytest.raises(ValueError, match="different"):
        experiments.submit(identity, "new hypothesis", snapshots, configs, library)
    experiments.advance(str(identity))
    tasks = TaskStore(engine)
    assert len(tasks.list()) == 4
    assert {t["snapshot_id"] for t in tasks.list()} == {str(s) for s in snapshots[:2]}
    while (task := tasks.claim()) is not None:
        execute(tasks, library, task["task_id"])
        assert tasks.get(task["task_id"])["status"] == "SUCCEEDED"
    experiments.advance(str(identity))
    selected = experiments.get(str(identity))
    assert selected["selection"]["winner"]
    assert len(tasks.list()) == 4  # test cannot precede the committed decision
    engine.dispose()
    engine = open_store(path)
    experiments = Experiments(engine)
    experiments.advance(str(identity))
    tasks = TaskStore(engine)
    assert len(tasks.list()) == 5
    task = tasks.claim()
    assert task["snapshot_id"] == str(snapshots[2])
    execute(tasks, library, task["task_id"])
    experiments.advance(str(identity))
    final = experiments.get(str(identity))
    assert final["status"] == "SUCCEEDED"
    assert experiments.pending() == ()
    assert final["selection"] == selected["selection"]
    assert sum(t["phase"] == "test" for t in final["trials"]) == 1
    with engine.begin() as c, pytest.raises(DBAPIError, match="immutable"):
        c.execute(text("DELETE FROM research_experiment_selections"))
    with engine.begin() as c, pytest.raises(DBAPIError, match="immutable"):
        c.execute(text("UPDATE research_experiments SET plan_id='bad'"))
    engine.dispose()


def test_overlaps_and_different_costs_rejected_and_failed_candidates_retained(
    postgres_engine,
    clean_database,
    tmp_path,
):
    library, snapshots, configs = _inputs(postgres_engine, tmp_path)
    engine = open_store(tmp_path / "research.sqlite3")
    initialize(engine)
    experiments = Experiments(engine)
    with pytest.raises(ValueError, match="disjoint"):
        experiments.submit(uuid4(), "unordered", tuple(reversed(snapshots)), configs, library)
    with pytest.raises(ValueError, match="distinct fixed"):
        experiments.submit(uuid4(), "reuse", (snapshots[0],) * 3, configs, library)
    with pytest.raises(ValueError, match="identical"):
        experiments.submit(
            uuid4(),
            "cost changed",
            snapshots,
            [configs[0], replace(configs[1], risk=replace(configs[1].risk, max_lots=1))],
            library,
        )
    identity = str(uuid4())
    experiments.submit(UUID(identity), "retain failures", snapshots, configs, library)
    experiments.advance(identity)
    tasks = TaskStore(engine)
    for task in tasks.list():
        tasks.control(task["task_id"], "cancel")
    experiments.advance(identity)
    state = experiments.get(identity)
    assert state["status"] == "FAILED"
    assert state["selection"]["winner"] is None
    assert len(state["selection"]["scores"]) == 2
    assert len(tasks.list()) == 4  # no fallback test trial after all candidates fail
    engine.dispose()


def test_experiment_api_uses_owned_binary_protocol_and_login(
    postgres_engine,
    clean_database,
    tmp_path,
):
    from northstar_quant.apps.research.application import create_app
    from tests.apps.browser import ProtocolClient, _browser_session

    library, snapshots, configs = _inputs(postgres_engine, tmp_path)
    engine = open_store(tmp_path / "api.sqlite3")
    initialize(engine)
    with ProtocolClient(create_app(engine, library), base_url="http://localhost") as client:
        body = {
            "request_id": str(uuid4()),
            "hypothesis": "fixed grid",
            "train_snapshot": str(snapshots[0]),
            "validation_snapshot": str(snapshots[1]),
            "test_snapshot": str(snapshots[2]),
            "configurations": [c.to_dict() for c in configs],
        }
        assert client.post("/api/experiments", json=body).status_code in {401, 403}
        _browser_session(client)
        response = client.post("/api/experiments", json=body)
        assert response.status_code == 202, response.text
        record = response.json()
        assert record["selection"] is None
        assert len(record["trials"]) == 4
        assert client.get(f"/api/experiments/{body['request_id']}").json() == record
        assert client.get("/api/experiments").json() == [record]
    engine.dispose()
