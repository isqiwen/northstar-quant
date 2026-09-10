"""Prune verified supplier Parquet row groups without changing row or source meaning."""

import io
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from ..tushare import normalization


def day_label(value: str) -> str:
    return (
        datetime.strptime(value[:10], "%Y-%m-%d").date().isoformat()
        if "-" in value
        else datetime.strptime(value, "%Y%m%d").date().isoformat()
    )


class ResponseScan:
    def __init__(
        self, raw: bytes, *, row_count: int, dataset: str, scope: str, start: str, end: str
    ) -> None:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        self.parquet = pq.ParquetFile(io.BytesIO(raw))
        if self.parquet.metadata.num_rows != row_count or row_count > 10000:
            raise ValueError("Parquet 行数与固定版本不一致或超过单分片上限")
        if len(self.parquet.schema.names) > 64:
            raise ValueError("发布字段超过浏览上限")
        self.scope, self.start, self.end = scope, start, end
        self.clock = (
            "end_date"
            if dataset in ("week", "month")
            else ("trade_time" if "trade_time" in self.parquet.schema.names else "trade_date")
        )
        self.groups = []
        self.cost = {
            "verified_bytes": len(raw),
            "row_groups_total": self.parquet.num_row_groups,
            "row_groups_read": 0,
            "rows_decoded": 0,
            "selected_compressed_bytes": 0,
        }
        names = self.parquet.schema.names
        for index in range(self.parquet.num_row_groups):
            group = self.parquet.metadata.row_group(index)
            # Missing/nullable statistics cannot establish a pruning proof.
            stats = (
                group.column(names.index(self.clock)).statistics if self.clock in names else None
            )
            scope_stats = (
                group.column(names.index("ts_code")).statistics if "ts_code" in names else None
            )
            if (
                scope_stats is not None
                and scope_stats.has_min_max
                and (scope_stats.min != scope or scope_stats.max != scope)
            ):
                raise ValueError("发布记录不属于所选合约")
            if (
                stats is not None
                and stats.has_min_max
                and stats.null_count == 0
                and scope_stats is not None
                and scope_stats.has_min_max
                and scope_stats.null_count == 0
            ):
                try:
                    first, last = day_label(stats.min), day_label(stats.max)
                except (ValueError, TypeError):
                    pass  # Decode and reject malformed labels through the ordinary row path.
                else:
                    if last < start or first > end:
                        continue
            self.groups.append(index)
            self.cost["selected_compressed_bytes"] += sum(
                group.column(c).total_compressed_size for c in range(group.num_columns)
            )
        self.cost["row_groups_read"] = len(self.groups)

    def rows(self) -> Iterator[dict[str, Any]]:
        for batch in self.parquet.iter_batches(batch_size=256, row_groups=self.groups):
            self.cost["rows_decoded"] += batch.num_rows
            for raw_row in batch.to_pylist():
                row = normalization.response_row(raw_row)
                if row.get("ts_code") != self.scope:
                    raise ValueError("发布记录不属于所选合约")
                clock = row.get(self.clock)
                if not isinstance(clock, str):
                    raise ValueError("发布记录缺少供应商时间标签")
                if self.start <= day_label(clock) <= self.end:
                    yield row
