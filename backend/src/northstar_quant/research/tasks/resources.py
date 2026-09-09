"""Local measurements used for research admission and execution evidence."""

from __future__ import annotations

import os
import platform
import resource
import shutil
from pathlib import Path

import psutil  # type: ignore[import-untyped]


def sample() -> dict[str, int | str]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "platform": platform.platform(),
        "pid": os.getpid(),
        "peak_rss_bytes": int(usage.ru_maxrss * (1 if platform.system() == "Darwin" else 1024)),
    }


def capacity(root: Path) -> tuple[int, int, int]:
    cpus = (
        len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    )
    available = int(psutil.virtual_memory().available)
    # psutil can report host memory in a container; respect its cgroup v2 ceiling too.
    try:
        ceiling = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if ceiling != "max":
            used = int(Path("/sys/fs/cgroup/memory.current").read_text())
            available = min(available, max(0, int(ceiling) - used))
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            cpus = min(cpus, max(1, int(quota) // int(period)))
    except (OSError, ValueError):
        pass
    return cpus, available, shutil.disk_usage(root).free
