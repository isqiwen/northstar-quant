#!/usr/bin/env bash
set -euo pipefail

# 只采集系统、systemd 和磁盘摘要；不得读取 .env、凭据、数据库内容或账户状态。
if [ "$#" -ne 2 ]; then
  printf '%s\n' '用法：diagnose.sh <systemd-service-name> <app-root>' >&2
  exit 64
fi

service_name="$1"
app_root="$2"
if [[ ! "${service_name}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  printf '%s\n' '拒绝不安全的 systemd 服务名。' >&2
  exit 64
fi
if [[ ! "${app_root}" =~ ^/srv/[A-Za-z0-9._/-]+$ ]] || [[ "${app_root}" == *".."* ]]; then
  printf '%s\n' '拒绝不安全的应用根目录。' >&2
  exit 64
fi

printf '%s\n' '== 系统 =='
uname -a
printf '%s\n' '== 服务 =='
systemctl is-active "${service_name}.service" || true
systemctl show "${service_name}.service" \
  --property=ActiveState,SubState,ExecMainStatus,ExecMainStartTimestamp --no-pager
printf '%s\n' '== 磁盘 =='
df -h "${app_root}" || true
printf '%s\n' '== 最近错误日志 =='
journalctl --no-pager --output=short-iso --priority=err --unit "${service_name}.service" --lines=40 || true
