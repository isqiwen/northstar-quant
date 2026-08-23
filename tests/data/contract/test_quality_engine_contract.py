"""P1-WP05 公开质量契约与领域边界测试。"""

from __future__ import annotations

from pathlib import Path

import northstar_quant.data.quality as quality

from tests.helpers.paths import PROJECT_ROOT


def test_quality_engine_exports_explicit_prepublication_contract() -> None:
    expected = {
        "DataQualityEngine",
        "DataQualityError",
        "QualityRequest",
        "QualityEvaluation",
        "QualityFinding",
        "QualityEvidence",
        "QualityReferenceDecision",
        "QualityRule",
        "QualityMode",
        "CalendarConsistencyResolver",
        "ContractConsistencyResolver",
        "CalendarCoverageResolver",
        "canonical_frame_payload",
    }

    assert expected <= set(quality.__all__)
    assert {rule.value for rule in quality.QualityRule} == {
        "completeness",
        "uniqueness",
        "ordering",
        "schema",
        "range",
        "calendar_consistency",
        "contract_consistency",
        "staleness",
        "gap",
        "revision",
    }


def test_quality_core_does_not_depend_on_application_trading_or_mutable_runtime_storage() -> None:
    root = PROJECT_ROOT / "src" / "northstar_quant" / "data" / "quality"
    source = "\n".join(
        (root / name).read_text(encoding="utf-8") for name in ("models.py", "engine.py")
    )

    forbidden = (
        "northstar_quant.application",
        "northstar_quant.trading_execution",
        "foundation.db",
        "sqlalchemy",
        "requests",
        "httpx",
        "datetime.now",
        "ArtifactStore",
    )
    assert not [fragment for fragment in forbidden if fragment in source]


def test_quality_assessment_is_explicitly_prepublication_not_artifact_store_persistence() -> None:
    source = Path(
        PROJECT_ROOT
        / "src"
        / "northstar_quant"
        / "data"
        / "quality"
        / "models.py"
    ).read_text(encoding="utf-8")

    assert "bind_published_artifact" in source
    assert "DataQualityResult" in source
    assert "ArtifactStore" not in source
