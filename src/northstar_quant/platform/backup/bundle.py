"""创建并验证不含秘密元数据的 Northstar 备份包。

备份包是一个由受信任运维流程写入的目录。创建过程只接受明确的六类输入，
对每个进入包内的文件记录 SHA-256 和大小，并在最终发布前自行验证。它不读取
``.env``，也不接受任意目录的通配复制。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
from typing import Callable, Final, Iterable, Literal
from uuid import UUID, uuid4


BundleCategory = Literal[
    "postgresql",
    "config",
    "ontology",
    "run_manifest",
    "runtime_state",
    "release_metadata",
]

_BUNDLE_FORMAT_VERSION: Final = 1
_MANIFEST_NAME: Final = "manifest.json"
_ALL_CATEGORIES: Final[tuple[BundleCategory, ...]] = (
    "postgresql",
    "config",
    "ontology",
    "run_manifest",
    "runtime_state",
    "release_metadata",
)
_CATEGORY_SET: Final = frozenset(_ALL_CATEGORIES)
_ARCHIVE_SEGMENT_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RUNTIME_STATE_PATTERN: Final = re.compile(
    r"^brokers/(?:paper|ctp_sim)/[A-Za-z0-9][A-Za-z0-9_-]{0,63}/state\.json$"
)
_SECRET_NAME_PATTERN: Final = re.compile(
    r"(?:^|[._-])(?:env|secret|password|credential|token|api[_-]?key|private[_-]?key)(?:$|[._-])",
    re.IGNORECASE,
)
_SECRET_CONTENT_PATTERN: Final = re.compile(
    rb"(?:^|[\r\n])[ \t]*(?:[A-Za-z_]*?(?:password|secret|token|api[_-]?key)|"
    rb"NORTHSTAR_[A-Z0-9_]*?(?:PASSWORD|TOKEN|SECRET|KEY))[ \t]*[:=]"
    rb"|(?:^|[{\s,])[ \t]*\"(?:[A-Za-z_]*?(?:password|secret|token|api[_-]?key)|"
    rb"NORTHSTAR_[A-Z0-9_]*?(?:PASSWORD|TOKEN|SECRET|KEY))[ \t]*\"[ \t]*:",
    re.IGNORECASE,
)
_PRIVATE_KEY_PATTERN: Final = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_DATABASE_URL_PATTERN: Final = re.compile(rb"postgresql(?:\+[a-z0-9_-]+)?://[^\s]+", re.IGNORECASE)
_MAX_TEXT_ASSET_BYTES: Final = 16 * 1024 * 1024
_MAX_DATABASE_DUMP_BYTES: Final = 512 * 1024 * 1024 * 1024
_COPY_CHUNK_BYTES: Final = 1024 * 1024
_AT_FDCWD: Final = -100
_RENAME_NOREPLACE: Final = 1
_WINDOWS_FILE_EXISTS_ERRORS: Final = frozenset({80, 183})


class BackupBundleError(ValueError):
    """备份输入、包结构或完整性不满足受限契约。"""


@dataclass(frozen=True, slots=True)
class BackupBundleSources:
    """唯一允许进入备份包的持久化资产根路径。

    ``release_metadata_dir`` 必须是由调用方准备的、已去除环境文件的元数据快照；
    这样既可保留 release 身份和 systemd 快照，也不会把配置秘密带入包内。
    """

    database_dump: Path
    config_file: Path
    ontology_dir: Path
    reports_dir: Path
    storage_dir: Path
    release_metadata_dir: Path


@dataclass(frozen=True, slots=True)
class BackupBundle:
    """已完成并通过自身校验的包摘要。"""

    bundle_id: str
    path: Path
    created_at: str
    entry_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _PlannedFile:
    category: BundleCategory
    source: Path
    archive_path: PurePosixPath


@dataclass(frozen=True, slots=True)
class _Entry:
    category: BundleCategory
    archive_path: PurePosixPath
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "archive_path": self.archive_path.as_posix(),
            "category": self.category,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class _VerifiedBundle:
    bundle_id: str
    created_at: str
    entry_count: int
    manifest_sha256: str


def create_backup_bundle(
    sources: BackupBundleSources,
    *,
    output_parent: str | Path,
    bundle_id: str | None = None,
    now: datetime | None = None,
    pre_publish_check: Callable[[], None] | None = None,
) -> BackupBundle:
    """创建一个不可覆盖、完成前不可被验证为有效的备份包。

    该函数不会创建 ``output_parent``。调用方必须把它指向已存在、非符号链接、
    不允许 group/other 写入的目标目录；通常应是已挂载的外部备份介质。任何输入
    结构未知、文件变化、符号链接、意外秘密或最终名称已存在都会失败关闭。
    ``pre_publish_check`` lets a caller revalidate an external quiescence gate
    after all assets are staged but before the bundle becomes visible.
    """

    normalized_id = _normalize_bundle_id(bundle_id)
    created_at = _normalize_now(now)
    parent = _secure_existing_directory(Path(output_parent), "备份输出父目录")
    final_path = parent / f"northstar-backup-{normalized_id}"
    if final_path.exists() or final_path.is_symlink():
        raise BackupBundleError("备份包目标已存在；拒绝覆盖任何既有备份。")

    stage_path = parent / f".northstar-backup-{normalized_id}.{uuid4().hex}.partial"
    try:
        os.mkdir(stage_path, 0o700)
    except FileExistsError as exc:
        raise BackupBundleError("备份临时目录冲突；拒绝复用未知目录。") from exc
    except OSError as exc:
        raise BackupBundleError("无法创建私有备份临时目录。") from exc

    published = False
    try:
        entries = _copy_planned_files(sources, stage_path)
        manifest = _build_manifest(normalized_id, created_at, entries)
        _write_manifest(stage_path, manifest)
        _fsync_directory(stage_path)
        _verify_bundle_directory(stage_path)
        if pre_publish_check is not None:
            pre_publish_check()
        _publish_stage_no_replace(stage_path, final_path)
        _fsync_directory(parent)
        published = True
        verified = _verify_bundle_directory(final_path)
        return BackupBundle(
            bundle_id=normalized_id,
            path=final_path,
            created_at=created_at,
            entry_count=len(entries),
            sha256=verified.manifest_sha256,
        )
    finally:
        if not published and stage_path.exists() and not stage_path.is_symlink():
            _discard_private_stage(stage_path)


def _publish_stage_no_replace(stage_path: Path, final_path: Path) -> None:
    """Atomically publish a completed directory without ever replacing a target.

    ``os.rename`` replaces an existing target on POSIX, so it cannot be used for
    backup publication even when a preceding existence check succeeds.  Northstar
    supports Windows and Linux as development platforms; use each platform's
    no-replace primitive and fail closed everywhere else.
    """

    if os.name == "nt":
        _publish_windows_no_replace(stage_path, final_path)
        return
    if sys.platform == "linux":
        _publish_linux_no_replace(stage_path, final_path)
        return
    raise BackupBundleError("当前平台不支持无覆盖原子发布备份包。")


def _publish_windows_no_replace(stage_path: Path, final_path: Path) -> None:
    kernel32 = getattr(ctypes, "windll").kernel32
    move_file = kernel32.MoveFileExW
    move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move_file.restype = ctypes.c_int
    if move_file(str(stage_path), str(final_path), 0):
        return
    get_last_error = kernel32.GetLastError
    get_last_error.argtypes = []
    get_last_error.restype = ctypes.c_uint32
    error_code = int(get_last_error())
    if error_code in _WINDOWS_FILE_EXISTS_ERRORS:
        raise BackupBundleError("备份包目标在发布时出现；拒绝覆盖。")
    raise BackupBundleError("无法无覆盖地原子发布备份包。")


def _publish_linux_no_replace(stage_path: Path, final_path: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    rename_at2 = getattr(libc, "renameat2", None)
    if rename_at2 is None:
        raise BackupBundleError("当前 Linux 运行时不支持无覆盖原子发布备份包。")
    rename_at2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename_at2.restype = ctypes.c_int
    if (
        rename_at2(
            _AT_FDCWD,
            os.fsencode(stage_path),
            _AT_FDCWD,
            os.fsencode(final_path),
            _RENAME_NOREPLACE,
        )
        == 0
    ):
        return
    error_code = ctypes.get_errno()
    if error_code in {errno.EEXIST, errno.ENOTEMPTY}:
        raise BackupBundleError("备份包目标在发布时出现；拒绝覆盖。")
    raise BackupBundleError("无法无覆盖地原子发布备份包。")


def verify_backup_bundle(bundle_dir: str | Path) -> BackupBundle:
    """重新校验包清单、类型、文件集合、大小、哈希和无秘密文本资产约束。"""

    path = _secure_existing_directory(Path(bundle_dir), "备份包目录")
    verified = _verify_bundle_directory(path)
    return BackupBundle(
        bundle_id=verified.bundle_id,
        path=path,
        created_at=verified.created_at,
        entry_count=verified.entry_count,
        sha256=verified.manifest_sha256,
    )


def _copy_planned_files(sources: BackupBundleSources, stage_path: Path) -> list[_Entry]:
    planned = tuple(_plan_files(sources))
    seen = set[PurePosixPath]()
    entries: list[_Entry] = []
    for item in planned:
        _validate_archive_path(item.archive_path)
        if item.archive_path in seen:
            raise BackupBundleError("备份包包含重复的归档路径，已拒绝创建。")
        seen.add(item.archive_path)
        destination = stage_path.joinpath(*item.archive_path.parts)
        entries.append(_copy_one_file(item, destination))
    if not any(entry.category == "postgresql" for entry in entries):
        raise BackupBundleError("备份包缺少 PostgreSQL 自定义格式转储。")
    if not any(entry.category == "config" for entry in entries):
        raise BackupBundleError("备份包缺少活动非秘密配置。")
    if not any(entry.category == "ontology" for entry in entries):
        raise BackupBundleError("备份包缺少 ontology。")
    if not any(entry.category == "release_metadata" for entry in entries):
        raise BackupBundleError("备份包缺少 release metadata。")
    return sorted(entries, key=lambda entry: entry.archive_path.as_posix())


def _plan_files(sources: BackupBundleSources) -> Iterable[_PlannedFile]:
    database_dump = _regular_file(sources.database_dump, "PostgreSQL 转储")
    if database_dump.stat().st_size < 1:
        raise BackupBundleError("PostgreSQL 转储为空，已拒绝创建备份包。")
    yield _PlannedFile(
        category="postgresql",
        source=database_dump,
        archive_path=PurePosixPath("postgresql/database.dump"),
    )

    config_file = _regular_file(sources.config_file, "活动配置")
    if config_file.name != "app.yaml":
        raise BackupBundleError("备份配置必须是明确的 app.yaml 文件。")
    yield _PlannedFile(
        category="config",
        source=config_file,
        archive_path=PurePosixPath("config/app.yaml"),
    )

    ontology_dir = _secure_existing_directory(sources.ontology_dir, "ontology 目录")
    ontology_files = tuple(_iter_regular_files(ontology_dir))
    if not ontology_files:
        raise BackupBundleError("ontology 目录为空，已拒绝创建备份包。")
    for source in ontology_files:
        relative = source.relative_to(ontology_dir)
        if source.suffix.lower() not in {".yaml", ".yml"}:
            raise BackupBundleError("ontology 目录只允许 YAML 文件。")
        yield _PlannedFile(
            category="ontology",
            source=source,
            archive_path=PurePosixPath("ontology", *relative.parts),
        )

    reports_dir = _secure_existing_directory(sources.reports_dir, "报告目录")
    backtest_dir = reports_dir / "backtest"
    if backtest_dir.exists() or backtest_dir.is_symlink():
        backtest_dir = _secure_existing_directory(backtest_dir, "回测报告目录")
        for source in _iter_regular_files(backtest_dir):
            relative = source.relative_to(backtest_dir)
            if source.name != "manifest.json":
                continue
            yield _PlannedFile(
                category="run_manifest",
                source=source,
                archive_path=PurePosixPath("run-manifests", *relative.parts),
            )

    storage_dir = _secure_existing_directory(sources.storage_dir, "运行状态目录")
    brokers_dir = storage_dir / "brokers"
    if brokers_dir.exists() or brokers_dir.is_symlink():
        brokers_dir = _secure_existing_directory(brokers_dir, "券商运行状态目录")
        for source in _iter_regular_files(brokers_dir):
            runtime_relative = PurePosixPath("brokers", *source.relative_to(brokers_dir).parts)
            if not _RUNTIME_STATE_PATTERN.fullmatch(runtime_relative.as_posix()):
                continue
            yield _PlannedFile(
                category="runtime_state",
                source=source,
                archive_path=PurePosixPath("runtime-state", *runtime_relative.parts),
            )

    release_metadata_dir = _secure_existing_directory(
        sources.release_metadata_dir,
        "release metadata 目录",
    )
    metadata_files = tuple(_iter_regular_files(release_metadata_dir))
    if not metadata_files:
        raise BackupBundleError("release metadata 目录为空，已拒绝创建备份包。")
    for source in metadata_files:
        relative = source.relative_to(release_metadata_dir)
        if source.suffix.lower() not in {".json", ".txt", ".service"}:
            raise BackupBundleError("release metadata 目录包含未允许的文件类型。")
        yield _PlannedFile(
            category="release_metadata",
            source=source,
            archive_path=PurePosixPath("release-metadata", *relative.parts),
        )


def _copy_one_file(item: _PlannedFile, destination: Path) -> _Entry:
    source_stat = _regular_file(item.source, "备份输入文件").stat()
    maximum = _MAX_DATABASE_DUMP_BYTES if item.category == "postgresql" else _MAX_TEXT_ASSET_BYTES
    if source_stat.st_size > maximum:
        raise BackupBundleError("备份输入文件超过受限大小上限。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(destination.parent, 0o700)
    _assert_destination_parent(destination.parent)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        source_fd = os.open(item.source, flags)
    except OSError as exc:
        raise BackupBundleError("无法安全打开备份输入文件。") from exc
    try:
        opened_source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(opened_source_stat.st_mode):
            raise BackupBundleError("备份输入不是普通文件。")
        if opened_source_stat.st_size != source_stat.st_size:
            raise BackupBundleError("备份输入在复制前发生变化。")
        try:
            destination_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except OSError as exc:
            raise BackupBundleError("无法创建私有备份文件。") from exc
        digest = hashlib.sha256()
        captured = bytearray() if item.category != "postgresql" else None
        try:
            with os.fdopen(source_fd, "rb", closefd=False) as source_handle, os.fdopen(
                destination_fd,
                "wb",
                closefd=False,
            ) as destination_handle:
                while True:
                    chunk = source_handle.read(_COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
                    destination_handle.write(chunk)
                    if captured is not None:
                        captured.extend(chunk)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
        finally:
            os.close(destination_fd)
        final_source_stat = os.fstat(source_fd)
        if (
            final_source_stat.st_ino != opened_source_stat.st_ino
            or final_source_stat.st_size != opened_source_stat.st_size
            or final_source_stat.st_mtime_ns != opened_source_stat.st_mtime_ns
        ):
            raise BackupBundleError("备份输入在复制过程中发生变化。")
    finally:
        os.close(source_fd)
    copied_stat = _regular_file(destination, "备份输出文件").stat()
    if copied_stat.st_size != source_stat.st_size:
        raise BackupBundleError("备份输出文件大小与输入不一致。")
    if captured is not None:
        _reject_secret_bytes(bytes(captured), item.archive_path)
    return _Entry(
        category=item.category,
        archive_path=item.archive_path,
        sha256=digest.hexdigest(),
        size_bytes=copied_stat.st_size,
    )


def _build_manifest(bundle_id: str, created_at: str, entries: list[_Entry]) -> dict[str, object]:
    counts = {category: 0 for category in _ALL_CATEGORIES}
    for entry in entries:
        counts[entry.category] += 1
    return {
        "format_version": _BUNDLE_FORMAT_VERSION,
        "bundle_id": bundle_id,
        "created_at": created_at,
        "categories": counts,
        "entries": [entry.to_dict() for entry in entries],
    }


def _write_manifest(stage_path: Path, manifest: dict[str, object]) -> None:
    temporary = stage_path / f".{_MANIFEST_NAME}.{uuid4().hex}.partial"
    serialized = (json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    try:
        with open(temporary, "xb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, stage_path / _MANIFEST_NAME)
    except OSError as exc:
        raise BackupBundleError("无法写入备份包清单。") from exc


def _verify_bundle_directory(path: Path) -> _VerifiedBundle:
    manifest_path = path / _MANIFEST_NAME
    manifest_file = _regular_file(manifest_path, "备份包清单")
    if manifest_file.stat().st_size > _MAX_TEXT_ASSET_BYTES:
        raise BackupBundleError("备份包清单超过允许大小。")
    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupBundleError("备份包清单不是有效的 UTF-8 JSON。") from exc
    bundle_id, created_at, entries, counts = _parse_manifest(payload)
    expected_paths = {_MANIFEST_NAME}
    for entry in entries:
        expected_paths.add(entry.archive_path.as_posix())
    expected_directories = _archive_parent_directories(expected_paths)
    actual_files, actual_directories = _collect_regular_files_and_directories(path)
    actual_paths = {file.relative_to(path).as_posix() for file in actual_files}
    actual_directory_paths = {
        directory.relative_to(path).as_posix() for directory in actual_directories
    }
    if actual_paths != expected_paths or actual_directory_paths != expected_directories:
        raise BackupBundleError("备份包文件集合与清单不一致。")
    observed_counts = {category: 0 for category in _ALL_CATEGORIES}
    for entry in entries:
        file_path = path.joinpath(*entry.archive_path.parts)
        file = _regular_file(file_path, "备份包条目")
        file_stat = file.stat()
        if file_stat.st_size != entry.size_bytes:
            raise BackupBundleError("备份包条目大小与清单不一致。")
        digest = _sha256_file(file)
        if digest != entry.sha256:
            raise BackupBundleError("备份包条目哈希与清单不一致。")
        if entry.category != "postgresql":
            _reject_secret_bytes(file.read_bytes(), entry.archive_path)
        observed_counts[entry.category] += 1
    if observed_counts != counts:
        raise BackupBundleError("备份包分类计数与清单不一致。")
    if not observed_counts["postgresql"] or not observed_counts["config"]:
        raise BackupBundleError("备份包缺少必需的 PostgreSQL 或配置条目。")
    if not observed_counts["ontology"] or not observed_counts["release_metadata"]:
        raise BackupBundleError("备份包缺少必需的 ontology 或 release metadata 条目。")
    return _VerifiedBundle(
        bundle_id=bundle_id,
        created_at=created_at,
        entry_count=len(entries),
        manifest_sha256=_sha256_file(manifest_path),
    )


def _parse_manifest(payload: object) -> tuple[str, str, list[_Entry], dict[BundleCategory, int]]:
    if not isinstance(payload, dict) or set(payload) != {
        "format_version",
        "bundle_id",
        "created_at",
        "categories",
        "entries",
    }:
        raise BackupBundleError("备份包清单字段不完整或包含未知字段。")
    if payload["format_version"] != _BUNDLE_FORMAT_VERSION:
        raise BackupBundleError("备份包格式版本不受支持。")
    bundle_id = _normalize_bundle_id(payload["bundle_id"] if isinstance(payload["bundle_id"], str) else None)
    created_at = _normalize_timestamp(payload["created_at"])
    raw_counts = payload["categories"]
    if not isinstance(raw_counts, dict) or set(raw_counts) != _CATEGORY_SET:
        raise BackupBundleError("备份包分类计数不完整或包含未知分类。")
    counts: dict[BundleCategory, int] = {}
    for category in _ALL_CATEGORIES:
        value = raw_counts[category]
        if type(value) is not int or value < 0:
            raise BackupBundleError("备份包分类计数必须是非负整数。")
        counts[category] = value
    raw_entries = payload["entries"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise BackupBundleError("备份包清单必须包含至少一个条目。")
    entries: list[_Entry] = []
    seen_paths: set[PurePosixPath] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict) or set(raw_entry) != {
            "archive_path",
            "category",
            "sha256",
            "size_bytes",
        }:
            raise BackupBundleError("备份包条目字段不完整或包含未知字段。")
        raw_path = raw_entry["archive_path"]
        raw_category = raw_entry["category"]
        raw_sha256 = raw_entry["sha256"]
        raw_size = raw_entry["size_bytes"]
        if not isinstance(raw_path, str) or not isinstance(raw_category, str):
            raise BackupBundleError("备份包条目类型无效。")
        archive_path = PurePosixPath(raw_path)
        _validate_archive_path(archive_path)
        if archive_path in seen_paths:
            raise BackupBundleError("备份包清单包含重复归档路径。")
        seen_paths.add(archive_path)
        if raw_category not in _CATEGORY_SET:
            raise BackupBundleError("备份包条目分类未知。")
        if not isinstance(raw_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_sha256):
            raise BackupBundleError("备份包条目 SHA-256 无效。")
        if type(raw_size) is not int or raw_size < 0:
            raise BackupBundleError("备份包条目大小无效。")
        entries.append(
            _Entry(
                category=raw_category,  # type: ignore[arg-type]
                archive_path=archive_path,
                sha256=raw_sha256,
                size_bytes=raw_size,
            )
        )
    if [entry.archive_path.as_posix() for entry in entries] != sorted(
        entry.archive_path.as_posix() for entry in entries
    ):
        raise BackupBundleError("备份包条目必须按归档路径排序。")
    return bundle_id, created_at, entries, counts


def _normalize_bundle_id(value: str | None) -> str:
    candidate = value or str(uuid4())
    try:
        parsed = UUID(candidate)
    except (TypeError, ValueError) as exc:
        raise BackupBundleError("备份包标识必须是 UUID。") from exc
    return str(parsed)


def _normalize_now(value: datetime | None) -> str:
    instant = datetime.now(timezone.utc) if value is None else value
    if instant.tzinfo is None:
        raise BackupBundleError("备份包时间必须带有时区。")
    return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BackupBundleError("备份包时间必须是以 Z 结尾的 UTC ISO-8601。")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise BackupBundleError("备份包时间不是有效 UTC ISO-8601。") from exc
    if parsed.tzinfo != timezone.utc:
        raise BackupBundleError("备份包时间必须使用 UTC。")
    return parsed.isoformat().replace("+00:00", "Z")


def _secure_existing_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise BackupBundleError(f"{label}不能是符号链接。")
    try:
        resolved = path.resolve(strict=True)
        mode = path.lstat().st_mode
    except OSError as exc:
        raise BackupBundleError(f"{label}不存在或无法安全读取。") from exc
    if not stat.S_ISDIR(mode):
        raise BackupBundleError(f"{label}必须是目录。")
    if os.name == "posix" and mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise BackupBundleError(f"{label}不能允许 group 或 other 写入。")
    return resolved


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise BackupBundleError(f"{label}不能是符号链接。")
    try:
        resolved = path.resolve(strict=True)
        mode = path.lstat().st_mode
    except OSError as exc:
        raise BackupBundleError(f"{label}不存在或无法安全读取。") from exc
    if not stat.S_ISREG(mode):
        raise BackupBundleError(f"{label}必须是普通文件。")
    return resolved


def _iter_regular_files(
    root: Path,
) -> Iterable[Path]:
    """遍历普通文件；任何符号链接或特殊节点都使整包失败。"""

    files, _ = _collect_regular_files_and_directories(root)
    yield from files


def _collect_regular_files_and_directories(root: Path) -> tuple[list[Path], list[Path]]:
    """Read a complete tree once while rejecting every non-regular node."""

    pending = [root]
    files: list[Path] = []
    directories: list[Path] = []
    while pending:
        directory = pending.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise BackupBundleError("无法遍历备份输入目录。") from exc
        for child in children:
            try:
                child_stat = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise BackupBundleError("无法安全读取备份输入目录项。") from exc
            if stat.S_ISLNK(child_stat.st_mode):
                raise BackupBundleError("备份输入目录包含符号链接。")
            child_path = Path(child.path)
            if stat.S_ISDIR(child_stat.st_mode):
                directories.append(child_path)
                pending.append(child_path)
                continue
            if not stat.S_ISREG(child_stat.st_mode):
                raise BackupBundleError("备份输入目录包含特殊文件。")
            files.append(child_path)
    return files, directories


def _archive_parent_directories(archive_paths: Iterable[str]) -> set[str]:
    directories: set[str] = set()
    for archive_path in archive_paths:
        for parent in PurePosixPath(archive_path).parents:
            if parent == PurePosixPath("."):
                break
            directories.add(parent.as_posix())
    return directories


def _validate_archive_path(path: PurePosixPath) -> None:
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupBundleError("备份归档路径无效。")
    if any(not _ARCHIVE_SEGMENT_PATTERN.fullmatch(part) for part in path.parts):
        raise BackupBundleError("备份归档路径包含不允许的字符。")
    if any(_SECRET_NAME_PATTERN.search(part) for part in path.parts):
        raise BackupBundleError("备份归档路径看起来包含秘密文件名。")


def _assert_destination_parent(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise BackupBundleError("无法验证备份输出父目录。") from exc
    if path.is_symlink() or not stat.S_ISDIR(mode):
        raise BackupBundleError("备份输出父目录不安全。")
    if os.name == "posix" and mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise BackupBundleError("备份输出父目录不能允许 group 或 other 写入。")


def _reject_secret_bytes(value: bytes, archive_path: PurePosixPath) -> None:
    if _SECRET_CONTENT_PATTERN.search(value) or _PRIVATE_KEY_PATTERN.search(value):
        raise BackupBundleError(f"备份条目 {archive_path.as_posix()} 疑似包含秘密，已拒绝写入。")
    if _DATABASE_URL_PATTERN.search(value):
        raise BackupBundleError(f"备份条目 {archive_path.as_posix()} 疑似包含数据库 URL，已拒绝写入。")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(_COPY_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise BackupBundleError("无法读取备份包条目计算哈希。") from exc
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        raise BackupBundleError("无法同步备份目录。") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise BackupBundleError("无法同步备份目录。") from exc
    finally:
        os.close(descriptor)


def _discard_private_stage(path: Path) -> None:
    """只清理本次刚创建的私有临时目录，绝不触及最终包或输入。"""

    try:
        shutil.rmtree(path)
    except OSError:
        # 失败时宁可留下不可验证的 .partial 目录，也不能扩大清理目标。
        pass
