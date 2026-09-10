"""Non-expiring local file ownership; no automatic failover or trading authority."""

import fcntl
import os
import stat
from pathlib import Path


class FileLock:
    """A local process lock has no expiry and cannot authorize trading failover."""

    def __init__(self, database: Path) -> None:
        path = Path(str(database) + ".owner")
        self._path = path
        self._fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(self._fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("Local ownership lock must be an owned regular file")
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._identity = (info.st_dev, info.st_ino)
        except BaseException:
            os.close(self._fd)
            raise

    def check(self) -> None:
        if self._fd < 0:
            raise ValueError("Local ownership lock is closed")
        info = self._path.lstat()
        if (info.st_dev, info.st_ino) != self._identity:
            raise ValueError("Local ownership lock was replaced")

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
