"""Fail-closed host policy for the Linux deployment control plane.

This module is standard-library-only because it is included in the signed
control bundle and used before any package installation, filesystem mutation,
SSH connection, or privileged release operation.
"""

from __future__ import annotations

import platform


SUPPORTED_SYSTEM = "Linux"
SUPPORTED_MACHINES = frozenset({"x86_64", "amd64"})


class PlatformSupportError(RuntimeError):
    """Raised when a deployment operation is attempted on an unsupported host."""


def canonical_machine(value: str) -> str:
    """Return the normalized machine identifier used by the host contract."""

    return value.strip().lower().replace("-", "_")


def require_linux_x86_64(
    *,
    system_name: str | None = None,
    machine: str | None = None,
) -> None:
    """Reject every host except Linux x86_64 before a deployment operation."""

    system = system_name or platform.system()
    architecture = canonical_machine(machine or platform.machine())
    if system == SUPPORTED_SYSTEM and architecture in SUPPORTED_MACHINES:
        return
    raise PlatformSupportError(
        "Northstar Quant 部署控制面仅支持 Linux x86_64；"
        f"当前主机为 {system or 'UNKNOWN'} {architecture or 'UNKNOWN'}。"
    )
