#!/usr/bin/env python3
"""Install a pinned, verified ``just`` executable under ``.northstar``.

This standard-library-only installer is intentionally invoked only by the
explicitly confirmed development-tool bootstrap. It downloads one official,
pinned release asset, verifies its SHA-256 before extraction, and copies only
the expected executable into the repository-local tool directory.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import sys
import tarfile
import tempfile
from typing import BinaryIO
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile


JUST_VERSION = "1.57.0"
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_EXECUTABLE_BYTES = 16 * 1024 * 1024
_RELEASE_URL = f"https://github.com/casey/just/releases/download/{JUST_VERSION}"


class JustBootstrapError(RuntimeError):
    """Raised when the repository-local just installation cannot be verified."""


@dataclass(frozen=True)
class JustReleaseAsset:
    """A pinned official just release asset for one supported platform."""

    filename: str
    sha256: str
    archive_format: str
    executable_name: str

    @property
    def url(self) -> str:
        return f"{_RELEASE_URL}/{self.filename}"


_ASSETS: dict[tuple[str, str], JustReleaseAsset] = {
    (
        "Linux",
        "x86_64",
    ): JustReleaseAsset(
        filename="just-1.57.0-x86_64-unknown-linux-musl.tar.gz",
        sha256="45b548094283cb9739af8f13273b8cddeee869f5b4ef2bb631b1f311cb566155",
        archive_format="tar.gz",
        executable_name="just",
    ),
    (
        "Windows",
        "x86_64",
    ): JustReleaseAsset(
        filename="just-1.57.0-x86_64-pc-windows-msvc.zip",
        sha256="4c7391d17cb1d17b758b52004ee6411372b8a13ff37c3c9b9031625cb6026e09",
        archive_format="zip",
        executable_name="just.exe",
    ),
}


def _canonical_machine(machine: str) -> str:
    normalized = machine.strip().lower().replace("-", "_")
    if normalized in {"x86_64", "amd64"}:
        return "x86_64"
    return normalized


def release_asset_for_platform(
    *,
    system_name: str | None = None,
    machine: str | None = None,
) -> JustReleaseAsset:
    """Return the exact official release asset for a Tier-1 development host."""

    system = system_name or platform.system()
    architecture = _canonical_machine(machine or platform.machine())
    try:
        return _ASSETS[(system, architecture)]
    except KeyError as error:
        raise JustBootstrapError(
            "仓库本地 just 仅支持 Windows x86_64 与 Linux x86_64；"
            f"当前平台为 {system} {architecture}。"
        ) from error


def _require_directory(path: Path, *, label: str) -> None:
    """Create or validate a writable directory without traversing symlinks."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
            metadata = path.lstat()
        except OSError as error:
            raise JustBootstrapError(f"无法创建 {label}：{error}") from error
    except OSError as error:
        raise JustBootstrapError(f"无法检查 {label}：{error}") from error

    if stat.S_ISLNK(metadata.st_mode):
        raise JustBootstrapError(f"{label} 不能是符号链接。")
    if not stat.S_ISDIR(metadata.st_mode):
        raise JustBootstrapError(f"{label} 必须是目录。")
    getuid = getattr(os, "getuid", None)
    if getuid is not None and metadata.st_uid != getuid():
        raise JustBootstrapError(f"{label} 不属于当前用户。")
    if not os.access(path, os.W_OK | os.X_OK):
        raise JustBootstrapError(f"当前用户没有 {label} 的写入权限。")


def _prepare_tool_directories(tool_root: Path) -> tuple[Path, Path]:
    """Prepare the only two paths used by the binary downloader."""

    _require_directory(tool_root, label="仓库 .northstar 工具目录")
    binary_directory = tool_root / "bin"
    download_directory = tool_root / "downloads"
    _require_directory(binary_directory, label="仓库 .northstar/bin 目录")
    _require_directory(download_directory, label="仓库 .northstar/downloads 目录")
    return binary_directory, download_directory


def _download_asset(asset: JustReleaseAsset, *, directory: Path) -> Path:
    """Download one bounded HTTPS archive and verify its exact digest."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{asset.filename}.",
        suffix=".download",
        dir=directory,
    )
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    downloaded = 0
    try:
        request = Request(asset.url, headers={"User-Agent": "northstar-quant-bootstrap"})
        try:
            response = urlopen(request, timeout=30)
        except (OSError, URLError) as error:
            raise JustBootstrapError(f"无法下载固定版本 just：{error}") from error
        with response:
            with os.fdopen(descriptor, "wb") as handle:
                # fdopen owns the descriptor from here, including every error path.
                descriptor = -1
                final_url = urlparse(response.geturl())
                if final_url.scheme != "https":
                    raise JustBootstrapError("just 下载重定向不是 HTTPS，已拒绝。")
                while chunk := response.read(64 * 1024):
                    downloaded += len(chunk)
                    if downloaded > MAX_ARCHIVE_BYTES:
                        raise JustBootstrapError("just 下载包超过允许大小，已拒绝。")
                    digest.update(chunk)
                    handle.write(chunk)
        if digest.hexdigest() != asset.sha256:
            raise JustBootstrapError("just 下载包 SHA-256 不匹配，已拒绝安装。")
        return temporary_path
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise


def _member_parts(name: str) -> tuple[str, ...]:
    """Reject archive member paths that could ever escape an extraction root."""

    if "\\" in name:
        raise JustBootstrapError("just 发布包包含不安全的反斜杠路径。")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise JustBootstrapError("just 发布包包含不安全的成员路径。")
    return path.parts


def _copy_bounded(source: BinaryIO, destination: Path) -> None:
    """Atomically materialize the one archive member we permit."""

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary_path = Path(temporary_name)
    copied = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            while chunk := source.read(64 * 1024):
                copied += len(chunk)
                if copied > MAX_EXECUTABLE_BYTES:
                    raise JustBootstrapError("just 可执行文件超过允许大小，已拒绝。")
                handle.write(chunk)
        os.chmod(temporary_path, 0o755)
        os.replace(temporary_path, destination)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _install_from_zip(archive_path: Path, *, asset: JustReleaseAsset, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            candidates: list[zipfile.ZipInfo] = []
            for member in archive.infolist():
                parts = _member_parts(member.filename)
                if parts[-1] != asset.executable_name:
                    continue
                mode = member.external_attr >> 16
                if member.is_dir() or stat.S_ISLNK(mode):
                    raise JustBootstrapError("just 发布包中的可执行文件不是普通文件。")
                if member.file_size > MAX_EXECUTABLE_BYTES:
                    raise JustBootstrapError("just 可执行文件超过允许大小，已拒绝。")
                candidates.append(member)
            if len(candidates) != 1:
                raise JustBootstrapError("just 发布包没有唯一的预期可执行文件。")
            with archive.open(candidates[0]) as source:
                _copy_bounded(source, destination)
    except zipfile.BadZipFile as error:
        raise JustBootstrapError("just 下载包不是有效 ZIP 文件。") from error


def _install_from_tar(archive_path: Path, *, asset: JustReleaseAsset, destination: Path) -> None:
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            candidates: list[tarfile.TarInfo] = []
            for member in archive.getmembers():
                parts = _member_parts(member.name)
                if parts[-1] != asset.executable_name:
                    continue
                if not member.isfile():
                    raise JustBootstrapError("just 发布包中的可执行文件不是普通文件。")
                if member.size > MAX_EXECUTABLE_BYTES:
                    raise JustBootstrapError("just 可执行文件超过允许大小，已拒绝。")
                candidates.append(member)
            if len(candidates) != 1:
                raise JustBootstrapError("just 发布包没有唯一的预期可执行文件。")
            source = archive.extractfile(candidates[0])
            if source is None:
                raise JustBootstrapError("无法读取 just 发布包中的可执行文件。")
            with source:
                _copy_bounded(source, destination)
    except (OSError, tarfile.TarError) as error:
        raise JustBootstrapError("just 下载包不是有效 tar.gz 文件。") from error


def install_repository_just(
    *,
    tool_root: Path,
    system_name: str | None = None,
    machine: str | None = None,
) -> Path:
    """Install the verified pinned just binary and return its local path."""

    asset = release_asset_for_platform(system_name=system_name, machine=machine)
    binary_directory, download_directory = _prepare_tool_directories(tool_root)
    archive_path = _download_asset(asset, directory=download_directory)
    destination = binary_directory / asset.executable_name
    try:
        if asset.archive_format == "zip":
            _install_from_zip(archive_path, asset=asset, destination=destination)
        else:
            _install_from_tar(archive_path, asset=asset, destination=destination)
    finally:
        archive_path.unlink(missing_ok=True)
    print(f"已安装仓库本地 just {JUST_VERSION}：{destination}")
    return destination


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安装经 SHA-256 校验的仓库本地 just。")
    parser.add_argument("--tool-root", required=True, type=Path, help="未跟踪的 .northstar 目录。")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        install_repository_just(tool_root=args.tool_root)
    except (JustBootstrapError, OSError) as error:
        print(f"仓库本地 just 安装失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
