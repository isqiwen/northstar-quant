#!/usr/bin/env bash
set -euo pipefail

# 这些脚本只会随部署控制面暂存到 Linux 目标。它们不接受 Windows 路径，也不应
# 直接绕过 scripts/deploy 下既有的 production / broker / 环境文件门禁。
REMOTE_LINUX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${REMOTE_LINUX_DIR}/../.." && pwd)"

# shellcheck disable=SC1091
source "${DEPLOY_DIR}/lib/common.sh"

remote_linux_require_target() {
  if [ "$(uname -s)" != "Linux" ]; then
    deploy_fail "Linux 目标操作只能在 Linux 服务器执行。"
  fi
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
