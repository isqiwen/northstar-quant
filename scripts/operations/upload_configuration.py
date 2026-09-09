"""Install an initial private environment file from SSH stdin without overwriting it."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path


def install(path: Path, content: bytes) -> None:
    if len(content) > 1024 * 1024:
        raise ValueError("应用配置超过大小限制")
    if path.parent.resolve() != path.parent or not path.parent.is_dir():
        raise ValueError("运行配置目录未准备或使用了符号链接")
    if path.is_symlink():
        raise ValueError("运行配置不能使用符号链接")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("已有运行配置必须是私有普通文件")
        print("保留已有应用运行配置", flush=True)
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".env-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            # A concurrent installer won; validate and preserve its complete file.
            install(path, content)
            return
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print("已自动安装应用运行配置（权限 600）", flush=True)


if __name__ == "__main__":
    try:
        install(Path(sys.argv[1]), sys.stdin.buffer.read(1024 * 1024 + 1))
    except (OSError, ValueError) as error:
        print(f"配置上传失败：{error}", file=sys.stderr)
        sys.exit(1)
