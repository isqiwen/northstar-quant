"""不可变、分区 Parquet Lake 的发布与验证存储。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

import polars as pl

from northstar_quant.data.lake.models import (
    LakeContractError,
    LakeDatasetReference,
    LakeManifest,
    LakePartition,
)
from northstar_quant.foundation.platform_support import require_linux_x86_64


class LakeStoreError(RuntimeError):
    """Lake 文件系统边界或发布协议不满足。"""


class LakeNotFoundError(LakeStoreError):
    """请求的不可变 Lake 版本不存在。"""


class LakeIntegrityError(LakeStoreError):
    """manifest、Parquet 或分区元数据遭到篡改或不完整。"""


@dataclass(frozen=True, slots=True)
class VerifiedLakeDataset:
    """已逐文件验真的 Lake 版本，供 DuckDB 只读分析消费。"""

    manifest: LakeManifest
    dataset_dir: Path
    manifest_path: Path
    manifest_sha256: str
    parquet_paths: tuple[Path, ...]


class ParquetLakeStore:
    """固定于 ``<storage_dir>/lake`` 的不可变历史数据湖。"""

    def __init__(self, root: str | Path) -> None:
        require_linux_x86_64()
        candidate = Path(root).expanduser()
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise LakeStoreError("Lake root 必须是无 '..' 的绝对路径")
        _ensure_private_directory(candidate, "Lake root")
        self._root = candidate
        self._datasets_root = self._root / "datasets"
        self._staging_root = self._root / ".staging"
        self._query_staging_root = self._root / ".query-staging"
        for directory, label in (
            (self._datasets_root, "Lake datasets 目录"),
            (self._staging_root, "Lake 发布暂存目录"),
            (self._query_staging_root, "Lake 查询暂存目录"),
        ):
            _ensure_private_directory(directory, label)

    @classmethod
    def from_settings(cls) -> "ParquetLakeStore":
        from northstar_quant.foundation.config.settings import get_settings

        return cls(get_settings().storage_dir / "lake")

    @property
    def root(self) -> Path:
        return self._root

    def dataset_dir(self, reference: LakeDatasetReference) -> Path:
        if not isinstance(reference, LakeDatasetReference):
            raise LakeStoreError("reference 必须是 LakeDatasetReference")
        dataset_dir = (
            self._datasets_root
            / reference.kind.value
            / reference.dataset_id
            / reference.version_hash
        )
        self._assert_safe_directory_path(dataset_dir, "Lake dataset 目录")
        return dataset_dir

    def manifest_path(self, reference: LakeDatasetReference) -> Path:
        return self.dataset_dir(reference) / "manifest.json"

    def create_staging_dir(self) -> Path:
        """创建同一文件系统内的受控发布暂存目录。"""

        self._assert_safe_directory_path(self._staging_root, "Lake 发布暂存目录")
        staging_dir = Path(tempfile.mkdtemp(prefix="lake-", dir=self._staging_root))
        self._assert_safe_directory_path(staging_dir, "Lake 发布暂存目录")
        return staging_dir

    def publish_staged(self, staging_dir: Path, manifest: LakeManifest) -> VerifiedLakeDataset:
        """原子发布已完整写入 staging 的 Lake version，禁止覆盖既有版本。"""

        if not isinstance(manifest, LakeManifest):
            raise LakeStoreError("manifest 必须是 LakeManifest")
        if (
            staging_dir.parent != self._staging_root
            or not staging_dir.is_dir()
            or staging_dir.is_symlink()
        ):
            raise LakeStoreError("staging_dir 必须是当前 Lake 创建的普通暂存目录")
        self._assert_safe_directory_path(staging_dir, "Lake 发布暂存目录")
        self._assert_tree_without_symlinks(staging_dir, "Lake 发布暂存目录")
        reference = manifest.reference
        manifest_path = staging_dir / "manifest.json"
        _write_manifest(manifest, manifest_path)
        expected_paths = {partition.relative_path for partition in manifest.partitions}
        actual_paths = {
            path.relative_to(staging_dir).as_posix()
            for path in staging_dir.rglob("*.parquet")
            if path.is_file() and not path.is_symlink()
        }
        if actual_paths != expected_paths:
            raise LakeStoreError("staging Lake 的 Parquet 文件集合与 manifest 不一致")
        target = self.dataset_dir(reference)
        self._ensure_private_directory_path(target.parent, "Lake dataset 父目录")
        if target.exists():
            existing = self.verify(reference)
            if existing.manifest.as_mapping() != manifest.as_mapping():
                raise LakeIntegrityError("同一 Lake version 已存在但 manifest 内容不一致")
            self._remove_staging_dir(staging_dir)
            return existing
        try:
            os.replace(staging_dir, target)
        except FileExistsError:
            existing = self.verify(reference)
            if existing.manifest.as_mapping() != manifest.as_mapping():
                raise LakeIntegrityError("并发发布产生同 version 不一致内容")
            self._remove_staging_dir(staging_dir)
            return existing
        return self.verify(reference)

    def verify(self, reference: LakeDatasetReference) -> VerifiedLakeDataset:
        """重新计算所有文件 hash、schema、分区与 PIT 范围。"""

        if not isinstance(reference, LakeDatasetReference):
            raise LakeStoreError("reference 必须是 LakeDatasetReference")
        dataset_dir = self.dataset_dir(reference)
        if not dataset_dir.is_dir() or dataset_dir.is_symlink():
            raise LakeNotFoundError(f"Lake dataset 不存在：{reference.version_hash}")
        self._assert_tree_without_symlinks(dataset_dir, "Lake dataset")
        manifest_path = dataset_dir / "manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise LakeIntegrityError("Lake manifest 缺失或不是普通文件")
        try:
            manifest_bytes = self._read_regular_bytes(manifest_path, "Lake manifest")
            payload = json.loads(manifest_bytes.decode("utf-8"))
            if not isinstance(payload, dict):
                raise LakeContractError("manifest 必须是 JSON 对象")
            manifest = LakeManifest.from_mapping(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, LakeContractError) as exc:
            raise LakeIntegrityError("Lake manifest 无法验证") from exc
        if manifest.reference != reference:
            raise LakeIntegrityError("Lake manifest reference 与请求不一致")
        parquet_paths: list[Path] = []
        actual_paths: set[str] = set()
        observed_minimum: datetime | None = None
        observed_maximum: datetime | None = None
        for partition in manifest.partitions:
            path = _partition_path(dataset_dir, partition)
            if not path.is_file() or path.is_symlink():
                raise LakeIntegrityError(
                    f"Lake partition 缺失或不是普通文件：{partition.relative_path}"
                )
            actual_paths.add(partition.relative_path)
            partition_bytes = self._read_regular_bytes(path, "Lake partition")
            if _sha256_bytes(partition_bytes) != partition.content_sha256:
                raise LakeIntegrityError(f"Lake partition hash 不一致：{partition.relative_path}")
            try:
                frame = pl.read_parquet(partition_bytes)
            except pl.exceptions.PolarsError as exc:
                raise LakeIntegrityError(
                    f"Lake partition 无法读取：{partition.relative_path}"
                ) from exc
            if frame.height != partition.row_count:
                raise LakeIntegrityError(f"Lake partition 行数不一致：{partition.relative_path}")
            if _schema(frame) != manifest.schema:
                raise LakeIntegrityError(f"Lake partition schema 不一致：{partition.relative_path}")
            _validate_event_time(frame, manifest.event_time_column)
            minimum, maximum = _validate_available_at(frame, manifest.available_at_column)
            observed_minimum = (
                minimum if observed_minimum is None else min(observed_minimum, minimum)
            )
            observed_maximum = (
                maximum if observed_maximum is None else max(observed_maximum, maximum)
            )
            _validate_partition_values(frame, partition)
            parquet_paths.append(path)
        discovered_paths = {
            path.relative_to(dataset_dir).as_posix()
            for path in dataset_dir.rglob("*.parquet")
            if path.is_file() and not path.is_symlink()
        }
        if discovered_paths != actual_paths:
            raise LakeIntegrityError("Lake 中存在未列入 manifest 的 Parquet 文件")
        if (
            observed_minimum != manifest.minimum_available_at
            or observed_maximum != manifest.maximum_available_at
        ):
            raise LakeIntegrityError("Lake available_at 范围与 manifest 不一致")
        return VerifiedLakeDataset(
            manifest=manifest,
            dataset_dir=dataset_dir,
            manifest_path=manifest_path,
            manifest_sha256=_sha256_bytes(manifest_bytes),
            parquet_paths=tuple(parquet_paths),
        )

    @contextmanager
    def query_snapshot(self, verified: VerifiedLakeDataset) -> Iterator[tuple[Path, ...]]:
        """创建仅供一次 DuckDB 查询读取的私有、已重验 Parquet 快照。

        Lake 的 hash 验证与 DuckDB 打开文件之间不能重用原始路径，否则本地文件被替换时会出现
        time-of-check/time-of-use 缺口。这里将每份刚重新校验 hash 的分区复制到受控临时目录；临时目录
        只承载本次查询，不会修改或清理任何 Lake version。
        """

        if not isinstance(verified, VerifiedLakeDataset):
            raise LakeStoreError("verified 必须是 VerifiedLakeDataset")
        reference = verified.manifest.reference
        if self.dataset_dir(reference) != verified.dataset_dir:
            raise LakeIntegrityError("查询 Lake snapshot 的 dataset 目录不一致")
        self._assert_safe_directory_path(self._query_staging_root, "Lake 查询暂存目录")
        with tempfile.TemporaryDirectory(prefix="query-", dir=self._query_staging_root) as root_text:
            snapshot_root = Path(root_text)
            parquet_paths: list[Path] = []
            for partition in verified.manifest.partitions:
                source = _partition_path(verified.dataset_dir, partition)
                payload = self._read_regular_bytes(source, "Lake 查询分区")
                if _sha256_bytes(payload) != partition.content_sha256:
                    raise LakeIntegrityError(
                        f"Lake 查询分区 hash 不一致：{partition.relative_path}"
                    )
                destination = snapshot_root.joinpath(*partition.relative_path.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with destination.open("xb") as file_obj:
                    file_obj.write(payload)
                destination.chmod(0o400)
                parquet_paths.append(destination)
            yield tuple(parquet_paths)

    def _remove_staging_dir(self, staging_dir: Path) -> None:
        """只清理由本实例创建、且已确认位于 staging root 内的临时目录。"""

        if staging_dir.parent != self._staging_root or not staging_dir.exists():
            return
        for path in sorted(staging_dir.rglob("*"), reverse=True):
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        staging_dir.rmdir()

    def _assert_safe_directory_path(self, path: Path, label: str) -> None:
        """拒绝根目录下任一已存在的符号链接或宽权限目录。"""

        try:
            relative = path.relative_to(self._root)
        except ValueError as exc:
            raise LakeStoreError(f"{label} 越出 Lake root") from exc
        _assert_private_directory(self._root, "Lake root", strict_permissions=True)
        current = self._root
        for part in relative.parts:
            current = current / part
            if _lstat(current) is None:
                return
            _assert_private_directory(current, label)

    def _ensure_private_directory_path(self, path: Path, label: str) -> None:
        try:
            relative = path.relative_to(self._root)
        except ValueError as exc:
            raise LakeStoreError(f"{label} 越出 Lake root") from exc
        _assert_private_directory(self._root, "Lake root", strict_permissions=True)
        current = self._root
        for part in relative.parts:
            current = current / part
            if _lstat(current) is None:
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
            _assert_private_directory(current, label)

    def _assert_tree_without_symlinks(self, root: Path, label: str) -> None:
        self._assert_safe_directory_path(root, label)
        for path in root.rglob("*"):
            state = _lstat(path)
            if state is None:
                raise LakeIntegrityError(f"{label} 在验证中消失：{path}")
            if stat.S_ISLNK(state.st_mode):
                raise LakeIntegrityError(f"{label} 不得包含符号链接：{path}")
            if stat.S_ISDIR(state.st_mode):
                _assert_private_directory(path, label)

    def _read_regular_bytes(self, path: Path, label: str) -> bytes:
        self._assert_safe_directory_path(path.parent, f"{label} 父目录")
        directory_fd = self._open_directory_fd(path.parent)
        try:
            return _read_regular_bytes_at(directory_fd, path.name, label)
        finally:
            os.close(directory_fd)

    def _open_directory_fd(self, path: Path) -> int:
        """从 Lake root 逐段以 ``O_NOFOLLOW`` 打开目录，防止中间路径被链接替换。"""

        try:
            relative = path.relative_to(self._root)
        except ValueError as exc:
            raise LakeStoreError("Lake 目录越出 root") from exc
        root_state = _assert_private_directory(
            self._root, "Lake root", strict_permissions=True
        )
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            directory_fd = os.open(self._root, flags)
        except OSError as exc:
            raise LakeStoreError("无法安全打开 Lake root") from exc
        try:
            opened_root = os.fstat(directory_fd)
            if not _same_file(opened_root, root_state):
                raise LakeStoreError("Lake root 在打开时发生变化")
            _assert_private_directory_state(opened_root, "Lake root", strict_permissions=True)
            for part in relative.parts:
                try:
                    next_fd = os.open(part, flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise LakeStoreError(f"无法安全打开 Lake 目录：{path}") from exc
                next_state = os.fstat(next_fd)
                try:
                    _assert_private_directory_state(next_state, "Lake 目录")
                except BaseException:
                    os.close(next_fd)
                    raise
                os.close(directory_fd)
                directory_fd = next_fd
            return directory_fd
        except BaseException:
            os.close(directory_fd)
            raise


def _ensure_private_directory(path: Path, label: str) -> None:
    """创建并验证仅服务用户可写的普通目录。"""

    _assert_no_symlink_ancestors(path, label)
    if _lstat(path) is None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _assert_private_directory(path, label, strict_permissions=True)


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _assert_no_symlink_ancestors(path: Path, label: str) -> None:
    """拒绝由外部链接引入的 Lake root，即使最终目录本身不是链接。"""

    parts = path.parts
    if not parts:
        raise LakeStoreError(f"{label} 路径无效")
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        state = _lstat(current)
        if state is None:
            return
        if stat.S_ISLNK(state.st_mode):
            raise LakeStoreError(f"{label} 的祖先目录不得是符号链接：{current}")


def _assert_private_directory(
    path: Path,
    label: str,
    *,
    strict_permissions: bool = False,
) -> os.stat_result:
    state = _lstat(path)
    if state is None:
        raise LakeStoreError(f"{label} 不存在：{path}")
    _assert_private_directory_state(state, label, strict_permissions=strict_permissions)
    return state


def _assert_private_directory_state(
    state: os.stat_result,
    label: str,
    *,
    strict_permissions: bool = False,
) -> None:
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        raise LakeStoreError(f"{label} 必须是普通目录，不能是符号链接")
    if state.st_uid != os.getuid():
        raise LakeStoreError(f"{label} 必须由当前服务用户拥有")
    if strict_permissions and stat.S_IMODE(state.st_mode) & 0o077:
        raise LakeStoreError(f"{label} 不得向 group 或 other 开放访问")


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _read_regular_bytes_at(directory_fd: int, filename: str, label: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(filename, flags, dir_fd=directory_fd)
    except FileNotFoundError as exc:
        raise LakeIntegrityError(f"{label} 不存在") from exc
    except OSError as exc:
        raise LakeIntegrityError(f"无法安全打开 {label}") from exc
    try:
        state = os.fstat(descriptor)
        if not stat.S_ISREG(state.st_mode):
            raise LakeIntegrityError(f"{label} 必须是普通文件")
        with os.fdopen(descriptor, "rb") as file_obj:
            descriptor = -1
            return file_obj.read()
    except OSError as exc:
        raise LakeIntegrityError(f"无法读取 {label}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_manifest(manifest: LakeManifest, path: Path) -> None:
    path.write_text(
        json.dumps(manifest.as_mapping(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _partition_path(dataset_dir: Path, partition: LakePartition) -> Path:
    path = dataset_dir.joinpath(*partition.relative_path.split("/"))
    try:
        path.relative_to(dataset_dir)
    except ValueError as exc:
        raise LakeIntegrityError("Lake partition 路径越界") from exc
    return path


def _schema(frame: pl.DataFrame) -> tuple[tuple[str, str], ...]:
    return tuple((name, str(dtype)) for name, dtype in frame.schema.items())


def _validate_available_at(frame: pl.DataFrame, column: str) -> tuple[datetime, datetime]:
    if column not in frame.columns:
        raise LakeIntegrityError("Lake partition 缺少 available_at 字段")
    dtype = frame.schema[column]
    if not isinstance(dtype, pl.Datetime) or dtype.time_zone is None:
        raise LakeIntegrityError("Lake available_at 必须是带时区的 Datetime")
    series = frame.get_column(column)
    if series.null_count() != 0:
        raise LakeIntegrityError("Lake available_at 不得为 null")
    minimum = series.min()
    maximum = series.max()
    if not isinstance(minimum, datetime) or not isinstance(maximum, datetime):
        raise LakeIntegrityError("Lake available_at 类型无效")
    return minimum.astimezone(UTC), maximum.astimezone(UTC)


def _validate_event_time(frame: pl.DataFrame, column: str) -> None:
    if column not in frame.columns:
        raise LakeIntegrityError("Lake partition 缺少 event_time 字段")
    dtype = frame.schema[column]
    if not isinstance(dtype, pl.Date) and not (
        isinstance(dtype, pl.Datetime) and dtype.time_zone is not None
    ):
        raise LakeIntegrityError("Lake event_time 必须是 Date 或带时区的 Datetime")
    if frame.get_column(column).null_count() != 0:
        raise LakeIntegrityError("Lake event_time 不得为 null")


def _validate_partition_values(frame: pl.DataFrame, partition: LakePartition) -> None:
    for column, expected in partition.values:
        if column not in frame.columns:
            raise LakeIntegrityError(f"Lake partition 缺少分区字段：{column}")
        values = frame.get_column(column).unique(maintain_order=True).to_list()
        if len(values) != 1 or partition_value(values[0]) != expected:
            raise LakeIntegrityError(f"Lake partition 值不一致：{column}")


def partition_value(value: object) -> str:
    """把固定的标量分区值转换为可审计文本，拒绝 null 与复杂对象。"""

    if value is None:
        raise LakeStoreError("Lake 分区字段不得为 null")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise LakeStoreError("Lake Datetime 分区字段必须带时区")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    raise LakeStoreError("Lake 分区字段必须是有限标量")


def schema_for(frame: pl.DataFrame) -> tuple[tuple[str, str], ...]:
    """发布器使用的 schema 快照，与验证器保持同一序列化规则。"""

    return _schema(frame)
