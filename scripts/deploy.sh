#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Linux 的公共入口统一交给跨平台 Python 控制面。Python 默认只做本地预检和
# 制品构建；只有显式 --apply 才会通过 OpenSSH 调用 Linux 目标端 Bash 操作层。
if command -v uv >/dev/null 2>&1; then
  exec uv run --no-project python "${SCRIPT_DIR}/deploy/deploy.py" "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 "${SCRIPT_DIR}/deploy/deploy.py" "$@"
fi

printf '%s\n' '部署错误：需要 uv 或 python3 来运行跨平台部署控制面。' >&2
exit 1
