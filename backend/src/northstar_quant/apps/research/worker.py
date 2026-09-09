"""Independent local supervisor; OS lifetime lock prevents overlapping recovery owners."""

from __future__ import annotations

import fcntl
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from threading import Event

from northstar_quant.logging_ import configure
from northstar_quant.research.storage import open_store, require_current
from northstar_quant.research.tasks.resources import capacity
from northstar_quant.research.tasks.store import TaskStore


def run() -> None:
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logs = configure("research", "worker")
    engine = open_store()
    children: dict[subprocess.Popen[bytes], tuple[str, int]] = {}
    lock_path = Path(os.environ["NORTHSTAR_RESEARCH_DATABASE"]).with_suffix(".worker.lock")
    try:
        require_current(engine)
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("已有研究执行器或未退出的计算进程持有执行权") from None
            store = TaskStore(engine)
            store.recover()
            while not stop.is_set():
                for process, (identity, budget) in list(children.items()):
                    if process.poll() is None:
                        continue
                    completed_task = store.get(identity)
                    if completed_task["status"] in {"RUNNING", "CANCEL_REQUESTED", "FINALIZING"}:
                        store.finish(
                            identity,
                            completed_task["attempt_id"],
                            "INTERRUPTED",
                            f"计算进程退出：{process.returncode}",
                        )
                    del children[process]
                task = store.queued()
                if task:
                    # Initial conservative budget includes Python/Arrow and retained result facts.
                    # Memory/disk availability, not a fixed fraction of the host, gates admission.
                    budget = store.memory_budget(task["total"])
                    reserved = sum(value[1] for value in children.values())
                    cpus, available, disk = capacity(lock_path.parent)
                    if (
                        len(children) < cpus
                        and available > budget + reserved + 128 * 1024**2
                        and disk > budget
                    ):
                        claimed = store.claim()
                        if claimed is not None:
                            environment = {
                                k: v
                                for k, v in os.environ.items()
                                if not k.startswith(("NORTHSTAR_LIVE", "NORTHSTAR_SIMNOW"))
                                and k
                                not in {
                                    "NORTHSTAR_DATABASE_URL",
                                    "NORTHSTAR_PUBLICATION_TOKEN",
                                    "NORTHSTAR_DATA_HUB_URL",
                                }
                            }
                            process = subprocess.Popen(
                                [
                                    sys.executable,
                                    "-m",
                                    "northstar_quant.apps.research.worker",
                                    claimed["task_id"],
                                ],
                                env=environment,
                                pass_fds=(lock.fileno(),),
                            )
                            children[process] = (claimed["task_id"], budget)
                    else:
                        store.waiting(
                            task["task_id"], "等待可用 CPU、内存或本机磁盘；保持管理与取消可用"
                        )
                stop.wait(0.25)
            for process in children:
                process.terminate()
            for process in children:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            store.recover()
    finally:
        engine.dispose()
        logging.getLogger(__name__).info("Research worker stopped")
        logs.close()


if __name__ == "__main__":
    from northstar_quant.research.tasks.execution import child

    child(sys.argv[1])
