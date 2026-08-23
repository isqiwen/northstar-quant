#!/bin/bash -p
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

# 卸载涉及服务、发布目录、持久化数据和可能的交易审计记录。没有经人工核验的
# 数据保留策略时，绝不能以脚本猜测删除范围。
remote_linux_require_confirmation "CONFIRM_UNINSTALL" "YES"
deploy_fail "卸载尚未启用；请先完成备份、审计保留和人工变更审批。"
