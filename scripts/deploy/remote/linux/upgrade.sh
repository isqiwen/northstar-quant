#!/bin/bash -p
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

# 正常升级不得顺带安装系统运行时；首次安装使用 install.sh。provision.sh 及其
# install-release.sh 保留制品校验、健康检查、原子切换和失败自动回滚。
if [ "${SETUP_SERVER:-0}" != "0" ]; then
  deploy_fail "正常升级不能设置 SETUP_SERVER=1；请使用 remote/linux/install.sh。"
fi

exec env SETUP_SERVER=0 /bin/bash -p "${DEPLOY_DIR}/provision.sh" "$@"
