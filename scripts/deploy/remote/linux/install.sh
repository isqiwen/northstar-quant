#!/bin/bash -p
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

# install 只封装首次目标端安装。真正的路径、权限、.env、交易模式和 systemd
# 门禁统一由 provision.sh 继续负责，避免形成第二条绕过路径。
if [ "${SETUP_SERVER:-1}" != "1" ]; then
  deploy_fail "首次安装必须使用 SETUP_SERVER=1。"
fi

exec env SETUP_SERVER=1 /bin/bash -p "${DEPLOY_DIR}/provision.sh" "$@"
