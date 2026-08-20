"""下载缓存的显式、安全清理。

该模块故意不处理标准行情、报告或日志。它只枚举 ``downloads_dir`` 内过期的
Parquet 缓存和本项目原子写入遗留的临时文件；默认仅生成计划，删除必须同时满足
策略启用和调用方传入 ``apply=True``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import stat as stat_module
from typing import Iterable, Literal

from northstar_quant.platform.config.output_retention import OutputRetentionPolicy
from northstar_quant.platform.config.settings import get_settings

CleanupKind = Literal["download_cache", "temporary_file"]


class OutputCleanupSafetyError(RuntimeError):
    """清理范围、文件状态或二次确认不安全时失败关闭。"""


@dataclass(frozen=True, slots=True)
class CleanupFile:
    """计划删除的单个文件及其计划生成时的身份快照。"""

    relative_path: str
    modified_at: str
    modified_at_ns: int
    size_bytes: int
    device_id: int
    inode: int


@dataclass(frozen=True, slots=True)
class CleanupTarget:
    """一组必须一起处理的过期缓存或单个临时文件。"""

    kind: CleanupKind
    age_days: float
    files: tuple[CleanupFile, ...]


@dataclass(frozen=True, slots=True)
class OutputCleanupPlan:
    """不会改变文件系统的清理计划。"""

    downloads_dir: str
    policy_enabled: bool
    download_cache_retention_days: int
    temporary_file_retention_days: int
    targets: tuple[CleanupTarget, ...]
    blocked_publication_markers: tuple[str, ...]
    skipped_unsafe_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OutputCleanupResult:
    """dry-run 或实际删除后的可审计结果。"""

    mode: Literal["dry_run", "applied"]
    plan: OutputCleanupPlan
    deleted_paths: tuple[str, ...]


def plan_output_cleanup(
    policy: OutputRetentionPolicy,
    *,
    downloads_dir: str | Path | None = None,
    protected_roots: Iterable[str | Path] | None = None,
    now: datetime | None = None,
) -> OutputCleanupPlan:
    """计算清理计划，不删除任何文件。

    ``protected_roots`` 只能追加保护范围，不能替代标准市场数据、不可变制品库和报告根。
    若 downloads 根与任一保护根重叠，直接拒绝，而不是猜测用户意图。
    """

    root = _resolve_downloads_dir(downloads_dir)
    resolved_now = _normalize_now(now)
    root_text = str(root)
    if root.is_symlink():
        raise OutputCleanupSafetyError(f"下载缓存根是符号链接，已拒绝清理：{root}")
    _assert_no_symlink_ancestors(root, "下载缓存根")
    if not root.exists():
        return OutputCleanupPlan(
            downloads_dir=root_text,
            policy_enabled=policy.enabled,
            download_cache_retention_days=policy.download_cache_retention_days,
            temporary_file_retention_days=policy.temporary_file_retention_days,
            targets=(),
            blocked_publication_markers=(),
            skipped_unsafe_paths=(),
        )
    if not root.is_dir():
        raise OutputCleanupSafetyError(f"下载缓存根不是目录，已拒绝清理：{root}")

    resolved_root = root.resolve()
    resolved_protected_roots = _resolve_protected_roots(protected_roots)
    for protected_root in resolved_protected_roots:
        if _paths_overlap(resolved_root, protected_root):
            raise OutputCleanupSafetyError(
                "下载缓存根与受保护的标准数据或报告目录重叠，已拒绝清理："
                f"{resolved_root} <-> {protected_root}"
            )

    blocked_markers: set[str] = set()
    skipped_paths: set[str] = set()
    targets: list[CleanupTarget] = []
    for path in _iter_files(resolved_root):
        relative_path = _safe_relative_path(
            path,
            root=resolved_root,
            protected_roots=resolved_protected_roots,
            skipped_paths=skipped_paths,
        )
        if relative_path is None:
            continue
        if path.name.endswith(".publishing.json"):
            blocked_markers.add(relative_path)
            continue

        try:
            stat = path.stat()
        except OSError:
            skipped_paths.add(relative_path)
            continue
        age_days = _age_days(stat.st_mtime_ns, resolved_now)

        if path.suffix == ".parquet" and age_days >= policy.download_cache_retention_days:
            target = _build_cache_target(
                path,
                root=resolved_root,
                protected_roots=resolved_protected_roots,
                skipped_paths=skipped_paths,
                age_days=age_days,
            )
            if target is None:
                marker = _publication_marker_path(path)
                marker_relative = _safe_relative_path(
                    marker,
                    root=resolved_root,
                    protected_roots=resolved_protected_roots,
                    skipped_paths=skipped_paths,
                )
                if marker_relative is not None:
                    blocked_markers.add(marker_relative)
                continue
            targets.append(target)
        elif _is_safe_temporary_file(path) and age_days >= policy.temporary_file_retention_days:
            active_markers = _active_publication_markers_for_temporary_file(path)
            if active_markers:
                for marker_path in active_markers:
                    try:
                        blocked_markers.add(marker_path.relative_to(resolved_root).as_posix())
                    except ValueError:
                        skipped_paths.add(str(marker_path))
                continue
            targets.append(
                CleanupTarget(
                    kind="temporary_file",
                    age_days=age_days,
                    files=(_cleanup_file(path, resolved_root),),
                )
            )

    targets.sort(key=lambda item: (item.kind, item.files[0].relative_path))
    return OutputCleanupPlan(
        downloads_dir=root_text,
        policy_enabled=policy.enabled,
        download_cache_retention_days=policy.download_cache_retention_days,
        temporary_file_retention_days=policy.temporary_file_retention_days,
        targets=tuple(targets),
        blocked_publication_markers=tuple(sorted(blocked_markers)),
        skipped_unsafe_paths=tuple(sorted(skipped_paths)),
    )


def cleanup_output_files(
    policy: OutputRetentionPolicy,
    *,
    apply: bool = False,
    downloads_dir: str | Path | None = None,
    protected_roots: Iterable[str | Path] | None = None,
    now: datetime | None = None,
) -> OutputCleanupResult:
    """执行或预览清理；没有 ``apply=True`` 时严格 dry-run。"""

    plan = plan_output_cleanup(
        policy,
        downloads_dir=downloads_dir,
        protected_roots=protected_roots,
        now=now,
    )
    if not apply:
        return OutputCleanupResult(mode="dry_run", plan=plan, deleted_paths=())
    if not policy.enabled:
        raise OutputCleanupSafetyError(
            "运行输出清理策略当前 disabled；请先在配置中显式启用，再传入 --apply"
        )

    root = Path(plan.downloads_dir)
    if root.is_symlink():
        raise OutputCleanupSafetyError(f"下载缓存根状态已变化，已拒绝清理：{root}")
    _assert_no_symlink_ancestors(root, "下载缓存根")
    if not root.exists():
        return OutputCleanupResult(mode="applied", plan=plan, deleted_paths=())
    if not root.is_dir():
        raise OutputCleanupSafetyError(f"下载缓存根状态已变化，已拒绝清理：{root}")
    resolved_root = root.resolve()
    resolved_protected_roots = _resolve_protected_roots(protected_roots)
    if any(
        _paths_overlap(resolved_root, protected_root) for protected_root in resolved_protected_roots
    ):
        raise OutputCleanupSafetyError("下载缓存根与受保护目录重叠，已拒绝清理")
    deleted_paths: list[str] = []
    for target in plan.targets:
        active_markers: tuple[Path, ...]
        if target.kind == "download_cache":
            active_markers = (_publication_marker_path(root / target.files[0].relative_path),)
        else:
            active_markers = _active_publication_markers_for_temporary_file(
                root / target.files[0].relative_path
            )
        for marker_path in active_markers:
            if marker_path.exists() or marker_path.is_symlink():
                raise OutputCleanupSafetyError(
                    f"待清理文件出现发布中标记，已拒绝清理：{marker_path}"
                )
        for file in target.files:
            path = root / file.relative_path
            _verify_planned_file(
                path,
                file,
                root=resolved_root,
                protected_roots=resolved_protected_roots,
            )
        for file in target.files:
            path = root / file.relative_path
            _unlink_verified_file(
                path,
                file,
                root=resolved_root,
                protected_roots=resolved_protected_roots,
            )
            deleted_paths.append(file.relative_path)

    return OutputCleanupResult(
        mode="applied",
        plan=plan,
        deleted_paths=tuple(deleted_paths),
    )


def _resolve_downloads_dir(downloads_dir: str | Path | None) -> Path:
    candidate = (
        Path(downloads_dir).expanduser()
        if downloads_dir is not None
        else get_settings().downloads_dir.expanduser()
    )
    if ".." in candidate.parts:
        raise OutputCleanupSafetyError("下载缓存根不得包含 '..' 路径段")
    if candidate.is_absolute():
        return candidate
    return Path.cwd() / candidate


def _resolve_protected_roots(
    protected_roots: Iterable[str | Path] | None,
) -> tuple[Path, ...]:
    settings = get_settings()
    # 不可变制品库与标准市场投影同属长期研究证据；任何下载目录误配到该根时必须拒绝，
    # 而不是让 cleanup 误删 hash blob、snapshot 或 dataset manifest。显式 roots 只能追加。
    configured_roots = (
        settings.storage_dir / "market",
        settings.storage_dir / "artifacts",
        settings.reports_dir,
        *(protected_roots or ()),
    )
    return tuple(dict.fromkeys(Path(path).expanduser().resolve() for path in configured_roots))


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("清理计划的 now 必须包含时区")
    return now.astimezone(timezone.utc)


def _assert_no_symlink_ancestors(path: Path, label: str) -> None:
    """在 resolve 前检查 lexical 路径，避免链接把清理范围重定向到其他目录。"""

    for candidate in reversed((path, *path.parents)):
        try:
            if candidate.is_symlink():
                raise OutputCleanupSafetyError(
                    f"{label} 或其祖先路径是符号链接，已拒绝清理：{candidate}"
                )
        except OSError as exc:
            raise OutputCleanupSafetyError(f"无法检查 {label} 路径：{candidate}") from exc


def _iter_files(root: Path) -> list[Path]:
    """递归列出普通文件；目录链接由后续安全校验记录并跳过。"""

    try:
        paths = list(root.rglob("*"))
    except OSError as exc:
        raise OutputCleanupSafetyError(f"无法枚举下载缓存根：{root}") from exc
    return sorted((path for path in paths if path.is_file() or path.is_symlink()), key=str)


def _safe_relative_path(
    path: Path,
    *,
    root: Path,
    protected_roots: tuple[Path, ...],
    skipped_paths: set[str],
) -> str | None:
    try:
        lexical_relative = path.relative_to(root)
    except ValueError:
        skipped_paths.add(str(path))
        return None
    relative_text = lexical_relative.as_posix()
    if path.is_symlink():
        skipped_paths.add(relative_text)
        return None
    try:
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(root)
    except (OSError, ValueError):
        skipped_paths.add(relative_text)
        return None
    if any(_is_within(resolved_path, protected_root) for protected_root in protected_roots):
        skipped_paths.add(relative_text)
        return None
    return relative_text


def _build_cache_target(
    cache_path: Path,
    *,
    root: Path,
    protected_roots: tuple[Path, ...],
    skipped_paths: set[str],
    age_days: float,
) -> CleanupTarget | None:
    marker_path = _publication_marker_path(cache_path)
    if marker_path.exists() or marker_path.is_symlink():
        return None

    files = [_cleanup_file(cache_path, root)]
    manifest_path = _manifest_path(cache_path)
    if manifest_path.exists():
        relative_manifest = _safe_relative_path(
            manifest_path,
            root=root,
            protected_roots=protected_roots,
            skipped_paths=skipped_paths,
        )
        if relative_manifest is None:
            return None
        try:
            files.append(_cleanup_file(manifest_path, root))
        except OSError:
            skipped_paths.add(relative_manifest)
            return None
    return CleanupTarget(
        kind="download_cache",
        age_days=age_days,
        files=tuple(files),
    )


def _cleanup_file(path: Path, root: Path) -> CleanupFile:
    stat = path.stat()
    return CleanupFile(
        relative_path=path.relative_to(root).as_posix(),
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        modified_at_ns=stat.st_mtime_ns,
        size_bytes=stat.st_size,
        device_id=stat.st_dev,
        inode=stat.st_ino,
    )


def _verify_planned_file(
    path: Path,
    planned_file: CleanupFile,
    *,
    root: Path,
    protected_roots: tuple[Path, ...],
) -> None:
    skipped_paths: set[str] = set()
    relative_path = _safe_relative_path(
        path,
        root=root,
        protected_roots=protected_roots,
        skipped_paths=skipped_paths,
    )
    if relative_path != planned_file.relative_path:
        raise OutputCleanupSafetyError(f"待删除文件不再位于安全下载缓存根内，已拒绝：{path}")
    try:
        stat = path.stat()
    except OSError as exc:
        raise OutputCleanupSafetyError(f"待删除文件不可访问，已拒绝：{path}") from exc
    if (
        stat.st_mtime_ns != planned_file.modified_at_ns
        or stat.st_size != planned_file.size_bytes
        or stat.st_dev != planned_file.device_id
        or stat.st_ino != planned_file.inode
    ):
        raise OutputCleanupSafetyError(f"待删除文件在预览后已变化，已拒绝：{path}")


def _unlink_verified_file(
    path: Path,
    planned_file: CleanupFile,
    *,
    root: Path,
    protected_roots: tuple[Path, ...],
) -> None:
    """从固定 root dir_fd 删除已验证文件，避免链接重定向删除范围。"""

    _verify_planned_file(
        path,
        planned_file,
        root=root,
        protected_roots=protected_roots,
    )
    if os.name == "nt":
        # Windows 的 reparse point 已由 _safe_relative_path 拒绝；部署 ACL 必须确保下载根仅
        # 对当前用户可写。此处没有可移植的 dir_fd unlink API，故保留最终一次身份校验。
        path.unlink()
        return
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise OutputCleanupSafetyError(f"待删除文件越出下载缓存根，已拒绝：{path}") from exc
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise OutputCleanupSafetyError(f"待删除文件相对路径不安全，已拒绝：{path}")
    parent_fd = _open_cleanup_directory_fd(root, relative.parts[:-1])
    try:
        filename = relative.name
        current = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        if not stat_module.S_ISREG(current.st_mode):
            raise OutputCleanupSafetyError(f"待删除文件不是普通文件，已拒绝：{path}")
        if (
            current.st_mtime_ns != planned_file.modified_at_ns
            or current.st_size != planned_file.size_bytes
            or current.st_dev != planned_file.device_id
            or current.st_ino != planned_file.inode
        ):
            raise OutputCleanupSafetyError(f"待删除文件在删除前已变化，已拒绝：{path}")
        os.unlink(filename, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileNotFoundError as exc:
        raise OutputCleanupSafetyError(f"待删除文件已不存在，已拒绝：{path}") from exc
    except OSError as exc:
        raise OutputCleanupSafetyError(f"无法安全删除下载缓存文件：{path}") from exc
    finally:
        os.close(parent_fd)


def _open_cleanup_directory_fd(root: Path, parts: tuple[str, ...]) -> int:
    """逐段打开非链接的 POSIX 下载缓存目录。"""

    try:
        root_state = root.lstat()
    except OSError as exc:
        raise OutputCleanupSafetyError(f"无法安全打开下载缓存根：{root}") from exc
    if not stat_module.S_ISDIR(root_state.st_mode) or stat_module.S_ISLNK(root_state.st_mode):
        raise OutputCleanupSafetyError(f"下载缓存根不是非链接目录，已拒绝：{root}")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(root, flags)
    except OSError as exc:
        raise OutputCleanupSafetyError(f"无法安全打开下载缓存根：{root}") from exc
    try:
        opened_root = os.fstat(directory_fd)
        if (opened_root.st_dev, opened_root.st_ino) != (root_state.st_dev, root_state.st_ino):
            raise OutputCleanupSafetyError("下载缓存根在打开时发生变化，已拒绝删除")
        for part in parts:
            if part in {"", ".", ".."}:
                raise OutputCleanupSafetyError("下载缓存目录段不安全，已拒绝删除")
            try:
                next_fd = os.open(part, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise OutputCleanupSafetyError("下载缓存父目录不安全，已拒绝删除") from exc
            if not stat_module.S_ISDIR(os.fstat(next_fd).st_mode):
                os.close(next_fd)
                raise OutputCleanupSafetyError("下载缓存父路径不是目录，已拒绝删除")
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def _publication_marker_path(path: Path) -> Path:
    return path.with_suffix(".publishing.json")


def _manifest_path(path: Path) -> Path:
    return path.with_suffix(".manifest.json")


def _is_safe_temporary_file(path: Path) -> bool:
    name = path.name
    return name.startswith(".") and name.endswith(".tmp") and name.count(".") >= 3


def _active_publication_markers_for_temporary_file(path: Path) -> tuple[Path, ...]:
    """返回仍在发布的原子写入临时文件对应标记，无法识别时宁可不推断。"""

    target_path = _temporary_target_path(path)
    if target_path is None:
        return ()

    candidates = {_publication_marker_path(target_path)}
    if target_path.name.endswith(".manifest.json"):
        dataset_name = target_path.name.removesuffix(".manifest.json") + ".parquet"
        candidates.add(_publication_marker_path(target_path.with_name(dataset_name)))
    return tuple(
        sorted(
            (
                marker_path
                for marker_path in candidates
                if marker_path.exists() or marker_path.is_symlink()
            ),
            key=str,
        )
    )


def _temporary_target_path(path: Path) -> Path | None:
    """从项目原子写入的 ``.<target>.<nonce>.tmp`` 名称恢复目标文件。"""

    if not _is_safe_temporary_file(path):
        return None
    temporary_stem = path.name[1 : -len(".tmp")]
    target_name, separator, nonce = temporary_stem.rpartition(".")
    if not separator or not target_name or not nonce:
        return None
    return path.with_name(target_name)


def _age_days(modified_at_ns: int, now: datetime) -> float:
    modified_at = datetime.fromtimestamp(modified_at_ns / 1_000_000_000, tz=timezone.utc)
    return max(0.0, (now - modified_at).total_seconds() / 86_400)


def _paths_overlap(first: Path, second: Path) -> bool:
    return _is_within(first, second) or _is_within(second, first)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
