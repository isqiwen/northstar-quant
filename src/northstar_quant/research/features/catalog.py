"""P2-WP02 canonical feature catalog。

catalog 不从 YAML 动态加载可执行代码。每个条目都由代码内的稳定 definition、版本化实现
hash 和受控 ``FeatureComputer`` 工厂组成；调用方仍须显式提供 code revision，并把结果登记到
FeatureRegistry。这样不会把“当前最新 feature”隐式塞进研究或交易路径。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from northstar_quant.research.features.basis import RELATIVE_BASIS, RelativeBasisComputer
from northstar_quant.research.features.canonical import CanonicalFeatureDefinition
from northstar_quant.research.features.carry import (
    ANNUALIZED_ROLL_YIELD,
    TERM_STRUCTURE_SLOPE,
    AnnualizedRollYieldComputer,
    TermStructureSlopeComputer,
)
from northstar_quant.research.features.inventory import (
    INVENTORY_LEVEL_CHANGE,
    InventoryLevelChangeComputer,
)
from northstar_quant.research.features.intelligence import (
    INTELLIGENCE_FEATURE_DEFINITIONS,
    IntelligenceMetricComputer,
)
from northstar_quant.research.features.models import FeatureVersion
from northstar_quant.research.features.momentum import MOMENTUM_ROC, MomentumRocComputer
from northstar_quant.research.features.positioning import (
    NET_POSITION_RATIO,
    NetPositionRatioComputer,
)
from northstar_quant.research.features.registry import FeatureComputer, FeatureRegistry
from northstar_quant.research.features.technical import (
    OPEN_INTEREST_CHANGE,
    REALIZED_VOLATILITY,
    VOLUME_RATIO,
    OpenInterestChangeComputer,
    RealizedVolatilityComputer,
    VolumeRatioComputer,
)


@dataclass(frozen=True, slots=True)
class CanonicalFeatureRegistration:
    """一个不可变 definition 与其受控 computer 构造器。"""

    definition: CanonicalFeatureDefinition
    build_computer: Callable[[FeatureVersion], FeatureComputer]


_REGISTRATIONS: tuple[CanonicalFeatureRegistration, ...] = (
    CanonicalFeatureRegistration(MOMENTUM_ROC, MomentumRocComputer),
    CanonicalFeatureRegistration(REALIZED_VOLATILITY, RealizedVolatilityComputer),
    CanonicalFeatureRegistration(VOLUME_RATIO, VolumeRatioComputer),
    CanonicalFeatureRegistration(OPEN_INTEREST_CHANGE, OpenInterestChangeComputer),
    CanonicalFeatureRegistration(ANNUALIZED_ROLL_YIELD, AnnualizedRollYieldComputer),
    CanonicalFeatureRegistration(TERM_STRUCTURE_SLOPE, TermStructureSlopeComputer),
    CanonicalFeatureRegistration(RELATIVE_BASIS, RelativeBasisComputer),
    CanonicalFeatureRegistration(INVENTORY_LEVEL_CHANGE, InventoryLevelChangeComputer),
    CanonicalFeatureRegistration(NET_POSITION_RATIO, NetPositionRatioComputer),
    *(CanonicalFeatureRegistration(definition, IntelligenceMetricComputer) for definition in INTELLIGENCE_FEATURE_DEFINITIONS),
)
_REGISTRATIONS_BY_ID = {item.definition.feature_id: item for item in _REGISTRATIONS}
# Registry 对同一 FeatureVersion 只接受同一个 computer 对象，防止实现被静默替换。
# catalog 因此在同一 Python 进程内复用这一受控对象，使显式 bootstrap 可安全重试。
_COMPUTERS_BY_VERSION_HASH: dict[str, FeatureComputer] = {}

if len(_REGISTRATIONS_BY_ID) != len(_REGISTRATIONS):  # pragma: no cover - import-time contract.
    raise RuntimeError("canonical feature catalog 包含重复 feature_id")
if len({item.definition.implementation_hash for item in _REGISTRATIONS}) != len(_REGISTRATIONS):
    raise RuntimeError("canonical feature catalog 包含重复 implementation_hash")


def list_canonical_feature_registrations() -> tuple[CanonicalFeatureRegistration, ...]:
    """按 feature ID 返回完整 catalog，不提供隐式 latest/默认策略选择。"""

    return tuple(sorted(_REGISTRATIONS, key=lambda item: item.definition.feature_id))


def get_canonical_feature_registration(feature_id: str) -> CanonicalFeatureRegistration:
    """按稳定 feature ID 查询 catalog 条目；未知 ID 失败关闭。"""

    try:
        return _REGISTRATIONS_BY_ID[feature_id]
    except KeyError as exc:
        raise KeyError(f"未知 canonical feature: {feature_id}") from exc


def register_canonical_feature(
    registry: FeatureRegistry,
    *,
    feature_id: str,
    version: str,
    code_revision: str,
) -> FeatureVersion:
    """显式登记一个 canonical definition、version 和同一受控 computer。"""

    registration = get_canonical_feature_registration(feature_id)
    registered_spec = registry.register_spec(registration.definition.feature_spec())
    feature_version = registration.definition.feature_version(
        version=version,
        code_revision=code_revision,
    )
    if feature_version.spec_hash != registered_spec.spec_hash:  # pragma: no cover - defensive.
        raise RuntimeError("canonical feature version 与已登记 spec 不一致")
    registered_version = registry.register_version(feature_version)
    computer = _COMPUTERS_BY_VERSION_HASH.get(registered_version.version_hash)
    if computer is None:
        computer = registration.build_computer(registered_version)
        _COMPUTERS_BY_VERSION_HASH[registered_version.version_hash] = computer
    registry.register_computer(computer)
    return registered_version


def register_all_canonical_features(
    registry: FeatureRegistry,
    *,
    version: str,
    code_revision: str,
) -> tuple[FeatureVersion, ...]:
    """显式登记当前 catalog 的全部 feature；不物化数据或触发回填。"""

    return tuple(
        register_canonical_feature(
            registry,
            feature_id=registration.definition.feature_id,
            version=version,
            code_revision=code_revision,
        )
        for registration in list_canonical_feature_registrations()
    )
