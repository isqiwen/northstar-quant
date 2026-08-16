#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/ntfy/lib.sh"

APP_NAME="${APP_NAME:-northstar-quant}"
SERVICE_USER="${SERVICE_USER:-northstar}"
SERVICE_HOME="${SERVICE_HOME:-/srv/${SERVICE_USER}}"
APP_ROOT="${APP_ROOT:-${SERVICE_HOME}/${APP_NAME}}"
SYSTEMD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME:-${APP_NAME}}"
SERVICE_MODE="${SERVICE_MODE:-health}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
UV_VERSION="${UV_VERSION:-}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
SETUP_SERVER="${SETUP_SERVER:-0}"
CONFIRM_LIVE_DEPLOY="${CONFIRM_LIVE_DEPLOY:-NO}"
ARTIFACT_TARBALL="${ARTIFACT_TARBALL:-}"
ARTIFACT_SHA256="${ARTIFACT_SHA256:-}"
RELEASE_ID="${RELEASE_ID:-}"
ENV_FILE_PATH="${ENV_FILE_PATH:-}"
SHARED_ENV_FILE="${APP_ROOT}/shared/.env"
DASHBOARD_DEPLOY_ENABLED="${DASHBOARD_DEPLOY_ENABLED:-0}"
NTFY_DEPLOY_ENABLED="${NTFY_DEPLOY_ENABLED:-0}"
NTFY_PUBLIC_HOST="${NTFY_PUBLIC_HOST:-}"
NTFY_ACME_EMAIL="${NTFY_ACME_EMAIL:-}"
NTFY_IMAGE="${NTFY_IMAGE:-binwiederhier/ntfy:v2.27.0}"
NTFY_CADDY_IMAGE="${NTFY_CADDY_IMAGE:-caddy:2.10.2-alpine}"
NTFY_CONFIG_DIR="${NTFY_CONFIG_DIR:-/etc/northstar-ntfy}"
NTFY_DATA_DIR="${NTFY_DATA_DIR:-/var/lib/northstar-ntfy}"
NTFY_CACHE_DURATION="${NTFY_CACHE_DURATION:-24h}"
UPLOAD_NTFY_BOOTSTRAP="${UPLOAD_NTFY_BOOTSTRAP:-0}"
NTFY_BOOTSTRAP_PATH="${NTFY_BOOTSTRAP_PATH:-}"
NTFY_APP_ENV_FILE=""
# NTFY_BOOTSTRAP_FILE 仅在本地 deploy.sh 中读取；远端只接收一次性 NTFY_BOOTSTRAP_PATH。

deploy_assert_bool "SETUP_SERVER" "${SETUP_SERVER}"
deploy_assert_bool "DASHBOARD_DEPLOY_ENABLED" "${DASHBOARD_DEPLOY_ENABLED}"
deploy_assert_bool "UPLOAD_NTFY_BOOTSTRAP" "${UPLOAD_NTFY_BOOTSTRAP}"

if [ "$(uname -s)" != "Linux" ]; then
  deploy_fail "远程部署目标必须是 Linux。"
fi

deploy_need_cmd realpath
SERVICE_HOME="$(realpath -m -- "${SERVICE_HOME}")"
APP_ROOT="$(realpath -m -- "${APP_ROOT}")"
SHARED_ENV_FILE="${APP_ROOT}/shared/.env"
ntfy_validate_deployment_config

if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ] && [ "${NTFY_DEPLOY_ENABLED}" != "1" ]; then
  deploy_fail "UPLOAD_NTFY_BOOTSTRAP=1 时必须同时设置 NTFY_DEPLOY_ENABLED=1。"
fi
if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ] && [ -z "${NTFY_BOOTSTRAP_PATH}" ]; then
  deploy_fail "UPLOAD_NTFY_BOOTSTRAP=1 时必须提供远端一次性 NTFY_BOOTSTRAP_PATH。"
fi

case "${SERVICE_HOME}" in
  /srv/*)
    ;;
  *)
    deploy_fail "SERVICE_HOME 必须位于 /srv 下。"
    ;;
esac
case "${APP_ROOT}" in
  "${SERVICE_HOME}"/*)
    ;;
  *)
    deploy_fail "APP_ROOT 必须位于 SERVICE_HOME 下。"
    ;;
esac
case "${SERVICE_HOME}${APP_ROOT}" in
  *[!A-Za-z0-9/._-]*)
    deploy_fail "部署路径只能包含字母、数字、点、下划线、连字符和斜杠。"
    ;;
esac

if [ "${SETUP_SERVER}" = "1" ]; then
  deploy_log "初始化服务器运行时"
  deploy_as_root env \
    APP_NAME="${APP_NAME}" \
    SERVICE_USER="${SERVICE_USER}" \
    SERVICE_HOME="${SERVICE_HOME}" \
    APP_ROOT="${APP_ROOT}" \
    PYTHON_VERSION="${PYTHON_VERSION}" \
    UV_VERSION="${UV_VERSION}" \
    RUNTIME_STORAGE_DIR="${RUNTIME_STORAGE_DIR:-}" \
    RUNTIME_DOWNLOADS_DIR="${RUNTIME_DOWNLOADS_DIR:-}" \
    RUNTIME_REPORTS_DIR="${RUNTIME_REPORTS_DIR:-}" \
    RUNTIME_LOG_DIR="${RUNTIME_LOG_DIR:-}" \
    RUNTIME_CACHE_DIR="${RUNTIME_CACHE_DIR:-}" \
    RUNTIME_MATPLOTLIB_DIR="${RUNTIME_MATPLOTLIB_DIR:-}" \
    bash "${SCRIPT_DIR}/install-runtime.sh"
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  deploy_fail "服务用户不存在：${SERVICE_USER}。首次部署请设置 SETUP_SERVER=1。"
fi

if [ -n "${ENV_FILE_PATH}" ]; then
  if [ ! -f "${ENV_FILE_PATH}" ]; then
    deploy_fail "待安装的生产环境文件不存在：${ENV_FILE_PATH}"
  fi
fi

if [ "${NTFY_DEPLOY_ENABLED}" = "1" ]; then
  # 先用候选 .env 验证/部署 ntfy，避免 TLS、Docker 或安全策略失败时覆盖正在运行应用的配置。
  NTFY_APP_ENV_FILE="${ENV_FILE_PATH:-${SHARED_ENV_FILE}}"
  if [ ! -f "${NTFY_APP_ENV_FILE}" ]; then
    deploy_fail "私有 ntfy 缺少可验证的活动 .env；首次部署请同时设置 UPLOAD_ENV=1。"
  fi
  deploy_log "部署私有 ntfy 与 Caddy TLS 反向代理"
  deploy_as_root env \
    APP_ENV_FILE="${NTFY_APP_ENV_FILE}" \
    SERVICE_USER="${SERVICE_USER}" \
    NTFY_DEPLOY_ENABLED="${NTFY_DEPLOY_ENABLED}" \
    NTFY_PUBLIC_HOST="${NTFY_PUBLIC_HOST}" \
    NTFY_ACME_EMAIL="${NTFY_ACME_EMAIL}" \
    NTFY_IMAGE="${NTFY_IMAGE}" \
    NTFY_CADDY_IMAGE="${NTFY_CADDY_IMAGE}" \
    NTFY_CONFIG_DIR="${NTFY_CONFIG_DIR}" \
    NTFY_DATA_DIR="${NTFY_DATA_DIR}" \
    NTFY_CACHE_DURATION="${NTFY_CACHE_DURATION}" \
    UPLOAD_NTFY_BOOTSTRAP="${UPLOAD_NTFY_BOOTSTRAP}" \
    NTFY_BOOTSTRAP_PATH="${NTFY_BOOTSTRAP_PATH}" \
    bash "${SCRIPT_DIR}/ntfy/provision-ntfy.sh"
fi

if [ -n "${ENV_FILE_PATH}" ]; then
  deploy_log "安装生产环境文件"
  deploy_as_root install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 \
    "${APP_ROOT}/shared"
  deploy_as_root install -m 0600 -o "${SERVICE_USER}" -g "${SERVICE_USER}" \
    "${ENV_FILE_PATH}" "${SHARED_ENV_FILE}"
fi

if [ ! -f "${SHARED_ENV_FILE}" ]; then
  deploy_fail "服务器缺少 ${SHARED_ENV_FILE}。首次部署请设置 UPLOAD_ENV=1。"
fi

deploy_log "安装应用版本"
deploy_as_root env \
  APP_NAME="${APP_NAME}" \
  SERVICE_USER="${SERVICE_USER}" \
  SERVICE_HOME="${SERVICE_HOME}" \
  APP_ROOT="${APP_ROOT}" \
  SYSTEMD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME}" \
  SERVICE_MODE="${SERVICE_MODE}" \
  PYTHON_VERSION="${PYTHON_VERSION}" \
  KEEP_RELEASES="${KEEP_RELEASES}" \
  CONFIRM_LIVE_DEPLOY="${CONFIRM_LIVE_DEPLOY}" \
  RUNTIME_STORAGE_DIR="${RUNTIME_STORAGE_DIR:-}" \
  RUNTIME_DOWNLOADS_DIR="${RUNTIME_DOWNLOADS_DIR:-}" \
  RUNTIME_REPORTS_DIR="${RUNTIME_REPORTS_DIR:-}" \
  RUNTIME_LOG_DIR="${RUNTIME_LOG_DIR:-}" \
  RUNTIME_CACHE_DIR="${RUNTIME_CACHE_DIR:-}" \
  RUNTIME_MATPLOTLIB_DIR="${RUNTIME_MATPLOTLIB_DIR:-}" \
  DASHBOARD_DEPLOY_ENABLED="${DASHBOARD_DEPLOY_ENABLED}" \
  ARTIFACT_TARBALL="${ARTIFACT_TARBALL}" \
  ARTIFACT_SHA256="${ARTIFACT_SHA256}" \
  RELEASE_ID="${RELEASE_ID}" \
  bash "${SCRIPT_DIR}/install-release.sh"

deploy_log "远程部署流程完成"
