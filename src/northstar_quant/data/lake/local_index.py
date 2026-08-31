"""SQLite-backed local discovery index for verified Parquet Lake manifests.

This module deliberately owns only disposable discovery metadata.  It is not a
Lake trust root: consumers must still call :meth:`ParquetLakeStore.verify` before
reading a Lake version.  It has no relationship to the core PostgreSQL runtime
database, Alembic, or trading state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time

from northstar_quant.data.lake.models import (
    LakeContractError,
    LakeDatasetKind,
    LakeDatasetReference,
)
from northstar_quant.data.lake.store import LakeStoreError, ParquetLakeStore
from northstar_quant.foundation.platform_support import require_linux_x86_64


_DATABASE_FILENAME = "lake-manifest-index.sqlite3"
_SCHEMA_VERSION = 1
_SQLITE_BOOTSTRAP_RETRY_DELAY_SECONDS = 0.05
_SQLITE_BOOTSTRAP_RETRY_TIMEOUT_SECONDS = 5.0


class LakeLocalIndexError(RuntimeError):
    """本地 SQLite 索引的路径、完整性或并发协议不满足。"""


class LakeLocalIndexCorruptionError(LakeLocalIndexError):
    """SQLite 文件损坏；只有显式 rebuild 可隔离旧文件后恢复。"""


@dataclass(frozen=True, slots=True)
class LocalLakeIndexEntry:
    """一条可重建的 Lake manifest discovery 记录，不能证明数据有效。"""

    reference: LakeDatasetReference
    manifest_relative_path: str
    manifest_sha256: str
    upstream_dataset_version_hash: str
    upstream_artifact_content_hash: str
    minimum_available_at: datetime
    maximum_available_at: datetime
    indexed_at: datetime

    def as_mapping(self) -> dict[str, object]:
        return {
            "indexed_at": self.indexed_at.isoformat(),
            "manifest_relative_path": self.manifest_relative_path,
            "manifest_sha256": self.manifest_sha256,
            "maximum_available_at": self.maximum_available_at.isoformat(),
            "minimum_available_at": self.minimum_available_at.isoformat(),
            "reference": self.reference.as_mapping(),
            "upstream_artifact_content_hash": self.upstream_artifact_content_hash,
            "upstream_dataset_version_hash": self.upstream_dataset_version_hash,
        }


@dataclass(frozen=True, slots=True)
class LocalLakeIndexRebuild:
    """一次显式 rebuild 的非权威本地工具收据。"""

    database_path: Path
    entry_count: int
    generation_id: int
    indexed_at: datetime

    def as_mapping(self) -> dict[str, object]:
        return {
            "database_path": str(self.database_path),
            "entry_count": self.entry_count,
            "generation_id": self.generation_id,
            "indexed_at": self.indexed_at.isoformat(),
            "non_authoritative": True,
        }


class LakeManifestLocalIndex:
    """位于 ``<storage_dir>/local-tools`` 的显式、可重建 SQLite index。"""

    def __init__(self, root: str | Path) -> None:
        require_linux_x86_64()
        candidate = Path(root).expanduser()
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise LakeLocalIndexError("SQLite Local-tools root 必须是无 '..' 的绝对路径")
        _ensure_private_root(candidate)
        self._root = candidate
        self._database_path = self._root / _DATABASE_FILENAME

    @classmethod
    def from_settings(cls) -> "LakeManifestLocalIndex":
        """只从运行存储目录派生 tool-owned 路径，不读取核心数据库设置。"""

        from northstar_quant.foundation.config.settings import get_settings

        return cls(get_settings().local_tools_dir)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def database_path(self) -> Path:
        return self._database_path

    def rebuild(self, lake_store: ParquetLakeStore) -> LocalLakeIndexRebuild:
        """扫描并逐份验证 Lake，再原子追加一个新的本地 index generation。

        在所有 Lake version 验证完成前不会写 SQLite；已有 generation 永不被清空或作为
        DuckDB 输入。SQLite 损坏时，本方法只隔离这个固定 tool-owned 文件并重建空索引。
        """

        if not isinstance(lake_store, ParquetLakeStore):
            raise LakeLocalIndexError("lake_store 必须是 ParquetLakeStore")
        indexed_at = datetime.now(UTC)
        entries = self._collect_verified_entries(lake_store, indexed_at=indexed_at)
        connection = self._open_initialized_rebuild_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO lake_manifest_index_rebuilds (
                        schema_version, indexed_at, lake_root, verified_entry_count
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        _SCHEMA_VERSION,
                        indexed_at.isoformat(),
                        str(lake_store.root),
                        len(entries),
                    ),
                )
                generation_id = cursor.lastrowid
                if not isinstance(generation_id, int) or generation_id < 1:
                    raise LakeLocalIndexError("SQLite rebuild generation 身份无效")
                connection.executemany(
                    """
                    INSERT INTO lake_manifest_index_entries (
                        generation_id, kind, dataset_id, version_hash,
                        manifest_relative_path, manifest_sha256,
                        upstream_dataset_version_hash, upstream_artifact_content_hash,
                        minimum_available_at, maximum_available_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            generation_id,
                            entry.reference.kind.value,
                            entry.reference.dataset_id,
                            entry.reference.version_hash,
                            entry.manifest_relative_path,
                            entry.manifest_sha256,
                            entry.upstream_dataset_version_hash,
                            entry.upstream_artifact_content_hash,
                            entry.minimum_available_at.isoformat(),
                            entry.maximum_available_at.isoformat(),
                        )
                        for entry in entries
                    ],
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.Error as exc:
            raise LakeLocalIndexError("SQLite Local-tools index rebuild 失败") from exc
        finally:
            connection.close()
        return LocalLakeIndexRebuild(
            database_path=self._database_path,
            entry_count=len(entries),
            generation_id=generation_id,
            indexed_at=indexed_at,
        )

    def _open_initialized_rebuild_connection(self) -> sqlite3.Connection:
        """打开可写 index，并在短暂 bootstrap 锁竞争时重试。

        新建 SQLite 文件时，``quick_check`` 与 ``journal_mode=WAL`` 的锁升级会与另一
        个同时启动的 local-tool 竞争。每次尝试都关闭连接后再重试，避免保留读取锁；真正
        的 generation 写入仍由后续的 ``BEGIN IMMEDIATE`` 串行化。
        """

        deadline = time.monotonic() + _SQLITE_BOOTSTRAP_RETRY_TIMEOUT_SECONDS
        while True:
            try:
                return self._open_initialized_rebuild_connection_once()
            except LakeLocalIndexError as exc:
                if not _has_sqlite_lock_cause(exc):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(_SQLITE_BOOTSTRAP_RETRY_DELAY_SECONDS, remaining))

    def _open_initialized_rebuild_connection_once(self) -> sqlite3.Connection:
        """执行一次可写 index bootstrap；锁竞争由调用方有界重试。"""

        connection = self._open_connection(recover_corruption=True, create=True)
        try:
            self._initialize_schema(connection)
        except (LakeLocalIndexCorruptionError, sqlite3.DatabaseError) as exc:
            connection.close()
            if isinstance(exc, sqlite3.DatabaseError) and _is_lock_error(exc):
                raise LakeLocalIndexError("SQLite Local-tools index 正被其他本地工具使用") from exc
            self._quarantine_corrupt_database()
            connection = self._open_connection(recover_corruption=False, create=True)
            try:
                self._initialize_schema(connection)
            except (LakeLocalIndexCorruptionError, sqlite3.DatabaseError) as recovery_exc:
                connection.close()
                raise LakeLocalIndexError("无法初始化 SQLite Local-tools index") from recovery_exc
        return connection

    def list_entries(
        self,
        *,
        kind: LakeDatasetKind | None = None,
        dataset_id: str | None = None,
    ) -> tuple[LocalLakeIndexEntry, ...]:
        """列出最新 rebuild generation 的元数据；不把索引当作验证结果。"""

        if kind is not None and not isinstance(kind, LakeDatasetKind):
            raise LakeLocalIndexError("kind 必须是 LakeDatasetKind 或 None")
        normalized_dataset_id = _dataset_id(dataset_id) if dataset_id is not None else None
        self._assert_database_path()
        if not self._database_path.exists():
            return ()
        connection = self._open_connection(recover_corruption=False, create=False)
        try:
            self._assert_schema(connection)
            generation = connection.execute(
                """
                SELECT generation_id, schema_version, indexed_at
                FROM lake_manifest_index_rebuilds
                ORDER BY generation_id DESC
                LIMIT 1
                """
            ).fetchone()
            if generation is None:
                return ()
            generation_id, schema_version, indexed_at_text = generation
            if (
                not isinstance(generation_id, int)
                or schema_version != _SCHEMA_VERSION
                or not isinstance(indexed_at_text, str)
            ):
                raise LakeLocalIndexCorruptionError("SQLite rebuild generation 记录无效")
            predicates = ["generation_id = ?"]
            parameters: list[object] = [generation_id]
            if kind is not None:
                predicates.append("kind = ?")
                parameters.append(kind.value)
            if normalized_dataset_id is not None:
                predicates.append("dataset_id = ?")
                parameters.append(normalized_dataset_id)
            rows = connection.execute(
                """
                SELECT
                    kind, dataset_id, version_hash,
                    manifest_relative_path, manifest_sha256,
                    upstream_dataset_version_hash, upstream_artifact_content_hash,
                    minimum_available_at, maximum_available_at
                FROM lake_manifest_index_entries
                WHERE """
                + " AND ".join(predicates)
                + " ORDER BY kind, dataset_id, version_hash",
                parameters,
            ).fetchall()
        except sqlite3.Error as exc:
            raise LakeLocalIndexError("SQLite Local-tools index 读取失败") from exc
        finally:
            connection.close()
        indexed_at = _datetime(indexed_at_text, "SQLite indexed_at")
        return tuple(
            _entry_from_row(row, indexed_at=indexed_at)
            for row in rows
        )

    def _collect_verified_entries(
        self,
        lake_store: ParquetLakeStore,
        *,
        indexed_at: datetime,
    ) -> tuple[LocalLakeIndexEntry, ...]:
        datasets_root = lake_store.root / "datasets"
        if not datasets_root.exists():
            return ()
        if datasets_root.is_symlink() or not datasets_root.is_dir():
            raise LakeLocalIndexError("Lake datasets root 必须是普通目录")
        entries: list[LocalLakeIndexEntry] = []
        try:
            kind_directories = sorted(datasets_root.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise LakeLocalIndexError("无法扫描 Lake datasets root") from exc
        for kind_directory in kind_directories:
            if kind_directory.is_symlink() or not kind_directory.is_dir():
                raise LakeLocalIndexError("Lake kind 目录必须是普通目录")
            try:
                kind = LakeDatasetKind(kind_directory.name)
            except ValueError as exc:
                raise LakeLocalIndexError(f"Lake kind 不受支持：{kind_directory.name}") from exc
            try:
                dataset_directories = sorted(kind_directory.iterdir(), key=lambda path: path.name)
            except OSError as exc:
                raise LakeLocalIndexError("无法扫描 Lake dataset 目录") from exc
            for dataset_directory in dataset_directories:
                if dataset_directory.is_symlink() or not dataset_directory.is_dir():
                    raise LakeLocalIndexError("Lake dataset 目录必须是普通目录")
                try:
                    version_directories = sorted(dataset_directory.iterdir(), key=lambda path: path.name)
                except OSError as exc:
                    raise LakeLocalIndexError("无法扫描 Lake version 目录") from exc
                for version_directory in version_directories:
                    if version_directory.is_symlink() or not version_directory.is_dir():
                        raise LakeLocalIndexError("Lake version 目录必须是普通目录")
                    try:
                        reference = LakeDatasetReference(
                            kind=kind,
                            dataset_id=dataset_directory.name,
                            version_hash=version_directory.name,
                        )
                        verified = lake_store.verify(reference)
                    except (LakeContractError, LakeStoreError) as exc:
                        raise LakeLocalIndexError("Lake manifest 无法验证，拒绝更新本地索引") from exc
                    try:
                        relative_manifest_path = verified.manifest_path.relative_to(lake_store.root)
                    except ValueError as exc:
                        raise LakeLocalIndexError("Lake manifest 路径越出 Lake root") from exc
                    manifest = verified.manifest
                    entries.append(
                        LocalLakeIndexEntry(
                            reference=reference,
                            manifest_relative_path=relative_manifest_path.as_posix(),
                            manifest_sha256=verified.manifest_sha256,
                            upstream_dataset_version_hash=manifest.upstream_dataset_version_hash,
                            upstream_artifact_content_hash=manifest.upstream_artifact_content_hash,
                            minimum_available_at=manifest.minimum_available_at,
                            maximum_available_at=manifest.maximum_available_at,
                            indexed_at=indexed_at,
                        )
                    )
        return tuple(sorted(entries, key=lambda entry: _entry_sort_key(entry.reference)))

    def _open_connection(
        self,
        *,
        recover_corruption: bool,
        create: bool,
    ) -> sqlite3.Connection:
        self._assert_database_path()
        if not create and not self._database_path.exists():
            raise LakeLocalIndexError("SQLite Local-tools index 尚未 rebuild")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=5.0,
                isolation_level=None,
            )
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check != ("ok",):
                raise sqlite3.DatabaseError("SQLite quick_check failed")
            connection.execute("PRAGMA journal_mode = WAL")
            self._database_path.chmod(0o600)
            return connection
        except sqlite3.DatabaseError as exc:
            if connection is not None:
                connection.close()
            if _is_lock_error(exc):
                raise LakeLocalIndexError("SQLite Local-tools index 正被其他本地工具使用") from exc
            if not recover_corruption:
                raise LakeLocalIndexCorruptionError(
                    "SQLite Local-tools index 损坏；请显式执行 lake-index rebuild 隔离并重建"
                ) from exc
            self._quarantine_corrupt_database()
            return self._open_connection(recover_corruption=False, create=create)
        except OSError as exc:
            if connection is not None:
                connection.close()
            raise LakeLocalIndexError("SQLite Local-tools index 文件权限或路径不可用") from exc

    def _initialize_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS lake_manifest_index_rebuilds (
                generation_id INTEGER PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                indexed_at TEXT NOT NULL,
                lake_root TEXT NOT NULL,
                verified_entry_count INTEGER NOT NULL CHECK (verified_entry_count >= 0)
            );
            CREATE TABLE IF NOT EXISTS lake_manifest_index_entries (
                generation_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                version_hash TEXT NOT NULL,
                manifest_relative_path TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                upstream_dataset_version_hash TEXT NOT NULL,
                upstream_artifact_content_hash TEXT NOT NULL,
                minimum_available_at TEXT NOT NULL,
                maximum_available_at TEXT NOT NULL,
                PRIMARY KEY (generation_id, kind, dataset_id, version_hash),
                FOREIGN KEY (generation_id)
                    REFERENCES lake_manifest_index_rebuilds(generation_id)
            );
            CREATE INDEX IF NOT EXISTS idx_lake_manifest_index_latest_lookup
                ON lake_manifest_index_entries (generation_id, kind, dataset_id, version_hash);
            """
        )
        self._assert_schema(connection)

    def _assert_schema(self, connection: sqlite3.Connection) -> None:
        expected = {
            "lake_manifest_index_rebuilds": {
                "generation_id",
                "schema_version",
                "indexed_at",
                "lake_root",
                "verified_entry_count",
            },
            "lake_manifest_index_entries": {
                "generation_id",
                "kind",
                "dataset_id",
                "version_hash",
                "manifest_relative_path",
                "manifest_sha256",
                "upstream_dataset_version_hash",
                "upstream_artifact_content_hash",
                "minimum_available_at",
                "maximum_available_at",
            },
        }
        for table, columns in expected.items():
            pragma_rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            observed = {row[1] for row in pragma_rows if len(row) > 1 and isinstance(row[1], str)}
            if observed != columns:
                raise LakeLocalIndexCorruptionError(
                    f"SQLite Local-tools index schema 不受支持：{table}"
                )

    def _assert_database_path(self) -> None:
        _ensure_private_root(self._root)
        try:
            state = self._database_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
            raise LakeLocalIndexError("SQLite Local-tools index 必须是普通 tool-owned 文件")
        if state.st_uid != os.getuid():
            raise LakeLocalIndexError("SQLite Local-tools index 必须由当前服务用户拥有")

    def _quarantine_corrupt_database(self) -> None:
        """只隔离固定 SQLite index 及其 sidecar，保留原始字节供人工检查。"""

        self._assert_database_path()
        if not self._database_path.exists():
            return
        suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + secrets.token_hex(4)
        quarantine_base = self._root / f"{_DATABASE_FILENAME}.corrupt-{suffix}"
        try:
            os.replace(self._database_path, quarantine_base)
            for sidecar_suffix in ("-wal", "-shm"):
                sidecar = self._root / f"{_DATABASE_FILENAME}{sidecar_suffix}"
                if sidecar.exists():
                    sidecar_state = sidecar.lstat()
                    if stat.S_ISLNK(sidecar_state.st_mode) or not stat.S_ISREG(sidecar_state.st_mode):
                        raise LakeLocalIndexError("SQLite Local-tools sidecar 必须是普通文件")
                    os.replace(sidecar, self._root / f"{quarantine_base.name}{sidecar_suffix}")
        except OSError as exc:
            raise LakeLocalIndexError("无法隔离损坏的 SQLite Local-tools index") from exc


def _ensure_private_root(path: Path) -> None:
    _assert_no_symlink_ancestors(path)
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise LakeLocalIndexError("SQLite Local-tools root 必须是普通目录，不能是符号链接")
    state = path.lstat()
    if state.st_uid != os.getuid():
        raise LakeLocalIndexError("SQLite Local-tools root 必须由当前服务用户拥有")
    if stat.S_IMODE(state.st_mode) & 0o077:
        raise LakeLocalIndexError("SQLite Local-tools root 不得向 group 或 other 开放访问")


def _assert_no_symlink_ancestors(path: Path) -> None:
    parts = path.parts
    if not parts:
        raise LakeLocalIndexError("SQLite Local-tools root 路径无效")
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        try:
            state = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(state.st_mode):
            raise LakeLocalIndexError("SQLite Local-tools root 的祖先目录不得是符号链接")


def _dataset_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LakeLocalIndexError("dataset_id 必须是非空文本")
    candidate = value.strip()
    try:
        LakeDatasetReference(
            kind=LakeDatasetKind.BARS,
            dataset_id=candidate,
            version_hash="0" * 64,
        )
    except LakeContractError as exc:
        raise LakeLocalIndexError("dataset_id 格式不合法") from exc
    return candidate


def _datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise LakeLocalIndexCorruptionError(f"{field} 必须是 ISO-8601 时间")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LakeLocalIndexCorruptionError(f"{field} 必须是 ISO-8601 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LakeLocalIndexCorruptionError(f"{field} 必须带时区")
    return parsed.astimezone(UTC)


def _entry_from_row(row: object, *, indexed_at: datetime) -> LocalLakeIndexEntry:
    if not isinstance(row, tuple) or len(row) != 9:
        raise LakeLocalIndexCorruptionError("SQLite Local-tools index entry 记录无效")
    (
        kind_text,
        dataset_id,
        version_hash,
        manifest_relative_path,
        manifest_sha256,
        upstream_dataset_version_hash,
        upstream_artifact_content_hash,
        minimum_available_at,
        maximum_available_at,
    ) = row
    try:
        kind = LakeDatasetKind(kind_text)
        reference = LakeDatasetReference(
            kind=kind,
            dataset_id=dataset_id,
            version_hash=version_hash,
        )
    except (LakeContractError, TypeError, ValueError) as exc:
        raise LakeLocalIndexCorruptionError("SQLite Local-tools index reference 无效") from exc
    hash_values = (
        manifest_sha256,
        upstream_dataset_version_hash,
        upstream_artifact_content_hash,
    )
    if not all(isinstance(value, str) and len(value) == 64 for value in hash_values):
        raise LakeLocalIndexCorruptionError("SQLite Local-tools index hash 无效")
    if not isinstance(manifest_relative_path, str) or (
        manifest_relative_path.startswith("/") or ".." in manifest_relative_path.split("/")
    ):
        raise LakeLocalIndexCorruptionError("SQLite Local-tools manifest 相对路径无效")
    return LocalLakeIndexEntry(
        reference=reference,
        manifest_relative_path=manifest_relative_path,
        manifest_sha256=manifest_sha256,
        upstream_dataset_version_hash=upstream_dataset_version_hash,
        upstream_artifact_content_hash=upstream_artifact_content_hash,
        minimum_available_at=_datetime(minimum_available_at, "SQLite minimum_available_at"),
        maximum_available_at=_datetime(maximum_available_at, "SQLite maximum_available_at"),
        indexed_at=indexed_at,
    )


def _entry_sort_key(reference: LakeDatasetReference) -> tuple[str, str, str]:
    return (reference.kind.value, reference.dataset_id, reference.version_hash)


def _is_lock_error(error: sqlite3.DatabaseError) -> bool:
    message = str(error).casefold()
    return "locked" in message or "busy" in message


def _has_sqlite_lock_cause(error: LakeLocalIndexError) -> bool:
    cause = error.__cause__
    return isinstance(cause, sqlite3.DatabaseError) and _is_lock_error(cause)
