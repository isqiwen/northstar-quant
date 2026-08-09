#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

source "${ROOT_DIR}/scripts/dev/common.sh"
source "${ROOT_DIR}/scripts/dev/docker.sh"
source "${ROOT_DIR}/scripts/dev/env.sh"
source "${ROOT_DIR}/scripts/dev/postgres.sh"

if [ "$#" -ne 0 ]; then
  fail "scripts/setup_dev.sh 不接受参数；开发环境固定使用 Docker PostgreSQL。"
fi

cd "${ROOT_DIR}"

log "检查本地开发工具..."
require_supported_os
require_command "uv" "未找到 uv。请先安装 uv，再重新运行 scripts/setup_dev.sh。"
require_docker
configure_dev_env

log "同步 Python 开发依赖..."
uv sync --extra dev --locked

log "启动 Docker PostgreSQL..."
start_postgres

log "执行数据库迁移..."
uv run northstar init-db

log "运行开发环境检查..."
uv run northstar health
uv run pytest
uv run ruff check .

log "开发环境已就绪。"
