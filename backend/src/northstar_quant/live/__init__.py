"""Live interfaces loaded on demand; configuration imports need no HTTP runtime."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .auth import LiveAuth as LiveAuth
    from .client import CommandUnknown as CommandUnknown
    from .client import LiveClient as LiveClient
    from .client import RuntimeUnavailable as RuntimeUnavailable

__all__ = ["CommandUnknown", "LiveAuth", "LiveClient", "RuntimeUnavailable"]


def __getattr__(name: str):
    if name == "LiveAuth":
        from .auth import LiveAuth

        return LiveAuth
    if name in {"CommandUnknown", "LiveClient", "RuntimeUnavailable"}:
        from . import client

        return getattr(client, name)
    raise AttributeError(name)
