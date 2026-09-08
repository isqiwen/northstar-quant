"""The two operator-approved SimNow environments and private local credentials.

Neither HTTP requests nor a credentials file can select a production endpoint.
Secrets are literal values in an owner-only file: it is never sourced as shell
code, interpolated, included in a public profile, or printed in diagnostics.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SimnowProfile:
    name: str
    td_front: str
    md_front: str

    def identity(self) -> dict[str, object]:
        return {
            "name": self.name,
            "td_front": self.td_front,
            "md_front": self.md_front,
            "environment": "SIMNOW",
            "broker_id": "9999",
        }


_PROFILES = {
    "simnow_dev": SimnowProfile(
        "simnow_dev", "tcp://182.254.243.31:40001", "tcp://182.254.243.31:40011"
    ),
    "simnow_trading": SimnowProfile(
        "simnow_trading", "tcp://182.254.243.31:30001", "tcp://182.254.243.31:30011"
    ),
}


def get_profile(name: str) -> SimnowProfile:
    try:
        return _PROFILES[name]
    except (KeyError, TypeError) as error:
        raise ValueError("select an explicitly approved SimNow profile") from error


def profiles() -> list[dict[str, object]]:
    return [profile.identity() for profile in _PROFILES.values()]


@dataclass(frozen=True, slots=True)
class Credentials:
    user_id: str = field(repr=False)
    password: str = field(repr=False)
    app_id: str = field(repr=False)
    auth_code: str = field(repr=False)
    broker_id: str = field(default="9999", repr=False)

    def __post_init__(self) -> None:
        if (
            self.broker_id != "9999"
            or not isinstance(self.user_id, str)
            or re.fullmatch(r"[0-9]{1,12}", self.user_id) is None
        ):
            raise ValueError("SimNow requires its numeric investor code and BrokerID 9999")
        for name, value, maximum in (
            ("password", self.password, 40),
            ("app_id", self.app_id, 32),
            ("auth_code", self.auth_code, 16),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or not value.isascii()
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(f"SimNow {name} must fit the current CTP ASCII field")


_KEYS = {
    "NORTHSTAR_SIMNOW_USER_ID",
    "NORTHSTAR_SIMNOW_PASSWORD",
    "NORTHSTAR_SIMNOW_APP_ID",
    "NORTHSTAR_SIMNOW_AUTH_CODE",
}


def credential_path() -> Path:
    value = os.environ.get("NORTHSTAR_SIMNOW_CONFIG")
    if not value or not Path(value).is_absolute():
        raise ValueError("set NORTHSTAR_SIMNOW_CONFIG to the absolute private credentials file")
    return Path(value)


def load_credentials(path: Path | None = None) -> Credentials:
    source = credential_path() if path is None else path
    if not source.is_absolute():
        raise ValueError("SimNow credentials require an absolute private file")
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            details = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(details.st_mode)
                or os.geteuid() not in {0, details.st_uid}
                or stat.S_IMODE(details.st_mode) & 0o077
                or details.st_size > 16_384
            ):
                raise ValueError("SimNow credentials must be an owner-only bounded regular file")
            content = stream.read(16_385)
    except OSError as error:
        raise ValueError("SimNow private credentials file is missing or unreadable") from error
    if len(content) != details.st_size or len(content) > 16_384:
        raise ValueError("SimNow credentials changed or exceeded their size limit")
    try:
        document = content.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("SimNow credentials must use UTF-8 text") from error
    values: dict[str, str] = {}
    for line in document.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in _KEYS or key in values:
            raise ValueError("SimNow credentials have unknown, duplicate or malformed entries")
        values[key] = value
    if set(values) != _KEYS:
        raise ValueError("SimNow credentials require investor code, password, AppID and AuthCode")
    return Credentials(
        user_id=values["NORTHSTAR_SIMNOW_USER_ID"],
        password=values["NORTHSTAR_SIMNOW_PASSWORD"],
        app_id=values["NORTHSTAR_SIMNOW_APP_ID"],
        auth_code=values["NORTHSTAR_SIMNOW_AUTH_CODE"],
    )


def credential_status() -> dict[str, object]:
    """A setup diagnostic, not authentication or permission to send orders."""

    try:
        load_credentials()
    except ValueError as error:
        return {"configured": False, "reason": str(error)}
    return {
        "configured": True,
        "reason": "Private configuration is readable; broker login has not been verified.",
    }


def validate_instrument(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z]{1,3}[0-9]{3,4}", value) is None:
        raise ValueError("select one concrete futures instrument, for example rb2610")
    return value
