#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
APP_CONFIG_EXAMPLE="${ROOT_DIR}/configs/app.example.yaml"
APP_CONFIG="${ROOT_DIR}/configs/app.yaml"
LEGACY_APP_CONFIG="${ROOT_DIR}/configs/app.local.yaml"

source "${ROOT_DIR}/scripts/dev/common.sh"
source "${ROOT_DIR}/scripts/dev/docker.sh"
source "${ROOT_DIR}/scripts/dev/env.sh"
source "${ROOT_DIR}/scripts/dev/postgres.sh"

ensure_active_app_config() {
  if [ -e "${LEGACY_APP_CONFIG}" ]; then
    fail "发现已废弃的 configs/app.local.yaml。请将需要保留的值完整迁入 configs/app.yaml，然后删除该文件。"
  fi
  if [ ! -f "${APP_CONFIG_EXAMPLE}" ]; then
    fail "未找到 configs/app.example.yaml；无法创建活动应用配置。"
  fi
  if [ -f "${APP_CONFIG}" ]; then
    return
  fi

  cp "${APP_CONFIG_EXAMPLE}" "${APP_CONFIG}"
  log "已从 configs/app.example.yaml 创建本地活动配置 configs/app.yaml。"
}

if [ "$#" -ne 0 ]; then
  fail "scripts/setup_dev.sh 不接受参数；开发环境固定使用 Docker PostgreSQL。"
fi

cd "${ROOT_DIR}"

log "检查本地开发工具..."
require_supported_os
require_command "uv" "未找到 uv。请先安装 uv，再重新运行 scripts/setup_dev.sh。"
require_docker
ensure_active_app_config
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
