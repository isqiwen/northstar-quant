"""DuckDB 只能读取已验证 Lake Parquet，且必须保持 PIT/只读边界。"""

from datetime import timedelta
from pathlib import Path

import polars as pl
import pytest

from northstar_quant.research.analytics import (
    DuckDBAnalyticsError,
    DuckDBQueryRequest,
    HistoricalLakeDuckDB,
)
from northstar_quant.data.lake.store import LakeIntegrityError
from tests.helpers.historical_lake import BASE_TIME, build_materialized_bars_lake


def test_duckdb_queries_verified_parquet_with_point_in_time_filter_and_replay_receipt(
    tmp_path: Path,
):
    fixture = build_materialized_bars_lake(tmp_path)
    analytics = HistoricalLakeDuckDB(fixture.lake_store)
    request = DuckDBQueryRequest(
        reference=fixture.materialized.verified.manifest.reference,
        sql="SELECT symbol, sum(price) AS total FROM lake_data GROUP BY symbol ORDER BY symbol",
        as_of=BASE_TIME + timedelta(minutes=30),
    )

    result = analytics.query(request)
    replay = analytics.verify_replay(request, result.receipt)

    assert result.frame.to_dicts() == [
        {"symbol": "CU", "total": 3.0},
        {"symbol": "RB", "total": 1.0},
    ]
    assert replay.receipt.result_sha256 == result.receipt.result_sha256
    assert result.receipt.lake_reference == fixture.materialized.verified.manifest.reference
    assert result.receipt.row_count == 2


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("DELETE FROM lake_data", "SELECT 或 WITH"),
        ("SELECT * FROM read_parquet('/tmp/untrusted.parquet')", "禁止"),
        ("SELECT * FROM lake_data; SELECT 1", "分号"),
    ],
)
def test_duckdb_rejects_writes_external_files_and_multiple_statements(
    tmp_path: Path,
    sql: str,
    expected: str,
):
    fixture = build_materialized_bars_lake(tmp_path)

    with pytest.raises(DuckDBAnalyticsError, match=expected):
        DuckDBQueryRequest(
            reference=fixture.materialized.verified.manifest.reference,
            sql=sql,
            as_of=BASE_TIME + timedelta(days=1),
        )


def test_duckdb_applies_deterministic_outer_order_for_multi_row_replayable_result(tmp_path: Path):
    fixture = build_materialized_bars_lake(tmp_path)
    request = DuckDBQueryRequest(
        reference=fixture.materialized.verified.manifest.reference,
        sql="SELECT symbol FROM lake_data",
        as_of=BASE_TIME + timedelta(days=1),
    )

    result = HistoricalLakeDuckDB(fixture.lake_store).query(request)

    assert result.frame.to_dicts() == [
        {"symbol": "CU"},
        {"symbol": "RB"},
        {"symbol": "RB"},
    ]


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("SELECT 'from lake_data' AS marker", "非 Lake base scan"),
        (
            "SELECT lake_data.symbol, name FROM lake_data CROSS JOIN duckdb_settings() ORDER BY name",
            "非 Lake base scan",
        ),
        (
            "WITH x AS (SELECT * FROM range(3)) "
            "SELECT * FROM lake_data CROSS JOIN x ORDER BY symbol",
            "非 Lake base scan",
        ),
        ("SELECT random() AS draw FROM lake_data", "不可回放"),
    ],
)
def test_duckdb_rejects_non_lake_or_non_replayable_query_bypasses(
    tmp_path: Path,
    sql: str,
    expected: str,
):
    fixture = build_materialized_bars_lake(tmp_path)

    with pytest.raises(DuckDBAnalyticsError, match=expected):
        request = DuckDBQueryRequest(
            reference=fixture.materialized.verified.manifest.reference,
            sql=sql,
            as_of=BASE_TIME + timedelta(days=1),
        )
        HistoricalLakeDuckDB(fixture.lake_store).query(request)


def test_duckdb_rechecks_lake_bytes_before_opening_query_snapshot(tmp_path: Path, monkeypatch):
    fixture = build_materialized_bars_lake(tmp_path)
    reference = fixture.materialized.verified.manifest.reference
    partition = fixture.materialized.verified.parquet_paths[0]
    replacement = tmp_path / "replacement.parquet"
    changed = pl.read_parquet(partition).with_columns((pl.col("price") + 1000).alias("price"))
    changed.write_parquet(replacement)
    original_verify = fixture.lake_store.verify

    def verify_then_replace(requested_reference):
        verified = original_verify(requested_reference)
        replacement.replace(partition)
        return verified

    monkeypatch.setattr(fixture.lake_store, "verify", verify_then_replace)
    request = DuckDBQueryRequest(
        reference=reference,
        sql="SELECT symbol, price FROM lake_data",
        as_of=BASE_TIME + timedelta(days=1),
    )

    with pytest.raises(LakeIntegrityError, match="查询分区 hash"):
        HistoricalLakeDuckDB(fixture.lake_store).query(request)
