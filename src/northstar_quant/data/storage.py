"""本地数据路径、Parquet 与 JSON manifest 的统一存取工具。

所有相对路径都相对项目根目录或 settings 指定的 storage 目录解析；本模块只负责文件
读写，不判断行情质量，质量验证必须由 data.schema 在写入前完成。
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import polars as pl

from northstar_quant.config.trading_profile import TradingProfile, load_trading_profile
from northstar_quant.config.settings import get_settings
from northstar_quant.data.schema import (
    SCHEMA_VERSION,
    to_signal_market_data,
    validate_market_dataset,
)


def _resolve_path(path: str | Path) -> Path:
    """把相对路径固定解析到项目根目录，避免命令工作目录改变数据位置。"""

    p = Path(path)
    if p.is_absolute():
        return p
    return get_settings().project_root / p


def save_parquet(df: pl.DataFrame, path: str | Path) -> Path:
    """以原子替换方式写入 Parquet；调用方必须先完成 schema 校验。

    新文件先完整写到目标目录中的临时文件，再用 ``os.replace`` 替换目标。因此写入
    中断时读取者仍会看到旧的完整文件，而不会读到半截 Parquet。这个保证仅覆盖单个
    文件；数据与 manifest 的发布顺序由下载模块控制。
    """

    path_obj = _resolve_path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _temporary_path(path_obj)
    try:
        df.write_parquet(temporary_path)
        os.replace(temporary_path, path_obj)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path_obj


def load_parquet(path: str | Path) -> pl.DataFrame:
    """读取 parquet 文件。"""

    path_obj = _resolve_path(path)
    return pl.read_parquet(path_obj)


def sha256_file(path: str | Path) -> str:
    """流式计算文件内容哈希，避免把大型行情文件整体读入内存。"""

    digest = hashlib.sha256()
    with _resolve_path(path).open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_config_sha256(profile: TradingProfile) -> str:
    """计算影响研究与执行语义的完整画像配置指纹。"""

    payload = json.dumps(
        asdict(profile),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def dataset_path(relative_path: str | Path) -> Path:
    """返回策略标准输入的 ``storage/market`` 路径，并确保父目录存在。"""

    settings = get_settings()
    path_obj = Path(relative_path)
    path = path_obj if path_obj.is_absolute() else settings.storage_dir / "market" / path_obj
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def market_data_path(filename: str) -> Path:
    return dataset_path(filename)


def downloads_path(relative_path: str | Path) -> Path:
    """返回下载缓存路径。"""

    settings = get_settings()
    path_obj = Path(relative_path)
    path = path_obj if path_obj.is_absolute() else settings.downloads_dir / path_obj
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def profile_market_data_path(profile: TradingProfile | str | None = None) -> Path:
    """根据交易画像解析其对应的数据文件路径。"""

    profile_obj = profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)
    return dataset_path(profile_obj.data.path)


def load_profile_market_data(profile: TradingProfile | str | None = None) -> pl.DataFrame:
    """读取并验证某个交易画像对应的市场数据制品。

    每次加载都会校验 schema、画像身份、行数和 Parquet 内容哈希。发布中标记、缺失
    manifest 或任何错配都会失败关闭，避免策略读取到半发布或被替换的数据。
    """

    profile_obj = profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)
    data_path = profile_market_data_path(profile_obj)
    marker_path = dataset_publication_marker_path(data_path)
    if marker_path.exists():
        raise RuntimeError(f"数据制品仍处于发布中，已拒绝读取：{data_path}")

    manifest_path = dataset_manifest_path(data_path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"数据 manifest 不存在，已拒绝读取：{manifest_path}")
    manifest = load_json(manifest_path)
    expected_hash = str(manifest.get("content_sha256") or "").strip().lower()
    if len(expected_hash) != 64:
        raise ValueError("数据 manifest 缺少有效的 content_sha256，请重新下载数据")
    actual_hash = sha256_file(data_path)
    if actual_hash != expected_hash:
        raise ValueError(
            f"数据内容哈希与 manifest 不一致，已拒绝读取：{data_path}"
        )

    df = load_parquet(data_path)
    validation = validate_market_dataset(profile_obj, df)
    identity_issues: list[str] = []
    if str(manifest.get("profile_id") or "") != profile_obj.profile_id:
        identity_issues.append("profile_id")
    if str(manifest.get("dataset_id") or "") != profile_obj.data.dataset_id:
        identity_issues.append("dataset_id")
    if str(manifest.get("schema", {}).get("schema_version") or "") != SCHEMA_VERSION:
        identity_issues.append("schema_version")
    if int(manifest.get("row_count", -1)) != df.height:
        identity_issues.append("row_count")
    if str(manifest.get("profile_config_sha256") or "") != profile_config_sha256(
        profile_obj
    ):
        identity_issues.append("profile_config_sha256")
    if identity_issues:
        raise ValueError(
            "数据 manifest 与当前画像或文件不一致："
            + ", ".join(identity_issues)
            + "；请重新下载并生成数据制品"
        )
    if validation["schema_version"] != SCHEMA_VERSION:
        raise ValueError("数据 schema 版本不受支持")
    return df


def load_profile_signal_data(profile: TradingProfile | str | None = None) -> pl.DataFrame:
    """读取标准行情并按画像 price_field 转换为策略价格口径。

    原始下载表保持不变；转换仅发生在内存中，从而保证研究可回溯到原始标准化数据。
    """

    profile_obj = profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)
    return to_signal_market_data(profile_obj, load_profile_market_data(profile_obj))


def profile_download_cache_path(
    profile: TradingProfile | str | None = None,
    provider: str | None = None,
) -> Path:
    """返回供应商下载缓存路径，按来源、市场、资产、频率和数据集 ID 隔离。"""

    profile_obj = profile if isinstance(profile, TradingProfile) else load_trading_profile(profile)
    resolved_provider = provider or profile_obj.data.download.provider or profile_obj.data.provider
    return downloads_path(
        Path(resolved_provider)
        / profile_obj.market.lower()
        / profile_obj.asset_type.lower()
        / profile_obj.data_frequency.lower()
        / f"{profile_obj.data.dataset_id}.parquet"
    )


def dataset_manifest_path(path: str | Path) -> Path:
    """返回数据文件对应的 manifest 路径。"""

    path_obj = _resolve_path(path)
    return path_obj.with_suffix(".manifest.json")


def dataset_publication_marker_path(path: str | Path) -> Path:
    """返回数据制品发布中标记路径。"""

    path_obj = _resolve_path(path)
    return path_obj.with_suffix(".publishing.json")


def save_json(payload: dict[str, Any], path: str | Path) -> Path:
    """以原子替换方式保存 JSON 文件，避免读取到半截 manifest。"""

    path_obj = _resolve_path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _temporary_path(path_obj)
    try:
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary_path, path_obj)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path_obj


def load_json(path: str | Path) -> dict[str, Any]:
    """读取 JSON 文件。"""

    path_obj = _resolve_path(path)
    return json.loads(path_obj.read_text(encoding="utf-8"))


def _temporary_path(path: Path) -> Path:
    """在目标文件同一目录创建临时路径，确保 ``os.replace`` 不跨文件系统。"""

    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    return Path(raw_path)
