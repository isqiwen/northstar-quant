"""交易日历 YAML 的严格离线加载器。

该加载器仅解析已经取得的日历快照；它不会下载交易所资料，也不会把工作日规则补成会话。
文件入口只用于显式 opt-in 的 ``test_only`` 夹具。运行时日历必须由 Application 从不可变
制品库读取 payload，再经 :func:`load_trading_calendar_payload` 解析；绝不能把可变 YAML
路径或“某个来源 hash 存在”的布尔回调当作运行时信任根。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from northstar_quant.data_platform.calendars.models import (
    CalendarError,
    CalendarQualityStatus,
    CalendarSession,
    TradingCalendarSnapshot,
)
from northstar_quant.data_platform.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
)


_ROOT_FIELDS = frozenset({"version", "fixture_scope", "snapshots"})
_SNAPSHOT_FIELDS = frozenset(
    {
        "calendar_id",
        "exchange_id",
        "timezone_name",
        "observed_at",
        "available_at",
        "coverage_start",
        "coverage_end",
        "source_artifact_hash",
        "quality_status",
        "trading_days",
        "closed_dates",
        "sessions",
    }
)
_SESSION_FIELDS = frozenset(
    {"exchange_id", "instrument_id", "trading_day", "session_id", "opens_at", "closes_at"}
)


def load_trading_calendar(
    path: str | Path,
    *,
    allow_test_fixtures: bool = False,
) -> tuple[TradingCalendarSnapshot, ...]:
    """从文件加载仅供测试/开发的 ``test_only`` 日历夹具。

    真实提交路径不可调用本函数：文件可能在读取后被替换，且单独的 YAML 不能证明其内容与
    不可变来源制品、授权和 PIT 事实一致。请使用
    :func:`load_trading_calendar_payload` 的 ``require_runtime=True`` 路径。
    """

    config_path = Path(path)
    if not config_path.is_file():
        raise CalendarError(f"交易日历配置不存在：{config_path}")
    try:
        payload = config_path.read_bytes()
    except OSError as exc:
        raise CalendarError(f"交易日历配置无法读取：{config_path}") from exc
    return load_trading_calendar_payload(
        payload,
        allow_test_fixtures=allow_test_fixtures,
        require_runtime=False,
        context=str(config_path),
    )


def load_trading_calendar_payload(
    payload: bytes,
    *,
    allow_test_fixtures: bool = False,
    require_runtime: bool = False,
    context: str = "immutable calendar payload",
) -> tuple[TradingCalendarSnapshot, ...]:
    """解析已经由调用方固定身份的日历 payload。

    ``require_runtime=True`` 只接受 ``fixture_scope: runtime``，供 Application 在验证 immutable
    ArtifactSnapshot、来源血缘、授权范围和内容绑定之后调用。该纯解析器本身不读取文件、网络
    或当前时间，也不接受“来源存在”一类无法绑定内容的布尔验证器。
    """

    root_payload = _load_unique_yaml_payload(payload, context)
    root = _object(root_payload, "trading calendar")
    _exact_fields(root, _ROOT_FIELDS, "trading calendar")
    if type(root["version"]) is not int or root["version"] != 1:
        raise CalendarError("trading calendar version 当前必须是整数 1")

    fixture_scope = _text(root["fixture_scope"], "fixture_scope")
    if fixture_scope not in {"test_only", "runtime"}:
        raise CalendarError("fixture_scope 只能是 test_only 或 runtime")
    if fixture_scope == "test_only":
        if require_runtime:
            raise CalendarError("运行时日历 payload 不能是 test_only fixture")
        if not allow_test_fixtures:
            raise CalendarError("test_only 日历夹具必须显式 allow_test_fixtures=True")
    elif not require_runtime:
        raise CalendarError("运行时日历只能从不可变制品 payload 加载")

    snapshots = tuple(
        _snapshot(item, f"snapshots[{index}]")
        for index, item in enumerate(_list(root["snapshots"], "snapshots"))
    )
    if not snapshots:
        raise CalendarError("snapshots 不能为空；空日历不能作为运行时事实")
    _validate_snapshot_set(snapshots)
    return snapshots


def calendar_content_hash(snapshots: tuple[TradingCalendarSnapshot, ...]) -> str:
    """返回日历语义内容身份，不依赖 YAML 空白、字段顺序或本机路径。

    每个 ``snapshot_hash`` 已涵盖会话、节假日、PIT、质量和来源制品；排序后的集合再形成
    文档级身份。Application 会将它与不可变日历制品 record 中的 provenance 属性比对，防止
    已授权制品 hash 被可变 YAML 的其他内容冒用。
    """

    if not snapshots or not all(isinstance(item, TradingCalendarSnapshot) for item in snapshots):
        raise CalendarError("calendar_content_hash 需要非空 TradingCalendarSnapshot 序列")
    try:
        return canonical_json_sha256(
            {
                "format": "northstar.trading-calendar-content.v1",
                "snapshot_hashes": sorted(item.snapshot_hash for item in snapshots),
            }
        )
    except FingerprintError as exc:
        raise CalendarError("交易日历内容无法生成确定性身份") from exc


def _load_unique_yaml_payload(payload: bytes, context: str) -> object:
    if not isinstance(payload, bytes):
        raise CalendarError("交易日历 payload 必须是 bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CalendarError(f"交易日历 YAML 必须是 UTF-8：{context}") from exc
    try:
        return yaml.load(text, Loader=_StrictYamlLoader)
    except yaml.YAMLError as exc:
        raise CalendarError(f"交易日历 YAML 无法解析：{context}") from exc


def _snapshot(value: object, context: str) -> TradingCalendarSnapshot:
    payload = _object(value, context)
    _exact_fields(payload, _SNAPSHOT_FIELDS, context)
    return TradingCalendarSnapshot.create(
        calendar_id=_text(payload["calendar_id"], f"{context}.calendar_id"),
        exchange_id=_text(payload["exchange_id"], f"{context}.exchange_id"),
        timezone_name=_text(payload["timezone_name"], f"{context}.timezone_name"),
        observed_at=_datetime(payload["observed_at"], f"{context}.observed_at"),
        available_at=_datetime(payload["available_at"], f"{context}.available_at"),
        coverage_start=_date(payload["coverage_start"], f"{context}.coverage_start"),
        coverage_end=_date(payload["coverage_end"], f"{context}.coverage_end"),
        source_artifact_hash=_text(
            payload["source_artifact_hash"],
            f"{context}.source_artifact_hash",
        ),
        quality_status=_quality_status(payload["quality_status"], f"{context}.quality_status"),
        trading_days=tuple(
            _date(item, f"{context}.trading_days[{index}]")
            for index, item in enumerate(_list(payload["trading_days"], f"{context}.trading_days"))
        ),
        closed_dates=tuple(
            _date(item, f"{context}.closed_dates[{index}]")
            for index, item in enumerate(_list(payload["closed_dates"], f"{context}.closed_dates"))
        ),
        sessions=tuple(
            _session(item, f"{context}.sessions[{index}]")
            for index, item in enumerate(_list(payload["sessions"], f"{context}.sessions"))
        ),
    )


def _session(value: object, context: str) -> CalendarSession:
    payload = _object(value, context)
    _exact_fields(payload, _SESSION_FIELDS, context)
    return CalendarSession(
        exchange_id=_text(payload["exchange_id"], f"{context}.exchange_id"),
        instrument_id=_text(payload["instrument_id"], f"{context}.instrument_id"),
        trading_day=_date(payload["trading_day"], f"{context}.trading_day"),
        session_id=_text(payload["session_id"], f"{context}.session_id"),
        opens_at=_datetime(payload["opens_at"], f"{context}.opens_at"),
        closes_at=_datetime(payload["closes_at"], f"{context}.closes_at"),
    )


def _validate_snapshot_set(snapshots: tuple[TradingCalendarSnapshot, ...]) -> None:
    if len({item.calendar_id for item in snapshots}) != len(snapshots):
        raise CalendarError("snapshots 不能包含重复 calendar_id")
    if len({item.snapshot_hash for item in snapshots}) != len(snapshots):
        raise CalendarError("snapshots 不能包含重复内容快照")
    for index, left in enumerate(snapshots):
        for right in snapshots[index + 1 :]:
            if left.exchange_id != right.exchange_id or left.available_at != right.available_at:
                continue
            if max(left.coverage_start, right.coverage_start) <= min(
                left.coverage_end,
                right.coverage_end,
            ):
                raise CalendarError("相同 PIT 可用时间的日历快照 coverage 不能重叠")


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CalendarError(f"{context} 必须是字符串键对象")
    return value


def _list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise CalendarError(f"{context} 必须是列表")
    return value


def _exact_fields(
    payload: dict[str, Any],
    expected: frozenset[str],
    context: str,
) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("缺少字段：" + ", ".join(missing))
        if unknown:
            details.append("未知字段：" + ", ".join(unknown))
        raise CalendarError(f"{context} " + "；".join(details))


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalendarError(f"{context} 必须是非空字符串")
    return value.strip()


def _date(value: object, context: str) -> date:
    if isinstance(value, datetime):
        raise CalendarError(f"{context} 必须是 ISO 日期")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise CalendarError(f"{context} 必须是 ISO 日期")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CalendarError(f"{context} 必须是 ISO 日期") from exc


def _datetime(value: object, context: str) -> datetime:
    if not isinstance(value, str):
        raise CalendarError(f"{context} 必须是含时区的 ISO 时间字符串")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CalendarError(f"{context} 必须是含时区的 ISO 时间字符串") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CalendarError(f"{context} 必须是含时区的 ISO 时间字符串")
    return parsed


def _quality_status(value: object, context: str) -> CalendarQualityStatus:
    if not isinstance(value, str):
        raise CalendarError(f"{context} 必须是 CalendarQualityStatus")
    try:
        return CalendarQualityStatus(value)
    except ValueError as exc:
        raise CalendarError(f"{context} 必须是 CalendarQualityStatus") from exc


class _StrictYamlLoader(yaml.SafeLoader):
    """拒绝 PyYAML 默认会静默覆盖的重复 key。"""


def _construct_unique_mapping(
    loader: _StrictYamlLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise CalendarError("交易日历 YAML 的对象键必须可哈希") from exc
        if duplicate:
            raise CalendarError(f"交易日历 YAML 包含重复字段：{key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)
