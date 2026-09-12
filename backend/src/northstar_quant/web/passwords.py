"""Local operator password hashing; also usable by deployment without app dependencies."""

import hashlib
import re
import secrets

_FORMAT = re.compile(r"scrypt\$131072\$8\$1\$([0-9a-f]{32})\$([0-9a-f]{64})")


def validate_password_hash(encoded: str) -> None:
    if _FORMAT.fullmatch(encoded) is None:
        raise ValueError("工作台密码摘要无效")


def _derive(password: str, salt: bytes) -> bytes:
    value = password.encode("utf-8")
    if not 1 <= len(value) <= 1024:
        raise ValueError("密码必须为 1 至 1024 个 UTF-8 字节")
    return hashlib.scrypt(value, salt=salt, n=131072, r=8, p=1, dklen=32, maxmem=256 * 1024**2)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt$131072$8$1${salt.hex()}${_derive(password, salt).hex()}"


def verify_password(password: str, encoded: str) -> bool:
    validate_password_hash(encoded)
    salt, expected = encoded.rsplit("$", 2)[1:]
    try:
        actual = _derive(password, bytes.fromhex(salt)).hex()
    except (ValueError, UnicodeError):
        return False
    return secrets.compare_digest(actual, expected)
