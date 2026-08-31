#!/usr/bin/env python3
"""以纯 Python 构建可由 Linux 目标安装的最小部署制品。"""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import subprocess
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
from stat import S_IMODE
from typing import Final, Iterable

try:  # Allow direct-script execution and module imports.
    from .archive_policy import archive_path_is_excluded
    from .platform_support import PlatformSupportError, require_linux_x86_64
except ImportError:  # pragma: no cover - direct-script invocation path.
    from archive_policy import archive_path_is_excluded
    from platform_support import PlatformSupportError, require_linux_x86_64


class PackageError(ValueError):
    """部署制品无法安全构建。"""


_BUNDLE_PATHS: Final = (
    "pyproject.toml",
    "README.md",
    "uv.lock",
    "alembic.ini",
    "scripts/ci/check_dependency_policy.py",
    "scripts/ci/bootstrap_pep517.py",
    "alembic",
    "configs",
    "src",
    "templates",
    "ontology",
    "datasets",
    "infra/systemd",
)
_EXCLUDED_FILES: Final = frozenset(
    {
        Path("configs/app.yaml"),
        Path("configs/app.local.yaml"),
        Path("configs/app.local.example.yaml"),
    }
)


@dataclass(frozen=True)
class Artifact:
    """一个已落盘、经校验的部署制品。"""

    path: Path
    release_id: str
    sha256: str
    revision: str = ""


_COMMITTED_REVISION_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")


def _require_linux_x86_64_host() -> None:
    """Reject unsupported controllers before creating a release artifact."""

    try:
        require_linux_x86_64()
    except PlatformSupportError as exc:
        raise PackageError(str(exc)) from exc


def _git_revision(project_root: Path, *, require_clean_commit: bool) -> str:
    result = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    revision = result.stdout.strip()
    if result.returncode == 0 and _COMMITTED_REVISION_PATTERN.fullmatch(revision):
        if require_clean_commit:
            status = subprocess.run(
                ["git", "-C", str(project_root), "status", "--porcelain", "--untracked-files=all"],
                check=False,
                capture_output=True,
                text=True,
            )
            if status.returncode != 0 or status.stdout.strip():
                raise PackageError(
                    "signed release packaging requires an exact committed, clean worktree"
                )
        return revision
    if require_clean_commit:
        raise PackageError("signed release packaging requires a reachable full Git revision")
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _is_excluded(relative_path: Path, *, is_directory: bool) -> bool:
    if relative_path in _EXCLUDED_FILES:
        return True
    return archive_path_is_excluded(relative_path, is_directory=is_directory)


def _iter_bundle_paths(project_root: Path) -> Iterable[Path]:
    for root_name in _BUNDLE_PATHS:
        relative_root = Path(root_name)
        source = project_root / relative_root
        if not source.exists():
            raise PackageError(f"构建制品缺少必需路径：{relative_root}")
        if source.is_symlink():
            raise PackageError(f"部署制品不允许包含符号链接：{relative_root}")
        if source.is_file():
            if not _is_excluded(relative_root, is_directory=False):
                yield source
            continue
        if not source.is_dir():
            raise PackageError(f"部署制品路径类型不受支持：{relative_root}")

        for candidate in sorted(source.rglob("*")):
            relative_path = candidate.relative_to(project_root)
            if candidate.is_symlink():
                raise PackageError(f"部署制品不允许包含符号链接：{relative_path}")
            is_directory = candidate.is_dir()
            if _is_excluded(relative_path, is_directory=is_directory):
                continue
            if is_directory or candidate.is_file():
                yield candidate
            else:
                raise PackageError(f"部署制品包含不受支持的文件类型：{relative_path}")


def _add_path(archive: tarfile.TarFile, project_root: Path, path: Path) -> None:
    relative_path = path.relative_to(project_root)
    stat_result = path.stat()
    info = tarfile.TarInfo(relative_path.as_posix())
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = int(stat_result.st_mtime)
    info.mode = S_IMODE(stat_result.st_mode)
    if path.is_dir():
        info.type = tarfile.DIRTYPE
        info.size = 0
        archive.addfile(info)
        return
    info.size = stat_result.st_size
    with path.open("rb") as source:
        archive.addfile(info, source)


def _artifact_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_artifact(
    *,
    project_root: Path,
    output_dir: Path,
    revision: str | None = None,
    built_at: datetime | None = None,
    require_clean_commit: bool = False,
) -> Artifact:
    """构建制品，且绝不把活动配置、秘密或本地缓存带入归档。"""

    _require_linux_x86_64_host()
    project_root = project_root.resolve()
    if not (project_root / "configs" / "app.example.yaml").is_file():
        raise PackageError("构建制品缺少完整应用配置模板：configs/app.example.yaml")
    if not (project_root / ".env.example").is_file():
        raise PackageError("构建制品缺少 .env.example 安全模板。")

    revision = revision or _git_revision(project_root, require_clean_commit=require_clean_commit)
    if not revision.replace("-", "").replace("_", "").isalnum():
        raise PackageError("制品 revision 只能包含字母、数字、下划线和连字符。")
    built_at = built_at or datetime.now(UTC)
    release_id = f"{revision}-{built_at.strftime('%Y%m%d%H%M%S')}"
    output_dir = output_dir.resolve()
    for root_name in _BUNDLE_PATHS:
        source_root = (project_root / root_name).resolve()
        if source_root == output_dir or source_root in output_dir.parents:
            raise PackageError("制品输出目录不能位于将被归档的运行时源目录内。")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / f"northstar-quant-{release_id}.tar.gz"
    if artifact_path.exists():
        raise PackageError(f"拒绝覆盖已有部署制品：{artifact_path}")

    try:
        with tarfile.open(artifact_path, mode="x:gz", format=tarfile.PAX_FORMAT) as archive:
            for path in _iter_bundle_paths(project_root):
                _add_path(archive, project_root, path)
            metadata = (
                f"revision={revision}\n"
                f"built_at={built_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            ).encode("utf-8")
            metadata_info = tarfile.TarInfo("DEPLOY_ARTIFACT_META.txt")
            metadata_info.uid = 0
            metadata_info.gid = 0
            metadata_info.uname = ""
            metadata_info.gname = ""
            metadata_info.mode = 0o600
            metadata_info.mtime = int(built_at.timestamp())
            metadata_info.size = len(metadata)
            archive.addfile(metadata_info, fileobj=BytesIO(metadata))
    except PackageError:
        artifact_path.unlink(missing_ok=True)
        raise
    except (OSError, tarfile.TarError) as exc:
        artifact_path.unlink(missing_ok=True)
        raise PackageError(f"写入部署制品失败：{exc}") from exc
    artifact_path.chmod(0o600)
    return Artifact(
        path=artifact_path,
        release_id=release_id,
        sha256=_artifact_sha256(artifact_path),
        revision=revision,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构建 Northstar Quant Linux x86_64 部署制品。")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="项目根目录。")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"), help="制品输出目录。")
    parser.add_argument("--revision", help="可选的安全 revision 标识。")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        artifact = build_artifact(
            project_root=args.project_root,
            output_dir=args.output_dir,
            revision=args.revision,
        )
    except PackageError as exc:
        print(f"构建部署制品失败：{exc}")
        return 1
    print("部署制品构建完成")
    print(f"artifact={artifact.path}")
    print(f"release={artifact.release_id}")
    print(f"sha256={artifact.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
