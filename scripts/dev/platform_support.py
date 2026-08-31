"""Fail-closed host policy for local Northstar developer tools.

This module is deliberately standard-library-only so setup and tool bootstrap can
enforce the repository's supported-host contract before project dependencies or
repository-local tools exist.
"""

from __future__ import annotations

import platform


SUPPORTED_SYSTEM = "Linux"
SUPPORTED_MACHINES = frozenset({"x86_64", "amd64"})


class PlatformSupportError(RuntimeError):
    """Raised when a command is not running on the only supported host ABI."""


def canonical_machine(value: str) -> str:
    """Return the normalized machine identifier used by the support contract."""

    return value.strip().lower().replace("-", "_")


def require_linux_x86_64(
    *,
    system_name: str | None = None,
    machine: str | None = None,
) -> None:
    """Reject every host except Linux x86_64 before a local operation begins."""

    system = system_name or platform.system()
    architecture = canonical_machine(machine or platform.machine())
    if system == SUPPORTED_SYSTEM and architecture in SUPPORTED_MACHINES:
        return
    raise PlatformSupportError(
        "Northstar Quant 仅支持 Linux x86_64；"
        f"当前主机为 {system or 'UNKNOWN'} {architecture or 'UNKNOWN'}。"
    )
