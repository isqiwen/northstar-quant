#!/bin/bash -p
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

# 这里故意不是 pg_dump：备份创建和恢复必须留给独立、最小权限的运维系统。
# 此脚本只读取应用已经记录的无秘密备份/恢复演练就绪证据。
if [ "$#" -ne 2 ]; then
  printf '%s\n' '用法：backup.sh <service-user> <app-root>' >&2
  exit 64
fi

readonly CANONICAL_SERVICE_USER="northstar"
readonly CANONICAL_APP_ROOT="/opt/northstar"
service_user="$1"
app_root="$2"
if [ "${service_user}" != "${CANONICAL_SERVICE_USER}" ] || \
  [ "${app_root}" != "${CANONICAL_APP_ROOT}" ]; then
  printf '%s\n' '备份就绪证据只允许从受管 Northstar 服务及固定应用根目录读取。' >&2
  exit 64
fi

readonly northstar_bin="${app_root}/current/.venv/bin/northstar"
if [ ! -x "${northstar_bin}" ]; then
  printf '%s\n' '未找到已发布的 Northstar CLI，无法读取备份就绪证据。' >&2
  exit 1
fi

exec runuser -u "${service_user}" -- "${northstar_bin}" ops backup status
