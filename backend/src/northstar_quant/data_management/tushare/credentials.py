"""Write-only browser credential, held in the core host's private persistent volume."""

import os
import re
import tempfile
from pathlib import Path

from ..files import SourceFiles


def validate(token: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9_-]{16,512}", token) is None:
        raise ValueError("Tushare token 格式不正确（16–512 位字母、数字、下划线或连字符）")


def root() -> Path:
    value = os.environ.get("NORTHSTAR_DATA_SECRET_DIR")
    if not value or not Path(value).is_absolute():
        raise ValueError("请配置 core 本地 NORTHSTAR_DATA_SECRET_DIR 凭据目录")
    path = Path(value)
    if path.is_symlink():
        raise ValueError("凭据目录不能是符号链接")
    return path


def configured() -> bool:
    try:
        return (root() / "tushare.token").is_file()
    except ValueError:
        return False


def save(token: str) -> None:
    validate(token)
    directory = root()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_mode & 0o077:
        raise ValueError("凭据目录权限必须为 0700")
    fd, name = tempfile.mkstemp(prefix=".tushare-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / "tushare.token")
        SourceFiles._sync(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def read() -> str:
    try:
        fd = os.open(root() / "tushare.token", os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            if os.fstat(stream.fileno()).st_mode & 0o077:
                raise ValueError("Tushare 凭据文件权限必须为 0600")
            token = stream.read(513)
        validate(token)
        return token
    except OSError:
        raise ValueError("请在历史同步页面设置 Tushare token") from None
