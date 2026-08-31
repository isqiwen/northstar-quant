"""Fail-closed Linux x86_64 runtime support contract."""

from __future__ import annotations

import platform


SUPPORTED_SYSTEM = "Linux"
SUPPORTED_MACHINES = frozenset({"x86_64", "amd64"})


class PlatformSupportError(RuntimeError):
    """Raised when a runtime operation is attempted on an unsupported host."""


def canonical_machine(value: str) -> str:
    """Normalize the machine string used by the host policy."""

    return value.strip().lower().replace("-", "_")


def require_linux_x86_64(
    *,
    system_name: str | None = None,
    machine: str | None = None,
) -> None:
    """Allow operations only on the declared Linux x86_64 runtime."""

    system = system_name or platform.system()
    architecture = canonical_machine(machine or platform.machine())
    if system == SUPPORTED_SYSTEM and architecture in SUPPORTED_MACHINES:
        return
    raise PlatformSupportError(
        "Northstar Quant 仅支持 Linux x86_64；"
        f"当前主机为 {system or 'UNKNOWN'} {architecture or 'UNKNOWN'}。"
    )
