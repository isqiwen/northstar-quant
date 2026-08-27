# 运维脚本

这些 Python 入口可从 Windows 或 Linux 开发机通过 SSH 调用 Linux 生产目标；目标由同一份
非机密 `deploy.env` 清单定义。它们不读取或复制本地 `.env`，也不在 Windows 上要求 systemd、
本机服务或 Linux 权限。

```bash
python scripts/dev/run_just.py ops-health
python scripts/dev/run_just.py ops-logs
python scripts/dev/run_just.py ops-diagnose
python scripts/dev/run_just.py ops-backup
```

先运行 `python scripts/dev/run_just.py env-bootstrap` 后，对应的直接命令为
`python scripts/dev/run_uv.py run --offline --no-sync python scripts/ops/health.py`、`logs.py`、`diagnose.py` 与
`backup.py`。四者都支持 `--inventory deploy.env` 和 `--dry-run`。实际远程调用需要工作站的
OpenSSH 客户端，以及目标主机为部署用户配置的非交互 `sudo`。

`health`、`logs`、`diagnose` 和 `backup` 都是只读操作。`backup` 只读取
`northstar ops backup status` 的无秘密备份/恢复演练证据；它不会执行 `pg_dump`、对象存储写入或
恢复。`remote/linux/restore.sh` 始终失败关闭，生产恢复必须按独立、已审批的 PostgreSQL
runbook 在隔离环境中完成。

远端脚本只接受固定的 `northstar-quant` systemd 服务、`northstar` 服务账户与
`/opt/northstar` 应用根目录；任何旧 `/srv` 布局或其他主机服务都会被拒绝。
