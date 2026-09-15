"""Fixed-input research execution and result replay shared by Web and CLI."""

from typing import cast
from uuid import UUID

from northstar_quant import code_revision
from northstar_quant.data_management.publications import DatasetReader
from northstar_quant.research.backtesting import run_research
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.runs import RunStore


class ResearchOperations:
    """Coordinate one bounded run; durable independent scheduling is a separate capability."""

    def __init__(self, library: DatasetReader, runs: RunStore) -> None:
        self._library = library
        self._runs = runs

    def run(self, snapshot_id: UUID, config: ResearchConfig) -> str:
        attempt = self._runs.begin_attempt(snapshot_id, config)
        try:
            dataset = self._library.load_dataset(snapshot_id)
            result = run_research(dataset, config)
            run_id = self._runs.save(dataset, config, result)
        except Exception as error:
            self._runs.finish_attempt(attempt, error=str(error)[:1000])
            raise
        self._runs.finish_attempt(attempt, run_id=run_id)
        return run_id

    def replay(self, run_id: str) -> dict[str, object]:
        original = self._runs.get(run_id)
        if original["code_revision"] != code_revision():
            raise ValueError("replay requires the saved Git code revision")
        snapshot = cast(dict[str, object], original["snapshot"])
        dataset = self._library.load_dataset(UUID(str(snapshot["id"])))
        if dataset.content_hash != snapshot["content_hash"]:
            raise ValueError("stored snapshot identity does not match the saved research")
        config = ResearchConfig.from_mapping(cast(dict[str, object], original["config"]))
        reproduced = self._runs.save(dataset, config, run_research(dataset, config))
        if reproduced != run_id:
            raise ValueError("replay did not reproduce the saved result identity")
        return self._runs.get(reproduced)

    def compare(self, run_ids: list[str]) -> list[dict[str, object]]:
        if len(run_ids) != 2 or len(set(run_ids)) != 2:
            raise ValueError("comparison requires two distinct fixed runs")
        runs = [self._runs.get(identity) for identity in run_ids]
        first = runs[0]
        config = cast(dict[str, object], first["config"])
        for run in runs[1:]:
            other = cast(dict[str, object], run["config"])
            if (
                run["snapshot"] != first["snapshot"]
                or run["code_revision"] != first["code_revision"]
                or cast(dict[str, object], cast(dict[str, object], run["result"])["evaluation"])[
                    "plan"
                ]
                != cast(dict[str, object], cast(dict[str, object], first["result"])["evaluation"])[
                    "plan"
                ]
                or any(other[key] != config[key] for key in ("risk", "simulation"))
            ):
                raise ValueError(
                    "comparison requires the same snapshot, evaluation plan, code, risk "
                    "and simulation assumptions"
                )
        return [
            {
                "run_id": run["run_id"],
                "strategy": cast(
                    dict[str, object], cast(dict[str, object], run["config"])["strategy"]
                )["strategy_id"],
                **cast(dict[str, object], cast(dict[str, object], run["result"])["summary"]),
            }
            for run in runs
        ]
