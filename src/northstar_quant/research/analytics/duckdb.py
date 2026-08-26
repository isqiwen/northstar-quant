"""只读 DuckDB 历史分析边界。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import re
from typing import cast

import duckdb
import polars as pl

from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256, content_sha256
from northstar_quant.data.lake.models import LakeDatasetReference
from northstar_quant.data.lake.store import ParquetLakeStore
from northstar_quant.data.quality import canonical_frame_payload


class DuckDBAnalyticsError(RuntimeError):
    """查询越过只读/PIT 边界或分析结果不可回放。"""


_FORBIDDEN_SQL = re.compile(
    r"\b(?:"
    r"alter|attach|call|checkpoint|copy|create|delete|detach|drop|export|"
    r"import|insert|install|load|merge|pragma|read_csv|read_json|read_parquet|"
    r"read_text|replace|set|sqlite_scan|parquet_scan|parquet_metadata|query_table|"
    r"truncate|update|vacuum|write_csv|httpfs|postgres_scan|glob|"
    r"fetch|limit|offset|recursive|sample|tablesample"
    r")\b",
    flags=re.IGNORECASE,
)
_VOLATILE_SQL = re.compile(
    r"\b(?:"
    r"current_date|current_localtime|current_localtimestamp|current_time|"
    r"current_timestamp|gen_random_uuid|localtime|localtimestamp|now|"
    r"random|row_number|rank|dense_rank|percent_rank|cume_dist|ntile|"
    r"lag|lead|first_value|last_value|uuid"
    r")\b",
    flags=re.IGNORECASE,
)
_ALLOWED_LAKE_SCAN_NAMES = frozenset({"lake_data", "main.lake_data", "memory.main.lake_data"})


@dataclass(frozen=True, slots=True)
class DuckDBQueryRequest:
    """只接受显式 Lake version、PIT 时点和单条 SELECT/WITH 查询。"""

    reference: LakeDatasetReference
    sql: str
    as_of: datetime
    parameters: tuple[object, ...] = ()
    maximum_rows: int = 1_000

    def __post_init__(self) -> None:
        if not isinstance(self.reference, LakeDatasetReference):
            raise DuckDBAnalyticsError("reference 必须是 LakeDatasetReference")
        if not isinstance(self.sql, str) or not self.sql.strip() or len(self.sql) > 20_000:
            raise DuckDBAnalyticsError("sql 必须是长度不超过 20000 的非空文本")
        normalized_sql = self.sql.strip()
        _validate_sql(normalized_sql)
        if (
            not isinstance(self.as_of, datetime)
            or self.as_of.tzinfo is None
            or self.as_of.utcoffset() is None
        ):
            raise DuckDBAnalyticsError("as_of 必须是带时区的 datetime")
        if isinstance(self.maximum_rows, bool) or not isinstance(self.maximum_rows, int):
            raise DuckDBAnalyticsError("maximum_rows 必须是正整数")
        if self.maximum_rows < 1 or self.maximum_rows > 10_000:
            raise DuckDBAnalyticsError("maximum_rows 必须在 1 至 10000 之间")
        parameters = tuple(_parameter(value) for value in self.parameters)
        object.__setattr__(self, "sql", normalized_sql)
        object.__setattr__(self, "as_of", self.as_of.astimezone(UTC))
        object.__setattr__(self, "parameters", parameters)


@dataclass(frozen=True, slots=True)
class DuckDBQueryReceipt:
    """一次可回放的 DuckDB 分析证据，而不是研究决策或交易指令。"""

    lake_reference: LakeDatasetReference
    lake_manifest_sha256: str
    sql: str
    query_sha256: str
    parameters: tuple[object, ...]
    parameters_sha256: str
    as_of: datetime
    duckdb_version: str
    result_sha256: str
    row_count: int

    def as_mapping(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "duckdb_version": self.duckdb_version,
            "lake_manifest_sha256": self.lake_manifest_sha256,
            "lake_reference": self.lake_reference.as_mapping(),
            "parameters": list(self.parameters),
            "parameters_sha256": self.parameters_sha256,
            "query_sha256": self.query_sha256,
            "result_sha256": self.result_sha256,
            "row_count": self.row_count,
            "sql": self.sql,
        }


@dataclass(frozen=True, slots=True)
class DuckDBQueryResult:
    """内存分析结果与其不可变查询收据。"""

    frame: pl.DataFrame
    receipt: DuckDBQueryReceipt


class HistoricalLakeDuckDB:
    """只把已验真的 Lake Parquet 注册进内存 DuckDB。"""

    def __init__(self, lake_store: ParquetLakeStore) -> None:
        if not isinstance(lake_store, ParquetLakeStore):
            raise DuckDBAnalyticsError("lake_store 必须是 ParquetLakeStore")
        self._lake_store = lake_store

    @classmethod
    def from_settings(cls) -> "HistoricalLakeDuckDB":
        return cls(ParquetLakeStore.from_settings())

    def query(self, request: DuckDBQueryRequest) -> DuckDBQueryResult:
        """执行内存 SELECT，并在 DuckDB 可见前强制套用 ``available_at <= as_of``。"""

        if not isinstance(request, DuckDBQueryRequest):
            raise DuckDBAnalyticsError("request 必须是 DuckDBQueryRequest")
        verified = self._lake_store.verify(request.reference)
        available_at_column = verified.manifest.available_at_column
        connection = duckdb.connect(":memory:")
        try:
            connection.execute("SET TimeZone='UTC'")
            with self._lake_store.query_snapshot(verified) as parquet_paths:
                relation = connection.read_parquet([str(path) for path in parquet_paths])
                relation.filter(
                    f"{_quoted_identifier(available_at_column)} <= {_timestamp_literal(request.as_of)}"
                ).create("lake_data")
                # 只在将已验证 Parquet query snapshot 物化到 :memory: 临时表之前允许文件访问；
                # 用户 SQL 随后即使绕过词法拒绝，也无法读取本机或网络上的其他文件、扩展或数据库。
                connection.execute("SET enable_external_access=false")
                _validate_query_plan(connection, request.sql, request.parameters)
                wrapped_sql = (
                    "SELECT * FROM ("
                    f"{request.sql}"
                    ") AS northstar_lake_query ORDER BY ALL "
                    f"LIMIT {request.maximum_rows + 1}"
                )
                arrow_table = connection.execute(
                    wrapped_sql, list(request.parameters)
                ).to_arrow_table()
                frame = cast(pl.DataFrame, pl.from_arrow(arrow_table))
        except duckdb.Error as exc:
            raise DuckDBAnalyticsError("DuckDB 历史分析失败") from exc
        finally:
            connection.close()
        if frame.height > request.maximum_rows:
            raise DuckDBAnalyticsError("查询结果超过 maximum_rows；请缩小范围或聚合")
        query_payload = {
            "lake_version_hash": request.reference.version_hash,
            "parameters": list(request.parameters),
            "sql": request.sql,
        }
        receipt = DuckDBQueryReceipt(
            lake_reference=request.reference,
            lake_manifest_sha256=verified.manifest_sha256,
            sql=request.sql,
            query_sha256=canonical_json_sha256(query_payload),
            parameters=request.parameters,
            parameters_sha256=canonical_json_sha256(list(request.parameters)),
            as_of=request.as_of,
            duckdb_version=duckdb.__version__,
            result_sha256=content_sha256(
                canonical_frame_payload(frame), field_name="duckdb result"
            ),
            row_count=frame.height,
        )
        return DuckDBQueryResult(frame=frame, receipt=receipt)

    def verify_replay(
        self,
        request: DuckDBQueryRequest,
        receipt: DuckDBQueryReceipt,
    ) -> DuckDBQueryResult:
        """重新执行并要求输入、版本与结果 hash 都与原收据一致。"""

        if not isinstance(receipt, DuckDBQueryReceipt):
            raise DuckDBAnalyticsError("receipt 必须是 DuckDBQueryReceipt")
        result = self.query(request)
        if result.receipt.as_mapping() != receipt.as_mapping():
            raise DuckDBAnalyticsError("DuckDB replay 与原始收据不一致")
        return result


def _validate_sql(sql: str) -> None:
    lowered = sql.casefold()
    if ";" in sql or "--" in sql or "/*" in sql or "*/" in sql:
        raise DuckDBAnalyticsError("DuckDB 只接受无注释、无分号的单条查询")
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise DuckDBAnalyticsError("DuckDB 只接受 SELECT 或 WITH 查询")
    if _FORBIDDEN_SQL.search(sql) is not None:
        raise DuckDBAnalyticsError("DuckDB 查询包含禁止的写入、扩展或外部 I/O 语句")
    if _VOLATILE_SQL.search(sql) is not None:
        raise DuckDBAnalyticsError("DuckDB 查询包含不可回放的时间、随机或顺序敏感函数")


def _validate_query_plan(connection: object, sql: str, parameters: tuple[object, ...]) -> None:
    """用 DuckDB 的物理计划确认所有 base scan 都是受控 ``lake_data``。

    仅用正则匹配 ``FROM lake_data`` 会允许字符串伪装、系统 table function 或合成 range/VALUES
    relation。优化器关闭后生成的 JSON physical plan 保留所有 base scan；未知或非 Lake leaf 一律
    失败关闭。用户 SQL 的外层排序和行数限制由调用方生成，避免依赖用户文本中的 ``ORDER BY``。
    """

    if not hasattr(connection, "execute"):
        raise DuckDBAnalyticsError("DuckDB 连接不可用")
    execute = connection.execute
    execute("PRAGMA disable_optimizer")
    try:
        rows = execute(f"EXPLAIN (FORMAT JSON) {sql}", list(parameters)).fetchall()
    finally:
        execute("PRAGMA enable_optimizer")
    plan_text = next(
        (
            value
            for name, value in rows
            if name == "physical_plan" and isinstance(value, str)
        ),
        None,
    )
    if plan_text is None:
        raise DuckDBAnalyticsError("DuckDB 无法生成可验证的物理查询计划")
    try:
        roots = json.loads(plan_text)
    except json.JSONDecodeError as exc:
        raise DuckDBAnalyticsError("DuckDB 物理查询计划不是受支持的 JSON") from exc
    if not isinstance(roots, list) or not roots:
        raise DuckDBAnalyticsError("DuckDB 物理查询计划为空")
    seen_lake_scan = False

    def walk(node: object) -> None:
        nonlocal seen_lake_scan
        if not isinstance(node, dict):
            raise DuckDBAnalyticsError("DuckDB 物理查询计划节点无效")
        name = node.get("name")
        children = node.get("children")
        extra_info = node.get("extra_info")
        if not isinstance(name, str) or not isinstance(children, list):
            raise DuckDBAnalyticsError("DuckDB 物理查询计划节点字段无效")
        if name == "SEQ_SCAN":
            if not isinstance(extra_info, dict):
                raise DuckDBAnalyticsError("DuckDB Lake scan 缺少 table 身份")
            table = extra_info.get("Table")
            if not isinstance(table, str) or table.casefold() not in _ALLOWED_LAKE_SCAN_NAMES:
                raise DuckDBAnalyticsError("DuckDB 查询不得读取 lake_data 之外的 relation")
            seen_lake_scan = True
        elif not children and name != "CTE_SCAN":
            raise DuckDBAnalyticsError(
                f"DuckDB 查询包含非 Lake base scan：{name}"
            )
        for child in children:
            walk(child)

    for root in roots:
        walk(root)
    if not seen_lake_scan:
        raise DuckDBAnalyticsError("DuckDB 查询必须读取受控 lake_data relation")


def _parameter(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DuckDBAnalyticsError("DuckDB 参数不得为 NaN 或无穷值")
        return value
    raise DuckDBAnalyticsError("DuckDB 参数只支持 JSON 标量")


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _timestamp_literal(value: datetime) -> str:
    return "TIMESTAMPTZ '" + value.astimezone(UTC).isoformat().replace("'", "''") + "'"
