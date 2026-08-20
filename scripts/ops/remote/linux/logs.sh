#!/usr/bin/env bash
set -euo pipefail

# 仅只读：journalctl 不会改动服务或保留策略。
if [ "$#" -ne 2 ]; then
  printf '%s\n' '用法：logs.sh <systemd-service-name> <lines>' >&2
  exit 64
fi

service_name="$1"
lines="$2"
if [[ ! "${service_name}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  printf '%s\n' '拒绝不安全的 systemd 服务名。' >&2
  exit 64
fi
if [[ ! "${lines}" =~ ^[1-9][0-9]{0,2}$|^1000$ ]]; then
  printf '%s\n' '日志行数必须是 1 到 1000。' >&2
  exit 64
fi

journalctl --no-pager --output=short-iso --unit "${service_name}.service" --lines="${lines}"
