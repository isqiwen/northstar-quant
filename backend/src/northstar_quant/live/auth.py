"""Separate deployment credentials from broker secrets and browser sessions."""

from __future__ import annotations

import hmac
import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LiveAuth:
    read_token: str = field(repr=False)
    control_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for token in (self.read_token, self.control_token):
            if token is not None and (
                not isinstance(token, str)
                or not 32 <= len(token) <= 256
                or not token.isascii()
                or any(not 33 <= ord(character) <= 126 for character in token)
            ):
                raise ValueError("Live tokens must contain 32..256 printable ASCII characters")
        if not self.read_token or self.read_token == self.control_token:
            raise ValueError("Live read and control credentials must be distinct")

    @classmethod
    def from_environment(cls, *, require_control: bool = False) -> LiveAuth:
        filename = os.environ.get("NORTHSTAR_LIVE_AUTH", "")
        if not filename or not Path(filename).is_absolute():
            raise ValueError("NORTHSTAR_LIVE_AUTH must name an absolute owner-only TOML file")
        return cls.from_file(Path(filename), require_control=require_control)

    @classmethod
    def from_file(cls, filename: Path, *, require_control: bool = False) -> LiveAuth:
        if not filename.is_absolute():
            raise ValueError("Live authentication requires an absolute file")
        descriptor: int | None = None
        try:
            descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > 4096
            ):
                raise ValueError(
                    "Live authentication file must be owner-only and at most 4096 bytes"
                )
            raw = os.read(descriptor, 4097)
            document = tomllib.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
            raise ValueError("Live authentication file is unreadable or invalid") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if set(document) - {"read_token", "control_token"} or "read_token" not in document:
            raise ValueError("Live authentication file accepts read_token and control_token only")
        result = cls(document["read_token"], document.get("control_token"))
        if require_control and result.control_token is None:
            raise ValueError("Live requires separate read and control credentials")
        return result

    def authorize(self, supplied: str, *, control: bool = False) -> None:
        tokens = (self.control_token,) if control else (self.read_token, self.control_token)
        if not any(
            token is not None and hmac.compare_digest(supplied, "Bearer " + token)
            for token in tokens
        ):
            raise PermissionError("Live operation is not authorized")
