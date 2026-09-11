"""Train-only labels, fixed learned strategy reuse and durable fit recovery."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from northstar_quant.data_management.research import ResearchDataset
from northstar_quant.market_data import Market, MarketBar
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.learning import LearningRecipe, fit, training_rows
from northstar_quant.strategies.configuration import StrategyConfig
from northstar_quant.strategies.runtime import StrategyRuntime


def sample():
    market = Market(uuid4(), "RB2610", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10), 60)
    start = datetime(2026, 9, 1, 1, tzinfo=UTC)
    bars = tuple(
        MarketBar(
            uuid4(),
            start + timedelta(minutes=i),
            start + timedelta(minutes=i + 1),
            start + timedelta(minutes=i + 1),
            start.date(),
            Decimal(3100 + (i * i + 7 * i) % 41),
            Decimal(100),
        )
        for i in range(60)
    )
    return ResearchDataset(uuid4(), "b" * 64, market, bars)


def test_vectorized_features_are_past_only_and_labels_stop_at_training_boundary():
    dataset = sample()
    recipe = LearningRecipe(fast_bars=1, slow_bars=3, horizon_bars=2)
    rows = training_rows(dataset, recipe)
    assert len(rows) == 55
    assert rows[-1][-1] == len(dataset.bars) - 1
    for fast, slow, label, index, end in rows:
        assert fast == pytest.approx(
            float(dataset.bars[index].close / dataset.bars[index - 1].close - 1)
        )
        assert slow == pytest.approx(
            float(dataset.bars[index].close / dataset.bars[index - 3].close - 1)
        )
        assert label == pytest.approx(
            float(dataset.bars[end].close / dataset.bars[index].close - 1)
        )
        assert end > index
    changed = replace(
        dataset, bars=dataset.bars[:-1] + (replace(dataset.bars[-1], close=Decimal(9000)),)
    )
    altered = training_rows(changed, recipe)
    assert [r[:2] for r in altered] == [r[:2] for r in rows]
    assert altered[:-1] == rows[:-1]  # only the final forward label reaches the changed bar


def test_fitted_coefficients_are_fixed_and_use_shared_strategy_decisions():
    dataset = sample()
    recipe = LearningRecipe(fast_bars=1, slow_bars=3)
    trained = fit(dataset, recipe, ResearchConfig())
    assert trained == fit(dataset, recipe, ResearchConfig())
    assert trained["last_label_observation"] == str(dataset.bars[-1].observation_id)
    assert len(trained["candidates"]) == 3
    for config in trained["candidates"].values():
        strategy = StrategyConfig.from_dict(config["strategy"])
        runtime = StrategyRuntime(strategy, dataset.market.contract_id)
        for bar in dataset.bars:
            step = runtime.advance(bar)
        assert step.intent is not None
        assert abs(step.intent.target_fraction) <= Decimal("0.5")
        assert all(factor.available_at <= step.intent.generated_at for _, factor in step.factors)
    with pytest.raises(ValueError, match="variation"):
        fit(
            replace(dataset, bars=tuple(replace(b, close=Decimal(3100)) for b in dataset.bars)),
            recipe,
            ResearchConfig(),
        )


def test_train_artifact_precedes_jobs_and_restart_never_refits_holdout(
    postgres_engine,
    clean_database,
    tmp_path,
):
    from northstar_quant.data_management.files import SourceFiles
    from northstar_quant.data_management.library import DataLibrary
    from northstar_quant.research.experiments import Experiments
    from northstar_quant.research.storage import initialize, open_store
    from northstar_quant.research.tasks.execution import execute
    from northstar_quant.research.tasks.store import TaskStore
    from tests.data_management.test_research import _receive, _spec

    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    snapshots = []
    for day in range(3):
        spec = _spec()
        start = spec.session_open + timedelta(days=day)
        spec = replace(
            spec,
            session_open=start,
            session_close=start + timedelta(minutes=40),
            trading_day=start.date(),
        )
        records = "event_time,available_at,source_record_id,open,high,low,close,volume\n"
        for i in range(40):
            price = 3100 + (i * i + 7 * i) % 41
            at = start + timedelta(minutes=i)
            stamp = at.isoformat().replace("+00:00", "Z")
            completed = (at + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
            records += f"{stamp},{completed},r{i},{price},{price + 1},{price - 1},{price},100\n"
        path = tmp_path / f"day{day}.csv"
        path.write_text(records)
        snapshots.append(_receive(library, path, spec).snapshot_id)
    path = tmp_path / "research.sqlite3"
    engine = open_store(path)
    initialize(engine)
    experiments = Experiments(engine)
    identity = str(uuid4())
    recipe = LearningRecipe(fast_bars=1, slow_bars=3, penalties=("0.001", "0.1"))
    created = experiments.submit(
        UUID(identity), "train only", tuple(snapshots), [ResearchConfig()], library, recipe
    )
    assert created["status"] == "FITTING" and created["fitted"] is None
    loaded = []

    class OnlyTrain:
        def load_dataset(self, snapshot):
            assert snapshot == snapshots[0]
            loaded.append(snapshot)
            return library.load_dataset(snapshot)

    assert experiments.advance(identity, OnlyTrain()) is True
    fitted = experiments.get(identity)["fitted"]
    assert fitted["status"] == "SUCCEEDED", fitted
    assert not TaskStore(engine).list()
    engine.dispose()
    engine = open_store(path)
    experiments = Experiments(engine)
    experiments.advance(identity, OnlyTrain())
    assert loaded == [snapshots[0]]
    store = TaskStore(engine)
    assert len(store.list()) == 4
    while (task := store.claim()) is not None:
        execute(store, library, task["task_id"])
        assert store.get(task["task_id"])["status"] == "SUCCEEDED"
    experiments.advance(identity, OnlyTrain())
    assert experiments.get(identity)["selection"]["winner"]
    assert len(store.list()) == 4
    experiments.advance(identity, OnlyTrain())
    assert len(store.list()) == 5
    task = store.claim()
    execute(store, library, task["task_id"])
    assert experiments.get(identity)["status"] == "SUCCEEDED"
    assert experiments.get(identity)["fitted"] == fitted
    assert loaded == [snapshots[0]]
    assert not experiments.pending()

    from northstar_quant.apps.research.application import create_app
    from tests.apps.browser import ProtocolClient, login_response

    with ProtocolClient(create_app(engine, library), base_url="http://127.0.0.1") as client:
        csrf = login_response(client).json()["csrf"]
        client.headers.update({"x-northstar-csrf": csrf, "origin": "http://127.0.0.1"})
        submitted = client.post(
            "/api/experiments/learn",
            json={
                "request_id": str(uuid4()),
                "hypothesis": "API fixed recipe",
                "train_snapshot": str(snapshots[0]),
                "validation_snapshot": str(snapshots[1]),
                "test_snapshot": str(snapshots[2]),
                "configurations": [ResearchConfig().to_dict()],
                "learning": recipe.to_dict(),
            },
        )
        assert submitted.status_code == 202, submitted.text
        pending = submitted.json()["experiment_id"]

        class BrokenInput:
            def load_dataset(self, snapshot):
                raise ValueError("synthetic corrupted training publication")

        experiments.advance(pending, BrokenInput())
        failed = client.get(f"/api/experiments/{pending}").json()
        assert failed["status"] == "FAILED"
        assert "corrupted" in failed["fitted"]["error"]
        assert not experiments.pending()
    engine.dispose()
