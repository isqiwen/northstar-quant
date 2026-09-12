"""One account owner across instance databases on the supported single Live host."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from northstar_quant.persistence.locks import FileLock

from .storage import require_local_path

# Every kernel container mounts the same host directory at this exact path.
# It must never be placed under an instance's private database directory.
ACCOUNT_DIRECTORY = Path("/opt/northstar/state/live/accounts")


class AccountOwnership:
    """Process-lifetime exclusion, never a time-based lease or trading admission."""

    def __init__(self, broker_profile: str, broker_id: str, account_id: str) -> None:
        if not all((broker_profile, broker_id, account_id)):
            raise ValueError("Account ownership requires broker profile, broker and account")
        key = hashlib.sha256(
            json.dumps([broker_profile, broker_id, account_id], separators=(",", ":")).encode()
        ).hexdigest()
        path = ACCOUNT_DIRECTORY / key
        require_local_path(path)
        info = ACCOUNT_DIRECTORY.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Live account directory must be owner-only")
        self._directory_identity = (info.st_dev, info.st_ino)
        self._directory = ACCOUNT_DIRECTORY
        try:
            self._lock = FileLock(path)
        except BlockingIOError as exc:
            raise ValueError("This broker account already has an active Live instance") from exc

    def check(self) -> None:
        info = self._directory.stat()
        if (info.st_dev, info.st_ino) != self._directory_identity:
            raise ValueError("Live account ownership directory was replaced")
        self._lock.check()

    def close(self) -> None:
        self._lock.close()
