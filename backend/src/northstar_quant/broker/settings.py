"""Approved SimNow profiles and kernel-only credentials from the Live environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from northstar_quant.trading.environment import Environment


def configured_environment() -> Environment:
    environment = Environment(os.environ.get("NORTHSTAR_ENVIRONMENT", "SANDBOX"))
    if environment is Environment.LIVE:
        raise ValueError(
            "Production Live is not implemented or admitted; no broker connection allowed"
        )
    if environment is not Environment.SANDBOX:
        raise ValueError("broker runtime requires SANDBOX")
    return environment


def require_supported_environment() -> None:
    """Do not silently route an unsupported or mistyped environment to SimNow."""
    configured_environment()
    environment = os.environ.get("NORTHSTAR_BROKER_PROFILE", "simnow_trading")
    if environment == "production":
        raise ValueError(
            "Production Live is not implemented or admitted; no broker connection allowed"
        )
    if environment not in {"simnow_trading", "simnow_dev"}:
        raise ValueError("NORTHSTAR_BROKER_PROFILE must be simnow_trading or simnow_dev")


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
    require_supported_environment()
    try:
        return _PROFILES[name]
    except (KeyError, TypeError) as error:
        raise ValueError("select an explicitly approved SimNow profile") from error


def configured_profile(bound_name: str | None = None) -> SimnowProfile:
    require_supported_environment()
    name = os.environ.get("NORTHSTAR_BROKER_PROFILE", "simnow_trading")
    if bound_name is not None and bound_name != name:
        raise ValueError("saved broker environment differs from the configured Live environment")
    return get_profile(name)


def profiles() -> list[dict[str, object]]:
    return [configured_profile().identity()]


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


def load_credentials() -> Credentials:
    require_supported_environment()
    values = {
        field: os.environ.get("NORTHSTAR_SIMNOW_" + field.upper(), "")
        for field in ("user_id", "password", "app_id", "auth_code")
    }
    if not all(values.values()):
        raise ValueError("Configure all four NORTHSTAR_SIMNOW credentials in the Live .env")
    return Credentials(**values)


def credential_status() -> dict[str, object]:
    """A setup diagnostic, not authentication or permission to send orders."""

    try:
        load_credentials()
    except ValueError as error:
        return {"configured": False, "reason": str(error)}
    return {
        "configured": True,
        "reason": "Live credentials configured; broker login has not been verified.",
    }


def validate_instrument(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z]{1,3}[0-9]{3,4}", value) is None:
        raise ValueError("select one concrete futures instrument, for example rb2610")
    return value
