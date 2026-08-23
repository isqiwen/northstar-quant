"""数据下载发布安全性的回归测试。"""

from datetime import date, timedelta

import polars as pl

from northstar_quant.data.sources.downloader import _quality_regression_issues, _symbol_quality_summary
from northstar_quant.data.artifacts.storage import load_json, load_parquet, save_json, save_parquet


def test_storage_writes_replace_complete_files_without_leaking_temporary_files(tmp_path):
    """Parquet 与 manifest 均从同目录临时文件原子替换，成功后不残留临时文件。"""

    parquet_path = tmp_path / "continuous.parquet"
    manifest_path = tmp_path / "continuous.manifest.json"

    save_parquet(pl.DataFrame({"value": [1, 2]}), parquet_path)
    save_json({"row_count": 2}, manifest_path)

    assert load_parquet(parquet_path).to_dict(as_series=False) == {"value": [1, 2]}
    assert load_json(manifest_path) == {"row_count": 2}
    assert list(tmp_path.glob("*.tmp")) == []


def test_quality_summary_detects_history_shrinkage_before_publication():
    """供应商只返回少量近期 bar 时，必须阻止它覆盖完整的历史数据。"""

    start = date(2024, 1, 2)
    complete = pl.DataFrame(
        {
            "date": [start + timedelta(days=offset) for offset in range(20)],
            "symbol": ["RB_CONT"] * 20,
        }
    )
    incomplete = complete.tail(5)
    previous_manifest = {"quality": {"symbols": _symbol_quality_summary(complete)}}

    issues = _quality_regression_issues(previous_manifest, _symbol_quality_summary(incomplete))

    assert any("起始日期" in issue for issue in issues)
    assert any("行数" in issue for issue in issues)
