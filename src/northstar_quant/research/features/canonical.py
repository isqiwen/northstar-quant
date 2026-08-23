"""Canonical Feature Families 的受控计算基础。

这里的对象把 P2-WP02 的特征定义和 P2-WP01 的 ``FeatureComputer`` 协议连接起来。
它们只读取 ``MarketDataSnapshot.selected_frame()``，不接受裸 DataFrame、文件路径、网络
或当前时钟。每个输入逻辑键都会得到一个 ``FeatureValue``；无法计算时输出带原因码的
显式缺失值，绝不静默丢弃行。

当前 Registry 的输入仍是单一 ``STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY`` 快照。因此本模块
不把任何输出标记为逐决策无前视特征，也不把它接入策略、回测准入或交易路径。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
import math
import re
from types import MappingProxyType
from typing import cast

import polars as pl

from northstar_quant.data_platform.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.data_platform.market.pit import (
    MarketDataKind,
    MarketDataPITSpec,
    MarketDataSnapshot,
)
from northstar_quant.data_platform.sources.protocol import PublicationScope
from northstar_quant.research.features.models import (
    FeatureLineage,
    FeatureRegistryError,
    FeatureSpec,
    FeatureValue,
    FeatureVersion,
)


_ACTUAL_CONTRACT_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*\.[0-9]{3,6}$")


def _required_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value or not value.replace("_", "a").isalnum():
        raise FeatureRegistryError(f"{field_name} 必须是非空字段标识")
    return value


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise FeatureRegistryError(f"{field_name} 必须是非空单行文本")
    return value.strip()


def _tuple_of_identifiers(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    result = tuple(_required_identifier(value, field_name) for value in values)
    if not result:
        raise FeatureRegistryError(f"{field_name} 不能为空")
    if len(result) != len(set(result)):
        raise FeatureRegistryError(f"{field_name} 不能包含重复字段")
    return result


def _freeze_json_value(value: object, field_name: str) -> object:
    """深度冻结 parameter schema，避免 catalog 常量被进程内调用方改写。"""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                _required_identifier(str(key), f"{field_name}.key"): _freeze_json_value(
                    item, f"{field_name}.{key}"
                )
                for key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item, field_name) for item in value)
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise FeatureRegistryError(f"{field_name} 必须是有限、可 JSON 序列化的值") from exc
    return value


def _thaw_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class FeatureInputContract:
    """一个 family 可消费的 feature-ready、单一 P1 输入契约。"""

    kind: MarketDataKind
    schema_version: str
    entity_key_columns: tuple[str, ...]
    event_time_column: str
    available_at_column: str
    value_columns: tuple[str, ...] | None = None
    requires_actual_contract_data: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MarketDataKind):
            raise FeatureRegistryError("input_contract.kind 必须是 MarketDataKind")
        schema_version = _required_text(self.schema_version, "input_contract.schema_version")
        entity_key_columns = _tuple_of_identifiers(
            self.entity_key_columns, "input_contract.entity_key_columns"
        )
        event_time_column = _required_identifier(
            self.event_time_column, "input_contract.event_time_column"
        )
        available_at_column = _required_identifier(
            self.available_at_column, "input_contract.available_at_column"
        )
        value_columns = (
            None
            if self.value_columns is None
            else _tuple_of_identifiers(self.value_columns, "input_contract.value_columns")
        )
        if event_time_column in entity_key_columns:
            raise FeatureRegistryError("event_time_column 不能同时是 entity key")
        if available_at_column in {*entity_key_columns, event_time_column}:
            raise FeatureRegistryError("available_at_column 不能同时是 key/event time")
        if value_columns is not None and set(value_columns).intersection(
            {*entity_key_columns, event_time_column, available_at_column}
        ):
            raise FeatureRegistryError(
                "input_contract.value_columns 不能与 key/event time/available_at 重叠"
            )
        if type(self.requires_actual_contract_data) is not bool:
            raise FeatureRegistryError("requires_actual_contract_data 必须是 bool")
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "entity_key_columns", entity_key_columns)
        object.__setattr__(self, "event_time_column", event_time_column)
        object.__setattr__(self, "available_at_column", available_at_column)
        object.__setattr__(self, "value_columns", value_columns)

    def validate_snapshot(
        self,
        snapshot: MarketDataSnapshot,
        *,
        required_columns: Iterable[str],
    ) -> pl.DataFrame:
        """验证 snapshot 的完整输入身份后，返回固定排序的独立表。"""

        if not isinstance(snapshot, MarketDataSnapshot):
            raise FeatureRegistryError("canonical feature 必须接收 MarketDataSnapshot")
        spec = snapshot.spec
        if not isinstance(spec, MarketDataPITSpec):  # 防御测试替身或错误适配器。
            raise FeatureRegistryError("MarketDataSnapshot.spec 必须是 MarketDataPITSpec")
        if spec.kind is not self.kind:
            raise FeatureRegistryError(
                f"canonical feature 需要 {self.kind.value} 输入，实际为 {spec.kind.value}"
            )
        if spec.schema_version != self.schema_version:
            raise FeatureRegistryError(
                "canonical feature 输入 schema_version 与 feature-ready 契约不一致"
            )
        if (
            self.requires_actual_contract_data
            and not snapshot.publication_scope.actual_contract_data
        ):
            raise FeatureRegistryError(
                "canonical feature 只接受已声明 actual_contract_data=true 的实际合约输入"
            )
        if spec.event_time_column != self.event_time_column:
            raise FeatureRegistryError("canonical feature 输入 event_time_column 不一致")
        if spec.available_at_column != self.available_at_column:
            raise FeatureRegistryError("canonical feature 输入 available_at_column 不一致")
        if self.value_columns is not None and spec.value_columns != self.value_columns:
            raise FeatureRegistryError(
                "canonical feature 输入 value_columns 必须精确匹配 feature-ready 契约"
            )
        actual_entity_keys = tuple(key for key in spec.key_columns if key != spec.event_time_column)
        if set(actual_entity_keys) != set(self.entity_key_columns):
            raise FeatureRegistryError("canonical feature 输入 entity key 与契约不一致")
        frame = snapshot.selected_frame()
        required = set(required_columns).union(
            self.entity_key_columns,
            {self.event_time_column, self.available_at_column},
        )
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise FeatureRegistryError("canonical feature 输入缺少必需列: " + ", ".join(missing))
        expected_logical_keys = (*self.entity_key_columns, self.event_time_column)
        if frame.select(pl.struct(expected_logical_keys).n_unique()).item() != frame.height:
            raise FeatureRegistryError("canonical feature 输入包含重复的 entity/event key")
        for row in frame.select(
            (*self.entity_key_columns, self.event_time_column, self.available_at_column)
        ).iter_rows(named=True):
            if any(row[column] is None for column in self.entity_key_columns):
                raise FeatureRegistryError("canonical feature 输入 entity key 不能为 null")
            event_time = row[self.event_time_column]
            available_at = row[self.available_at_column]
            if not isinstance(event_time, (date, datetime)):
                raise FeatureRegistryError("canonical feature event_time 必须是 date 或 datetime")
            if not isinstance(available_at, datetime) or available_at.tzinfo is None:
                raise FeatureRegistryError("canonical feature available_at 必须是带时区 datetime")
            if isinstance(event_time, datetime):
                if event_time.tzinfo is None or event_time > available_at:
                    raise FeatureRegistryError(
                        "canonical feature event_time/available_at 时间关系无效"
                    )
            elif event_time > available_at.date():
                raise FeatureRegistryError("canonical feature event_time 不能晚于 available_at")
        return frame.sort((*self.entity_key_columns, self.event_time_column))


@dataclass(frozen=True, slots=True)
class CanonicalFeatureDefinition:
    """特征家族的稳定研究合同及其实现身份。"""

    feature_id: str
    description: str
    input_contract: FeatureInputContract
    required_columns: tuple[str, ...]
    output_column: str
    lookback_semantics: str
    missing_value_semantics: str
    parameter_schema: Mapping[str, object]
    implementation_revision: str = "v1"

    def __post_init__(self) -> None:
        feature_id = _required_text(self.feature_id, "feature_id")
        family, separator, _ = feature_id.partition(".")
        if not separator:
            raise FeatureRegistryError("feature_id 必须是 family.name 形式")
        _required_identifier(family, "feature_id.family")
        required_columns = _tuple_of_identifiers(self.required_columns, "required_columns")
        output_column = _required_identifier(self.output_column, "output_column")
        if output_column in required_columns:
            raise FeatureRegistryError("output_column 不能覆盖 required_columns")
        if not isinstance(self.parameter_schema, Mapping):
            raise FeatureRegistryError("parameter_schema 必须是映射")
        parameter_schema = _freeze_json_value(self.parameter_schema, "parameter_schema")
        if not isinstance(parameter_schema, Mapping):  # pragma: no cover - protected above.
            raise FeatureRegistryError("parameter_schema 必须是映射")
        object.__setattr__(self, "feature_id", feature_id)
        object.__setattr__(self, "description", _required_text(self.description, "description"))
        object.__setattr__(self, "required_columns", required_columns)
        object.__setattr__(self, "output_column", output_column)
        object.__setattr__(
            self,
            "lookback_semantics",
            _required_text(self.lookback_semantics, "lookback_semantics"),
        )
        object.__setattr__(
            self,
            "missing_value_semantics",
            _required_text(self.missing_value_semantics, "missing_value_semantics"),
        )
        object.__setattr__(
            self,
            "implementation_revision",
            _required_text(self.implementation_revision, "implementation_revision"),
        )
        object.__setattr__(self, "parameter_schema", parameter_schema)

    @property
    def family(self) -> str:
        return self.feature_id.split(".", 1)[0]

    @property
    def input_columns(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    *self.input_contract.entity_key_columns,
                    self.input_contract.event_time_column,
                    self.input_contract.available_at_column,
                    *self.required_columns,
                }
            )
        )

    @property
    def implementation_hash(self) -> str:
        """稳定实现身份；发布版本仍须显式记录其 code_revision。"""

        return canonical_json_sha256(
            {
                "feature_id": self.feature_id,
                "format": "northstar.canonical-feature-implementation.v1",
                "implementation_revision": self.implementation_revision,
                "input_contract": {
                    "available_at_column": self.input_contract.available_at_column,
                    "entity_key_columns": list(self.input_contract.entity_key_columns),
                    "event_time_column": self.input_contract.event_time_column,
                    "kind": self.input_contract.kind.value,
                    "requires_actual_contract_data": (
                        self.input_contract.requires_actual_contract_data
                    ),
                    "schema_version": self.input_contract.schema_version,
                    "value_columns": (
                        list(self.input_contract.value_columns)
                        if self.input_contract.value_columns is not None
                        else None
                    ),
                },
                "description": self.description,
                "lookback_semantics": self.lookback_semantics,
                "missing_value_semantics": self.missing_value_semantics,
                "output_column": self.output_column,
                "required_columns": list(self.required_columns),
                "parameter_schema": _thaw_json_value(self.parameter_schema),
            }
        )

    def feature_spec(self) -> FeatureSpec:
        return FeatureSpec(
            feature_id=self.feature_id,
            family=self.family,
            description=self.description,
            input_columns=self.input_columns,
            input_schema_version=self.input_contract.schema_version,
            entity_key_columns=self.input_contract.entity_key_columns,
            output_column=self.output_column,
            event_time_column=self.input_contract.event_time_column,
            available_at_column=self.input_contract.available_at_column,
            lookback_semantics=self.lookback_semantics,
            missing_value_semantics=self.missing_value_semantics,
        )

    def feature_version(self, *, version: str, code_revision: str) -> FeatureVersion:
        parameter_schema = _thaw_json_value(self.parameter_schema)
        if not isinstance(parameter_schema, Mapping):  # pragma: no cover - definition guard.
            raise FeatureRegistryError("canonical definition 的 parameter_schema 无效")
        return FeatureVersion.from_spec(
            self.feature_spec(),
            version=version,
            implementation_hash=self.implementation_hash,
            code_revision=code_revision,
            parameter_schema=cast(Mapping[str, object], parameter_schema),
        )


@dataclass(frozen=True, slots=True)
class CanonicalFeatureRow:
    """经过 P1/P2 输入契约验证的一行 feature-ready 事实。"""

    key: Mapping[str, object]
    event_time: date | datetime
    values: Mapping[str, object]


class CanonicalFeatureComputer:
    """各 family computer 的公共身份与输入校验基座。"""

    __slots__ = ("feature_version_hash", "implementation_hash", "definition", "_sealed")

    feature_version_hash: str
    implementation_hash: str
    definition: CanonicalFeatureDefinition
    _sealed: bool

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("canonical FeatureComputer 的身份在登记后不可变")
        object.__setattr__(self, name, value)

    def __init__(self, version: FeatureVersion, definition: CanonicalFeatureDefinition) -> None:
        if not isinstance(version, FeatureVersion):
            raise FeatureRegistryError("canonical computer 必须绑定 FeatureVersion")
        if version.feature_id != definition.feature_id:
            raise FeatureRegistryError("FeatureVersion.feature_id 与 canonical definition 不一致")
        if version.spec_hash != definition.feature_spec().spec_hash:
            raise FeatureRegistryError("FeatureVersion.spec_hash 与 canonical definition 不一致")
        if version.implementation_hash != definition.implementation_hash:
            raise FeatureRegistryError("FeatureVersion.implementation_hash 与 canonical 实现不一致")
        expected_parameter_schema = _thaw_json_value(definition.parameter_schema)
        if not isinstance(
            expected_parameter_schema, Mapping
        ):  # pragma: no cover - definition guard.
            raise FeatureRegistryError("canonical definition 的 parameter_schema 无效")
        if version.parameter_schema != expected_parameter_schema:
            raise FeatureRegistryError("FeatureVersion.parameter_schema 与 canonical 定义不一致")
        object.__setattr__(self, "feature_version_hash", version.version_hash)
        object.__setattr__(self, "implementation_hash", version.implementation_hash)
        object.__setattr__(self, "definition", definition)
        object.__setattr__(self, "_sealed", True)

    def _rows(
        self,
        *,
        market_snapshot: MarketDataSnapshot,
        lineage: FeatureLineage,
    ) -> tuple[CanonicalFeatureRow, ...]:
        if not isinstance(lineage, FeatureLineage):
            raise FeatureRegistryError("canonical computer 必须接收 FeatureLineage")
        if lineage.feature_version_hash != self.feature_version_hash:
            raise FeatureRegistryError("FeatureLineage 与 canonical computer version 不一致")
        if lineage.implementation_hash != self.implementation_hash:
            raise FeatureRegistryError("FeatureLineage 与 canonical computer implementation 不一致")
        frame = self.definition.input_contract.validate_snapshot(
            market_snapshot,
            required_columns=self.definition.required_columns,
        )
        rows: list[CanonicalFeatureRow] = []
        for raw in frame.iter_rows(named=True):
            event_time = raw[self.definition.input_contract.event_time_column]
            if not isinstance(event_time, (date, datetime)):  # protected by input contract.
                raise FeatureRegistryError("canonical feature event_time 类型无效")
            rows.append(
                CanonicalFeatureRow(
                    key={
                        column: raw[column]
                        for column in self.definition.input_contract.entity_key_columns
                    },
                    event_time=event_time,
                    values=raw,
                )
            )
        return tuple(rows)

    @staticmethod
    def _groups(
        rows: Iterable[CanonicalFeatureRow],
        *,
        entity_key_columns: tuple[str, ...],
    ) -> tuple[tuple[CanonicalFeatureRow, ...], ...]:
        grouped: dict[tuple[object, ...], list[CanonicalFeatureRow]] = defaultdict(list)
        for row in rows:
            grouped[tuple(row.key[column] for column in entity_key_columns)].append(row)
        return tuple(tuple(grouped[key]) for key in sorted(grouped, key=repr))

    @staticmethod
    def _value(
        *,
        lineage: FeatureLineage,
        row: CanonicalFeatureRow,
        value: float | None,
        missing_reason: str | None = None,
    ) -> FeatureValue:
        if value is not None and (
            not isinstance(value, (int, float)) or not math.isfinite(float(value))
        ):
            raise FeatureRegistryError("canonical feature 计算得到非有限数值")
        return FeatureValue.from_lineage(
            lineage=lineage,
            key=row.key,
            event_time=row.event_time,
            value=float(value) if value is not None else None,
            missing_reason=missing_reason,
        )


def finite_number(value: object, *, field_name: str, allow_none: bool = True) -> float | None:
    """读取有限数字；null 留给 family 产生显式 missing，非有限值一律拒绝。"""

    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FeatureRegistryError(f"{field_name} 必须是有限数值")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise FeatureRegistryError(f"{field_name} 必须是有限数值")
    return numeric


def positive_number(value: object, *, field_name: str) -> float | None:
    numeric = finite_number(value, field_name=field_name)
    if numeric is not None and numeric <= 0:
        raise FeatureRegistryError(f"{field_name} 必须大于 0")
    return numeric


def non_negative_number(value: object, *, field_name: str) -> float | None:
    numeric = finite_number(value, field_name=field_name)
    if numeric is not None and numeric < 0:
        raise FeatureRegistryError(f"{field_name} 不能小于 0")
    return numeric


def required_text_value(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name) if isinstance(value, str) else None


def actual_contract_id(value: object, *, field_name: str = "contract_id") -> str | None:
    """读取 Contract Master 形式的实际合约 ID，拒绝裸券商/行情代码。"""

    identifier = required_text_value(value, field_name=field_name)
    if identifier is None:
        return None
    if _ACTUAL_CONTRACT_ID_RE.fullmatch(identifier) is None:
        raise FeatureRegistryError(
            f"{field_name} 必须是 EXCHANGE.PRODUCT.MONTH 形式的 Contract Master contract_id"
        )
    return identifier


def actual_contract_id_in_scope(
    value: object,
    *,
    scope: PublicationScope,
    expected_product: object | None = None,
    field_name: str = "contract_id",
) -> str | None:
    """验证实际合约 ID 与冻结发布范围、可选产品键的一致性。"""

    if not isinstance(scope, PublicationScope):
        raise FeatureRegistryError("actual contract 输入必须携带冻结 PublicationScope")
    identifier = actual_contract_id(value, field_name=field_name)
    if identifier is None:
        return None
    exchange_id, product_code, _ = identifier.split(".")
    if not scope.actual_contract_data:
        raise FeatureRegistryError("实际合约输入必须声明 actual_contract_data=true")
    if exchange_id not in scope.exchanges or product_code not in scope.products:
        raise FeatureRegistryError("实际合约 ID 不在冻结 PublicationScope 的交易所/品种范围内")
    if expected_product is not None:
        if not isinstance(expected_product, str) or not expected_product.strip():
            raise FeatureRegistryError("实际合约输入的 product 必须是非空文本")
        if product_code != expected_product.strip().upper():
            raise FeatureRegistryError("实际合约 ID 与输入 product 不一致")
    return identifier


def expiry_date(value: object, *, field_name: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise FeatureRegistryError(f"{field_name} 的 datetime 必须带时区")
        return value.astimezone(UTC).date()
    if isinstance(value, date):
        return value
    raise FeatureRegistryError(f"{field_name} 必须是 date 或带时区 datetime")


def integer_parameter(
    parameters: Mapping[str, object],
    name: str,
    *,
    minimum: int,
) -> int:
    """读取已由 FeatureVersion 校验过的整数参数，并保留 computer 的防御校验。"""

    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise FeatureRegistryError(f"parameters.{name} 必须是不小于 {minimum} 的整数")
    return value


CN_FUTURES_FEATURE_BAR_V1 = FeatureInputContract(
    kind=MarketDataKind.BAR,
    schema_version="cn_futures_feature_bar_v1",
    entity_key_columns=("symbol",),
    event_time_column="date",
    available_at_column="available_at",
)


CN_FUTURES_ACTUAL_CONTRACT_FEATURE_BAR_V1 = FeatureInputContract(
    kind=MarketDataKind.BAR,
    schema_version="cn_futures_actual_contract_feature_bar_v1",
    entity_key_columns=("contract_id",),
    event_time_column="date",
    available_at_column="available_at",
    requires_actual_contract_data=True,
)
