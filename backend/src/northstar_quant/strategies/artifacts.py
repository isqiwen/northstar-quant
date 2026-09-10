"""One bounded fixed strategy artifact format, shared by publication and local reception."""

from typing import Any

from northstar_quant import code_revision
from northstar_quant.factors.definition import content_id
from northstar_quant.risk.configuration import RiskConfig
from northstar_quant.simulation.configuration import SimulationConfig

from .configuration import StrategyConfig

CANDIDATE_FORMAT = 2


def verify_candidate(
    value: dict[str, Any], *, require_installed_revision: bool = False
) -> dict[str, Any]:
    """Verify content and code bindings, never production admission or authority.

    A receiver may require matching installed code in either SANDBOX or LIVE.
    This proves neither historical validity nor permission to send an order.
    """
    try:
        return _verify(value, require_installed_revision=require_installed_revision)
    except (KeyError, TypeError, AttributeError, ArithmeticError) as error:
        raise ValueError("candidate content is incomplete or invalid") from error


def _verify(value: dict[str, Any], *, require_installed_revision: bool) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "candidate_id", "version_id", "document", "same_clean_revision"}
        or value["format"] != CANDIDATE_FORMAT
    ):
        raise ValueError("unsupported fixed strategy candidate")
    document = value["document"]
    if not isinstance(document, dict) or content_id(document) != value["version_id"]:
        raise ValueError("strategy version integrity failure")
    if (
        content_id({key: item for key, item in value.items() if key != "candidate_id"})
        != value["candidate_id"]
    ):
        raise ValueError("candidate integrity failure")
    raw = document["configuration"]["config"]
    if set(document) != {"configuration", "code_revision", "evidence", "validation"} or set(
        raw
    ) != {"strategy", "risk", "simulation"}:
        raise ValueError("candidate contains unsupported fields")
    strategy = StrategyConfig.from_dict(raw["strategy"])
    config = {
        "strategy": strategy.to_dict(),
        "risk": RiskConfig.from_dict(raw["risk"]).to_dict(),
        "simulation": SimulationConfig.from_dict(raw["simulation"]).to_dict(),
    }
    expected = content_id({"name": document["configuration"]["name"], "config": config})
    if expected != document["configuration"]["configuration_id"]:
        raise ValueError("candidate configuration integrity failure")
    if not document["evidence"]:
        raise ValueError("candidate requires actual research evidence")
    for run in document["evidence"]:
        material = {key: run[key] for key in ("code_revision", "snapshot", "config", "result")}
        if content_id(material) != run["run_id"] or run["config"] != config:
            raise ValueError("candidate research evidence integrity failure")
    current = code_revision()
    refs = [
        strategy.code_revision,
        *(binding.code_revision for _, binding in strategy.factors),
        document["code_revision"],
        *(run["code_revision"] for run in document["evidence"]),
    ]
    same_clean_revision = all(not ref.endswith("-dirty") for ref in refs) and len(set(refs)) == 1
    if value["same_clean_revision"] is not same_clean_revision:
        raise ValueError("candidate code provenance is inconsistent")
    if require_installed_revision and (
        not same_clean_revision or any(ref != current for ref in refs)
    ):
        raise ValueError("Live requires a clean candidate matching its installed Git revision")
    return value
