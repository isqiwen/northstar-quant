#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
ENV_TEMPLATE="${ROOT_DIR}/.env.example"
ENV_SCHEMA_SYNC_SCRIPT="${ROOT_DIR}/scripts/dev/sync_env_schema.py"
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

ensure_active_env_schema() {
  if [ ! -f "${ENV_TEMPLATE}" ]; then
    fail "未找到 .env.example；无法创建完整的活动环境文件。"
  fi
  if [ ! -f "${ENV_SCHEMA_SYNC_SCRIPT}" ]; then
    fail "未找到 scripts/dev/sync_env_schema.py；无法校验活动环境文件。"
  fi

  uv run --no-sync python "${ENV_SCHEMA_SYNC_SCRIPT}" \
    --template "${ENV_TEMPLATE}" \
    --active "${ENV_FILE}" \
    --apply
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

log "同步 Python 开发依赖..."
uv sync --extra dev --locked

log "校验并迁移本地活动环境文件结构..."
ensure_active_env_schema
configure_dev_env

log "启动 Docker PostgreSQL..."
start_postgres

log "执行数据库迁移..."
uv run northstar init-db

log "运行开发环境检查..."
uv run northstar health
uv run pytest
uv run ruff check .

log "开发环境已就绪。"
