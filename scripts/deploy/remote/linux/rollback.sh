#!/bin/bash -p
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

# install-release.sh 已在新版本启动失败时执行受验证的自动回滚。手动回滚会改动
# current 软链接和运行服务，尚未形成可审计的发布选择协议，因此必须失败关闭。
remote_linux_require_confirmation "CONFIRM_MANUAL_ROLLBACK" "YES"
deploy_fail "手动回滚尚未启用；仅支持 install-release.sh 的失败自动回滚。"
