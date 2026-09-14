"""Explicit multi-application operations; single-app execution stays in northstarctl."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack


def run(args, configs: dict, root) -> int:
    apps = list(configs)
    if args.action == "stop":
        apps.reverse()
    commands = {
        app: [
            sys.executable,
            str(root / "scripts/northstarctl.py"),
            args.action,
            app,
            "--config",
            str(args.config),
            *(["--dry-run"] if args.dry_run else []),
            *(["--follow"] if args.follow else []),
        ]
        for app in apps
    }
    if args.action == "logs" and args.follow and not args.dry_run:
        return follow(commands)
    result = 0
    for app, command in commands.items():
        print(f"\n[{app}] {args.action}", flush=True)
        code = subprocess.run(command, check=False).returncode
        if code:
            result = code
            if args.action in {"deploy", "start", "restart"}:
                break
    return result


def follow(commands: dict[str, list[str]]) -> int:
    processes = []
    with ExitStack() as stack:

        def stream(app, process):
            for line in process.stdout:
                print(f"[{app}] {line.rstrip()}", flush=True)
            return process.wait()

        try:
            for app, command in commands.items():
                process = stack.enter_context(
                    subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        start_new_session=True,
                    )
                )
                processes.append((app, process))
            with ThreadPoolExecutor(max_workers=len(processes)) as pool:
                results = [pool.submit(stream, app, process) for app, process in processes]
                try:
                    for result in as_completed(results):
                        if result.result() != 0:
                            terminate(processes)
                            return 1
                    return 0
                except KeyboardInterrupt:
                    terminate(processes)
                    return 130
        except KeyboardInterrupt:
            terminate(processes)
            return 130
        except BaseException:
            terminate(processes)
            raise


def terminate(processes):
    for _, process in processes:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
