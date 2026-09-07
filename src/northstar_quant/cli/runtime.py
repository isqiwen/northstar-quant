"""Local runtime entrypoints and private deployment authentication bootstrap."""

from __future__ import annotations

import os
import secrets
import stat
import tomllib
from pathlib import Path


def initialize_auth(directory: Path) -> dict[str, str]:
    """Create once; reinitialization verifies existing files without rotating active authority."""
    from northstar_quant.live import LiveAuth

    directory = directory.absolute()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = directory.stat()
    if directory.is_symlink() or metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
        raise ValueError(
            "runtime authentication directory must be owned and not writable by others"
        )
    paths = [directory / "live.toml", directory / "console.toml"]
    if any(path.exists() or path.is_symlink() for path in paths):
        saved = []
        for path in paths:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError(
                    "existing runtime authentication files must be owner-only regular files"
                )
            with path.open("rb") as stream:
                values = tomllib.load(stream)
            if set(values) != {"read_token", "control_token"}:
                raise ValueError("existing runtime authentication bundle is incomplete")
            saved.append(LiveAuth(**values))
        if saved[0] != saved[1]:
            raise ValueError("existing Live and Console authentication files do not match")
    else:
        read_token, control_token = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        content = f'read_token = "{read_token}"\ncontrol_token = "{control_token}"\n'
        for path in paths:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return {"status": "ready", "live_auth": str(paths[0]), "console_auth": str(paths[1])}


def serve(role: str, port: int) -> None:
    import uvicorn

    if not 1024 <= port <= 65535:
        raise ValueError("port must be between 1024 and 65535")
    module = "live" if role == "live" else "web"
    uvicorn.run(f"northstar_quant.{module}:application", factory=True, host="127.0.0.1", port=port)
