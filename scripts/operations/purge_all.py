"""Coordinate locked host cleanup; NAS is accessed only through its client mount."""

from __future__ import annotations

import json
import runpy
import subprocess
from contextlib import ExitStack


def run(args, configs, settings, root, ssh) -> int:
    topology = runpy.run_path(str(root / "scripts/operations/nfs.py"))["topology"](settings)
    hosts = {config["host"]: config for config in configs.values()}
    if topology and topology["server"] in hosts:
        raise ValueError("NFS 服务端不能同时是卸载目标")
    print(
        "整套清空：所有上述主机的 Northstar 数据、配置、账号和部署（包含已配置的 Live）", flush=True
    )
    if topology:
        print(
            f"同时清空 {topology['server']}:/quant 项目行情；保留 NAS 服务、快照和其他共享",
            flush=True,
        )
    if args.dry_run:
        return 0
    program = (root / "scripts/operations/purge_shared.py").read_text()
    host_program = (root / "scripts/operations/purge_host.py").read_text()
    elevated = (
        "import subprocess,sys; sys.exit(subprocess.call(['sudo','-n','--',"
        "'python3','-u','-c'," + repr(program) + ",sys.argv[1]]))"
    )
    with ExitStack() as stack:
        sessions = {}
        try:
            for host, config in hosts.items():
                request = {
                    "host_program": host_program,
                    "server": topology["server"] if topology else None,
                    "writer": bool(topology and host == topology["writer"]),
                    "client": bool(topology and host in {topology["writer"], topology["reader"]}),
                }
                process = stack.enter_context(
                    subprocess.Popen(
                        ssh(config, elevated, json.dumps(request)),
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                    )
                )
                sessions[host] = process
            # No host stops until all hosts hold their deployment locks and pass checks.
            identities = {ack(p, "ready") for p in sessions.values()} - {None}
            if len(identities) > 1:
                raise ValueError("应用主机的共享存储身份不一致，拒绝清空")
            for process in sessions.values():
                send(process, "stop")
            for process in sessions.values():
                ack(process, "stopped")
            if topology:
                writer = sessions[topology["writer"]]
                send(writer, "clear")
                ack(writer, "cleared")
            for process in sessions.values():
                send(process, "purge")
            for process in sessions.values():
                ack(process, "purged")
                if process.wait() != 0:
                    raise ValueError("主机卸载未完成")
        finally:
            # EOF releases locks on failures; completed stops/deletes are not rolled back.
            for process in sessions.values():
                try:
                    process.stdin.close()
                except OSError:
                    pass
    print("全部已配置应用主机及项目活跃行情已清空；NAS 服务和快照保留", flush=True)
    return 0


def send(process, command):
    process.stdin.write(command + "\n")
    process.stdin.flush()


def ack(process, expected):
    line = process.stdout.readline()
    if not line:
        raise ValueError(f"整套清空在 {expected} 阶段失败；已完成步骤不回滚")
    response = json.loads(line)
    if response["phase"] != expected:
        raise ValueError("远程清空阶段不一致")
    return response.get("identity")
