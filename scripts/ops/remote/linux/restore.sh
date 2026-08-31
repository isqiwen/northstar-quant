#!/bin/bash -p
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

case "$(uname -s):$(uname -m)" in
  Linux:x86_64|Linux:amd64)
    ;;
  *)
    printf '%s\n' '远程运维目标仅支持 Linux x86_64。' >&2
    exit 1
    ;;
esac

# 明确失败关闭。生产恢复涉及独立备份介质、恢复目标隔离、凭据和人工批准；不能由通用部署
# 工具自动执行。此占位入口存在的目的是避免有人误以为可以通过仓库脚本恢复生产数据库。
printf '%s\n' '恢复操作被拒绝：请按经审批的独立 PostgreSQL 恢复演练 runbook 执行。' >&2
exit 64
