#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

# install 只封装首次目标端安装。真正的路径、权限、.env、交易模式和 systemd
# 门禁统一由 provision.sh 继续负责，避免形成第二条绕过路径。
if [ "${SETUP_SERVER:-1}" != "1" ]; then
  deploy_fail "首次安装必须使用 SETUP_SERVER=1。"
fi

exec env SETUP_SERVER=1 bash "${DEPLOY_DIR}/provision.sh" "$@"
