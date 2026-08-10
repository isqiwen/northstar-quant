#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

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

deploy_assert_bool "SETUP_SERVER" "${SETUP_SERVER}"

if [ "$(uname -s)" != "Linux" ]; then
  deploy_fail "远程部署目标必须是 Linux。"
fi

deploy_need_cmd realpath
SERVICE_HOME="$(realpath -m -- "${SERVICE_HOME}")"
APP_ROOT="$(realpath -m -- "${APP_ROOT}")"
SHARED_ENV_FILE="${APP_ROOT}/shared/.env"

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
  ARTIFACT_TARBALL="${ARTIFACT_TARBALL}" \
  ARTIFACT_SHA256="${ARTIFACT_SHA256}" \
  RELEASE_ID="${RELEASE_ID}" \
  bash "${SCRIPT_DIR}/install-release.sh"

deploy_log "远程部署流程完成"
