"""Range decoding preserves exact values and refuses uncertain scope evidence."""

import io
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from northstar_quant.data_management.exploration.parquet import ResponseScan


def _file(*, statistics=True, foreign=False):
    table = pa.table(
        {
            "ts_code": [
                "OTHER.SHF" if foreign and day == 1 else "RB2610.SHF"
                for day in range(1, 5)
                for _ in range(512)
            ],
            "trade_time": [
                f"2026-09-{day:02d} {minute // 60:02d}:{minute % 60:02d}:00"
                for day in range(1, 5)
                for minute in range(512)
            ],
            "close": pa.array([Decimal("3100.123456789012")] * 2048, type=pa.decimal128(38, 12)),
        }
    )
    output = io.BytesIO()
    pq.write_table(
        table, output, row_group_size=512, compression="zstd", write_statistics=statistics
    )
    return output.getvalue()


def _scan(raw, start="2026-09-02", end="2026-09-02"):
    return ResponseScan(
        raw, row_count=2048, dataset="1min", scope="RB2610.SHF", start=start, end=end
    )


def test_range_prunes_decoding_but_keeps_whole_content_verification_explicit():
    raw = _file()
    narrow = _scan(raw)
    selected = list(narrow.rows())
    full = _scan(raw, "2026-09-01", "2026-09-04")
    expected = [r for r in full.rows() if r["trade_time"].startswith("2026-09-02")]
    assert selected == expected
    assert len(selected) == 512
    assert {r["close"] for r in selected} == {"3100.123456789012"}
    assert narrow.cost["row_groups_total"] == 4
    assert narrow.cost["row_groups_read"] == 1
    assert narrow.cost["rows_decoded"] == 512
    assert full.cost["rows_decoded"] == 2048
    assert narrow.cost["selected_compressed_bytes"] < full.cost["selected_compressed_bytes"]
    assert narrow.cost["verified_bytes"] == full.cost["verified_bytes"] == len(raw)
    empty = _scan(raw, "2026-10-01", "2026-10-01")
    assert list(empty.rows()) == []
    assert empty.cost["rows_decoded"] == 0


def test_missing_statistics_falls_back_and_foreign_scope_is_not_pruned_away():
    full = _scan(_file(statistics=False))
    assert len(list(full.rows())) == 512
    assert full.cost["rows_decoded"] == 2048
    with pytest.raises(ValueError, match="不属于"):
        _scan(_file(foreign=True))
    with pytest.raises(ValueError, match="不属于"):
        list(_scan(_file(foreign=True, statistics=False)).rows())
    with pytest.raises(ValueError, match="行数"):
        ResponseScan(
            _file(),
            row_count=1,
            dataset="1min",
            scope="RB2610.SHF",
            start="2026-09-02",
            end="2026-09-02",
        )
