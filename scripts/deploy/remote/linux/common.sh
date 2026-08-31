#!/bin/bash -p
# Remote wrappers may be directly invoked by an operator.  Harden before
# resolving their own location or sourcing the privileged deployment helpers.
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

# 这些脚本只会随部署控制面暂存到 Linux 目标。它们拒绝非 POSIX 路径输入，也不应
# 直接绕过 scripts/deploy 下既有的 production / broker / 环境文件门禁。
REMOTE_LINUX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${REMOTE_LINUX_DIR}/../.." && pwd)"

# shellcheck disable=SC1091
source "${DEPLOY_DIR}/lib/common.sh"

remote_linux_require_target() {
  deploy_require_linux_x86_64
}

remote_linux_require_confirmation() {
  local variable_name="$1"
  local expected_value="$2"
  local current_value="${!variable_name:-}"

  if [ "${current_value}" != "${expected_value}" ]; then
    deploy_fail "此目标操作需要显式设置 ${variable_name}=${expected_value}。"
  fi
}

remote_linux_require_target
