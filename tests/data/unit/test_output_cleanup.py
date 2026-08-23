"""下载缓存与临时文件清理的安全边界测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from northstar_quant.foundation.config.output_retention import OutputRetentionPolicy
from northstar_quant.data.artifacts.output_cleanup import (
    OutputCleanupSafetyError,
    cleanup_output_files,
    plan_output_cleanup,
)

_NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_runtime_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """清理边界始终读取强制保护根；测试注入隔离的无数据库运行时设置。"""

    fake_settings = SimpleNamespace(
        storage_dir=tmp_path / "storage",
        reports_dir=tmp_path / "reports",
        downloads_dir=tmp_path / "downloads",
    )
    monkeypatch.setattr(
        "northstar_quant.data.artifacts.output_cleanup.get_settings",
        lambda: fake_settings,
    )


def test_cleanup_dry_run_then_apply_only_removes_expired_download_cache_and_safe_tmp(
    tmp_path: Path,
):
    downloads_dir = tmp_path / "downloads"
    market_dir = tmp_path / "storage" / "market"
    reports_dir = tmp_path / "reports"
    primary_cache = downloads_dir / "akshare" / "cn" / "futures" / "1d" / "core.parquet"
    cache_manifest = primary_cache.with_suffix(".manifest.json")
    provider_cache = (
        downloads_dir / "akshare_actual_daily" / "market_bars" / "SHFE" / "bars.parquet"
    )
    temporary_file = downloads_dir / ".core.parquet.partial.tmp"
    blocked_cache = downloads_dir / "akshare" / "cn" / "futures" / "1d" / "publishing.parquet"
    publishing_marker = blocked_cache.with_suffix(".publishing.json")
    standard_market_data = market_dir / "core.parquet"
    standard_manifest = standard_market_data.with_suffix(".manifest.json")
    report_evidence = reports_dir / "backtest" / "report.md"

    for path in (
        primary_cache,
        cache_manifest,
        provider_cache,
        temporary_file,
        blocked_cache,
        publishing_marker,
        standard_market_data,
        standard_manifest,
        report_evidence,
    ):
        _write_old_file(path)

    policy = OutputRetentionPolicy(
        enabled=False,
        download_cache_retention_days=30,
        temporary_file_retention_days=7,
    )
    kwargs = {
        "downloads_dir": downloads_dir,
        "protected_roots": (market_dir, reports_dir),
        "now": _NOW,
    }

    preview = cleanup_output_files(policy, **kwargs)

    assert preview.mode == "dry_run"
    assert preview.deleted_paths == ()
    assert [target.kind for target in preview.plan.targets] == [
        "download_cache",
        "download_cache",
        "temporary_file",
    ]
    assert [file.relative_path for file in preview.plan.targets[0].files] == [
        "akshare/cn/futures/1d/core.parquet",
        "akshare/cn/futures/1d/core.manifest.json",
    ]
    assert preview.plan.blocked_publication_markers == (
        "akshare/cn/futures/1d/publishing.publishing.json",
    )
    assert all(
        path.exists() for path in (primary_cache, cache_manifest, provider_cache, temporary_file)
    )

    with pytest.raises(OutputCleanupSafetyError, match="disabled"):
        cleanup_output_files(replace(policy, enabled=False), apply=True, **kwargs)

    applied = cleanup_output_files(replace(policy, enabled=True), apply=True, **kwargs)

    assert applied.mode == "applied"
    assert set(applied.deleted_paths) == {
        "akshare/cn/futures/1d/core.parquet",
        "akshare/cn/futures/1d/core.manifest.json",
        "akshare_actual_daily/market_bars/SHFE/bars.parquet",
        ".core.parquet.partial.tmp",
    }
    assert not primary_cache.exists()
    assert not cache_manifest.exists()
    assert not provider_cache.exists()
    assert not temporary_file.exists()
    assert blocked_cache.exists()
    assert publishing_marker.exists()
    assert standard_market_data.exists()
    assert standard_manifest.exists()
    assert report_evidence.exists()


def test_cleanup_refuses_downloads_root_that_overlaps_standard_market_data(tmp_path: Path):
    market_dir = tmp_path / "storage" / "market"
    market_dir.mkdir(parents=True)
    policy = OutputRetentionPolicy(
        enabled=True,
        download_cache_retention_days=30,
        temporary_file_retention_days=7,
    )

    with pytest.raises(OutputCleanupSafetyError, match="重叠"):
        plan_output_cleanup(
            policy,
            downloads_dir=market_dir,
            protected_roots=(market_dir,),
            now=_NOW,
        )


def test_cleanup_default_protects_immutable_artifact_store(tmp_path: Path, monkeypatch) -> None:
    artifacts_dir = tmp_path / "storage" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    fake_settings = SimpleNamespace(
        storage_dir=tmp_path / "storage",
        reports_dir=tmp_path / "reports",
        downloads_dir=tmp_path / "downloads",
    )
    monkeypatch.setattr(
        "northstar_quant.data.artifacts.output_cleanup.get_settings",
        lambda: fake_settings,
    )
    policy = OutputRetentionPolicy(
        enabled=True,
        download_cache_retention_days=30,
        temporary_file_retention_days=7,
    )

    with pytest.raises(OutputCleanupSafetyError, match="重叠"):
        plan_output_cleanup(policy, downloads_dir=artifacts_dir, protected_roots=())


def test_cleanup_rejects_symbolic_link_root_before_resolving_it(tmp_path: Path) -> None:
    real_downloads = tmp_path / "real-downloads"
    real_downloads.mkdir()
    linked_downloads = tmp_path / "linked-downloads"
    try:
        linked_downloads.symlink_to(real_downloads, target_is_directory=True)
    except OSError:
        pytest.skip("当前运行环境不允许创建符号链接")
    policy = OutputRetentionPolicy(
        enabled=True,
        download_cache_retention_days=30,
        temporary_file_retention_days=7,
    )

    with pytest.raises(OutputCleanupSafetyError, match="符号链接"):
        plan_output_cleanup(
            policy,
            downloads_dir=linked_downloads,
            protected_roots=(),
            now=_NOW,
        )


def test_cleanup_never_removes_an_active_atomic_write_temporary_file(tmp_path: Path):
    downloads_dir = tmp_path / "downloads"
    active_parquet_tmp = downloads_dir / ".inflight.parquet.nonce.tmp"
    active_manifest_tmp = downloads_dir / ".inflight.manifest.json.nonce.tmp"
    publication_marker = downloads_dir / "inflight.publishing.json"
    for path in (active_parquet_tmp, active_manifest_tmp, publication_marker):
        _write_old_file(path)

    policy = OutputRetentionPolicy(
        enabled=True,
        download_cache_retention_days=30,
        temporary_file_retention_days=7,
    )
    kwargs = {
        "downloads_dir": downloads_dir,
        "protected_roots": (tmp_path / "storage" / "market", tmp_path / "reports"),
        "now": _NOW,
    }

    preview = cleanup_output_files(policy, **kwargs)

    assert preview.plan.targets == ()
    assert preview.plan.blocked_publication_markers == ("inflight.publishing.json",)
    applied = cleanup_output_files(policy, apply=True, **kwargs)
    assert applied.deleted_paths == ()
    assert active_parquet_tmp.exists()
    assert active_manifest_tmp.exists()


def test_cleanup_skips_symbolic_links_in_downloads_root(tmp_path: Path):
    downloads_dir = tmp_path / "downloads"
    external_file = tmp_path / "outside.parquet"
    _write_old_file(external_file)
    downloads_dir.mkdir()
    linked_file = downloads_dir / "linked.parquet"
    try:
        linked_file.symlink_to(external_file)
    except OSError:
        pytest.skip("当前运行环境不允许创建符号链接")

    policy = OutputRetentionPolicy(
        enabled=True,
        download_cache_retention_days=30,
        temporary_file_retention_days=7,
    )
    plan = plan_output_cleanup(
        policy,
        downloads_dir=downloads_dir,
        protected_roots=(tmp_path / "storage" / "market", tmp_path / "reports"),
        now=_NOW,
    )

    assert plan.targets == ()
    assert plan.skipped_unsafe_paths == ("linked.parquet",)
    assert external_file.exists()


def _write_old_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test", encoding="utf-8")
    old_ns = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    os.utime(path, ns=(old_ns, old_ns))
