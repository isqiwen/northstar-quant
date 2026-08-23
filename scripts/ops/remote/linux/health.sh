#!/bin/bash -p
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

# 仅只读：展示 systemd 状态和应用 health 命令的输出，不启动服务或修改配置。
if [ "$#" -ne 1 ]; then
  printf '%s\n' '用法：health.sh <systemd-service-name>' >&2
  exit 64
fi

service_name="$1"
if [ "${service_name}" != "northstar-quant" ]; then
  printf '%s\n' '远程健康检查只允许受管 Northstar systemd 服务。' >&2
  exit 64
fi

if systemctl is-active --quiet "${service_name}.service"; then
  printf '%s\n' 'active'
  systemctl --no-pager --full status "${service_name}.service" --lines=40
  exit 0
fi

printf '%s\n' 'inactive' >&2
systemctl --no-pager --full status "${service_name}.service" --lines=40 || true
exit 3
