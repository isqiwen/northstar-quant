"""Data Platform 公开领域契约的稳定性检查。"""

from __future__ import annotations

from dataclasses import is_dataclass
from pathlib import Path

import northstar_quant.data_platform.contracts as contracts
from northstar_quant.data_platform.artifacts import fingerprints


PUBLIC_DOMAIN_TYPES = frozenset(
    {
        "DataSource",
        "ArtifactSnapshot",
        "RawArtifact",
        "NormalizedArtifact",
        "DerivedArtifact",
        "DatasetVersion",
        "DataQualityResult",
        "DataLineage",
        "LicenseMetadata",
    }
)


def test_data_domain_core_is_explicitly_exported_as_frozen_value_contracts() -> None:
    assert PUBLIC_DOMAIN_TYPES <= set(contracts.__all__)
    for name in PUBLIC_DOMAIN_TYPES:
        model = getattr(contracts, name)
        assert is_dataclass(model), f"{name} 必须是 dataclass 值对象"
        assert model.__dataclass_params__.frozen, f"{name} 必须不可变"


def test_reproducibility_fingerprints_are_pure_and_exclude_runtime_locations() -> None:
    source = fingerprints.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")

    for forbidden in ("Path(", "datetime.now", "uuid", "os.environ"):
        assert forbidden not in text
