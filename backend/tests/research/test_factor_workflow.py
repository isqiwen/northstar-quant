"""Persistent factor/configuration/result/candidate behavior and independent Live receipt."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from northstar_quant.apps.research import create_app
from northstar_quant.factors.evaluation import Binding
from northstar_quant.live.materials import StrategyMaterials
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.configurations import ConfigurationStore
from northstar_quant.research.factor_catalog import FactorCatalog
from northstar_quant.research.operations import ResearchOperations
from northstar_quant.research.runs import RunStore
from northstar_quant.research.strategy_management import StrategyVersions
from northstar_quant.strategies.artifacts import verify_candidate
from northstar_quant.strategies.configuration import StrategyConfig
from tests.apps.browser import ProtocolClient as TestClient
from tests.apps.browser import _browser_session
from tests.test_paper import _study


def test_factor_revisions_causal_results_references_and_candidate_survive_reopen(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library, dataset, config = _study(postgres_engine, tmp_path)
    catalog = FactorCatalog(postgres_engine, library)
    binding = dict(config.strategy.factors)["momentum"]
    identity = catalog.register(binding)
    assert catalog.register(binding) == identity
    calculated = catalog.calculate(identity, dataset.snapshot_id)
    assert calculated["status"] == "SUCCEEDED"
    assert calculated["result"]["values"][0]["status"] == "WARMING_UP"
    assert calculated["result"]["values"][1]["status"] == "READY"
    changed = Binding.create("trend.return", {"window_bars": 2})
    changed_id = catalog.register(changed)
    assert changed_id != identity
    other = catalog.calculate(changed_id, dataset.snapshot_id)
    assert other["result"]["values"][1]["status"] == "WARMING_UP"
    catalog.annotate(identity, "人工说明，不改变公式或结果")
    assert catalog.get(UUID(calculated["attempt_id"])) == calculated
    saved = ConfigurationStore(postgres_engine).save_configuration("明确引用", config)
    runs = RunStore(postgres_engine)
    operations = ResearchOperations(library, runs)
    run_id = operations.run(dataset.snapshot_id, config)
    for point, factor in zip(
        runs.get(run_id)["result"]["equity_curve"], calculated["result"]["values"], strict=True
    ):
        assert point["strategy"]["factors"]["momentum"]["value"] == factor["value"]
    second_config = replace(config, strategy=StrategyConfig.create(factors={"momentum": changed}))
    second_run = operations.run(dataset.snapshot_id, second_config)
    assert len(operations.compare([run_id, second_run])) == 2
    versions = StrategyVersions(postgres_engine)
    version = versions.register("动量候选", saved["configuration_id"], [run_id])
    candidate = versions.publish(version)
    assert versions.publish(version) == candidate
    assert verify_candidate(candidate) == candidate
    damaged = deepcopy(candidate)
    damaged["document"]["evidence"][0]["result"]["summary"]["ending_cash"] = "10000000"
    with pytest.raises(ValueError, match="integrity"):
        verify_candidate(damaged)
    reopened = FactorCatalog(postgres_engine, library)
    assert reopened.get(UUID(calculated["attempt_id"])) == calculated
    assert StrategyVersions(postgres_engine).get(version)["document"] == candidate["document"]
    assert StrategyMaterials(postgres_engine).list() == []
    if not candidate["production_eligible"]:
        with pytest.raises(ValueError, match="clean candidate"):
            StrategyMaterials(postgres_engine).accept(candidate)
    with pytest.raises(ValueError, match="exact"):
        versions.register("wrong evidence", saved["configuration_id"], [second_run])
    for sql in (
        "UPDATE factor_revisions SET binding = '{}'",
        "DELETE FROM factor_runs",
        "UPDATE strategy_candidates SET document = '{}'",
        "DELETE FROM strategy_versions",
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            with postgres_engine.begin() as connection:
                connection.execute(text(sql))

    # Use an uncached binding: a clean build may reuse the successful calculation
    # above without invoking the evaluator, even after its failure is injected.
    failing_id = catalog.register(Binding.create("trend.return", {"window_bars": 3}))

    # Terminal failed attempts stay visible; they expose no result.
    def broken(*args, **kwargs):
        raise ValueError("bounded calculation failed")

    monkeypatch.setattr("northstar_quant.research.factor_catalog.evaluate", broken)
    failed = catalog.calculate(failing_id, dataset.snapshot_id)
    assert failed["status"] == "FAILED" and failed["result"] is None
    with pytest.raises(LookupError):
        operations.run(uuid4(), config)
    assert any(item["status"] == "FAILED" for item in runs.attempts())


def test_http_catalog_and_exact_parameter_versions_use_same_business_operations(
    postgres_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    library, dataset, _ = _study(postgres_engine, tmp_path)
    with TestClient(create_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        assert len(client.get("/api/catalog").json()["strategies"]) == 2
        payload = {"factor_id": "range.position", "parameters": {"window_bars": 2}}
        assert client.post("/api/factor-revisions", json=payload).status_code == 403
        _browser_session(client)
        revision = client.post("/api/factor-revisions", json=payload).json()["revision_id"]
        calculated = client.post(
            "/api/factor-runs",
            json={"revision_id": revision, "snapshot_id": str(dataset.snapshot_id)},
        )
        assert calculated.status_code == 201, calculated.text
        assert calculated.json()["status"] == "SUCCEEDED"
        config = ResearchConfig(
            strategy=StrategyConfig.create(
                "mean_reversion.range",
                factors={"position": Binding.create("range.position", {"window_bars": 2})},
            )
        )
        saved = client.post(
            "/api/configurations", json={"name": "range", "config": config.to_dict()}
        ).json()
        submitted = client.post(
            "/api/runs", json={"snapshot_id": str(dataset.snapshot_id), "config": config.to_dict()}
        ).json()
        version = client.post(
            "/api/strategy-versions",
            json={
                "name": "range",
                "configuration_id": saved["configuration_id"],
                "run_ids": [submitted["run_id"]],
            },
        )
        assert version.status_code == 201, version.text
        candidate = client.post(
            f"/api/strategy-versions/{version.json()['version_id']}/publish", json={}
        )
        assert candidate.status_code == 200, candidate.text
        assert (
            verify_candidate(candidate.json())["candidate_id"] == candidate.json()["candidate_id"]
        )
        assert client.get(f"/api/runs/{submitted['run_id']}").status_code == 200


def test_clean_material_is_received_locally_without_research_or_execution_authority(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This is synthetic build provenance for the protocol acceptance test, never a real release.
    clean = "a" * 40
    for module in (
        "northstar_quant.factors.evaluation",
        "northstar_quant.strategies.configuration",
        "northstar_quant.strategies.evaluation",
        "northstar_quant.research.runs",
        "northstar_quant.research.strategy_management",
        "northstar_quant.strategies.artifacts",
    ):
        monkeypatch.setattr(module + ".code_revision", lambda: clean)
    library, dataset, config = _study(postgres_engine, tmp_path)
    saved = ConfigurationStore(postgres_engine).save_configuration("synthetic build", config)
    run_id = ResearchOperations(library, RunStore(postgres_engine)).run(dataset.snapshot_id, config)
    versions = StrategyVersions(postgres_engine)
    candidate = versions.publish(
        versions.register("synthetic build", saved["configuration_id"], [run_id])
    )
    assert candidate["production_eligible"] is True
    materials = StrategyMaterials(postgres_engine)
    received = materials.accept(candidate)
    assert received["execution_authorized"] is False and received["status"] == "RECEIVED"
    assert materials.accept(candidate) == received
    # Reading the received copy needs neither DataLibrary nor any Research table.
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE strategy_candidates, strategy_versions, research_runs, "
                "paper_configurations CASCADE"
            )
        )
    assert materials.list()[0]["document"] == candidate
    monkeypatch.setattr("northstar_quant.strategies.artifacts.code_revision", lambda: "b" * 40)
    with pytest.raises(ValueError, match="installed Git"):
        materials.accept(candidate)
