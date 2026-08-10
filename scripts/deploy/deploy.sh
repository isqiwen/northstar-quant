#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${ROOT_DIR}/scripts/deploy/lib/common.sh"
source "${ROOT_DIR}/scripts/deploy/lib/config.sh"
source "${ROOT_DIR}/scripts/deploy/lib/safety.sh"
source "${ROOT_DIR}/scripts/deploy/lib/ssh.sh"

usage() {
  cat <<'EOF'
将 Northstar Quant 一键部署到 Linux 服务器。

首次部署：
  UPLOAD_ENV=1 SETUP_SERVER=1 scripts/deploy.sh

后续发布：
  scripts/deploy.sh

常用环境变量：
  DEPLOY_CONFIG=deploy.env       非敏感部署配置
  RUNTIME_*_DIR                  可写数据、报告、日志和缓存目录（配置于 deploy.env）
  ENV_FILE=.env.production      首次上传的生产环境文件
  UPLOAD_ENV=1                  更新服务器生产环境文件
  SETUP_SERVER=1                安装 Linux、uv、Python 和服务用户
  ALLOW_DIRTY=1                 允许从未提交工作区构建
  SKIP_TESTS=1                  跳过本地 Pytest，不建议用于正式发布
  SKIP_RUFF=1                   跳过本地 Ruff，不建议用于正式发布
  DRY_RUN=1                     只检查并构建制品，不连接服务器
  CONFIRM_LIVE_DEPLOY=YES       明确允许启动非 paper 调度器
EOF
}

if [ "$#" -gt 1 ]; then
  deploy_fail "deploy.sh 只接受 --help。"
fi
if [ "$#" -eq 1 ]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      deploy_fail "未知参数：$1"
      ;;
  esac
fi

DEPLOY_CONFIG="${DEPLOY_CONFIG:-${ROOT_DIR}/deploy.env}"
if [[ "${DEPLOY_CONFIG}" != /* ]]; then
  DEPLOY_CONFIG="${ROOT_DIR}/${DEPLOY_CONFIG}"
fi
deploy_load_config "${DEPLOY_CONFIG}"

APP_NAME="${APP_NAME:-northstar-quant}"
SERVICE_USER="${SERVICE_USER:-northstar}"
SERVICE_HOME="${SERVICE_HOME:-/srv/${SERVICE_USER}}"
SYSTEMD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME:-${APP_NAME}}"
SERVICE_MODE="${SERVICE_MODE:-health}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
REMOTE_TMP="${REMOTE_TMP:-/tmp}"
DEPLOY_HOST="${DEPLOY_HOST:-}"
RUNTIME_STORAGE_DIR="${RUNTIME_STORAGE_DIR:-}"
RUNTIME_DOWNLOADS_DIR="${RUNTIME_DOWNLOADS_DIR:-}"
RUNTIME_REPORTS_DIR="${RUNTIME_REPORTS_DIR:-}"
RUNTIME_LOG_DIR="${RUNTIME_LOG_DIR:-}"
RUNTIME_CACHE_DIR="${RUNTIME_CACHE_DIR:-}"
RUNTIME_MATPLOTLIB_DIR="${RUNTIME_MATPLOTLIB_DIR:-}"

ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.production}"
UPLOAD_ENV="${UPLOAD_ENV:-0}"
SETUP_SERVER="${SETUP_SERVER:-0}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
SKIP_TESTS="${SKIP_TESTS:-0}"
SKIP_RUFF="${SKIP_RUFF:-0}"
CONFIRM_LIVE_DEPLOY="${CONFIRM_LIVE_DEPLOY:-NO}"
DRY_RUN="${DRY_RUN:-0}"
SSH_CONTROL="${SSH_CONTROL:-1}"
CLEAN_REMOTE_ON_EXIT="${CLEAN_REMOTE_ON_EXIT:-1}"
ARTIFACT_DIR="${ARTIFACT_DIR:-${ROOT_DIR}/dist}"

for bool_name in \
  UPLOAD_ENV \
  SETUP_SERVER \
  ALLOW_DIRTY \
  SKIP_TESTS \
  SKIP_RUFF \
  DRY_RUN \
  SSH_CONTROL \
  CLEAN_REMOTE_ON_EXIT; do
  deploy_assert_bool "${bool_name}" "${!bool_name}"
done

deploy_assert_safe_name "APP_NAME" "${APP_NAME}"
deploy_assert_safe_name "SERVICE_USER" "${SERVICE_USER}"
deploy_assert_safe_name "SYSTEMD_SERVICE_NAME" "${SYSTEMD_SERVICE_NAME}"

case "${SERVICE_MODE}" in
  health|scheduler)
    ;;
  *)
    deploy_fail "SERVICE_MODE 只能是 health 或 scheduler。"
    ;;
esac
case "${KEEP_RELEASES}" in
  *[!0-9]*|"")
    deploy_fail "KEEP_RELEASES 必须是整数。"
    ;;
esac
if [ "${KEEP_RELEASES}" -lt 2 ]; then
  deploy_fail "KEEP_RELEASES 至少为 2。"
fi
case "${CONFIRM_LIVE_DEPLOY}" in
  NO|YES)
    ;;
  *)
    deploy_fail "CONFIRM_LIVE_DEPLOY 只能是 NO 或 YES。"
    ;;
esac
case "${REMOTE_TMP}" in
  /tmp|/tmp/*|/var/tmp|/var/tmp/*)
    ;;
  *)
    deploy_fail "REMOTE_TMP 只能位于 /tmp 或 /var/tmp。"
    ;;
esac
case "/${REMOTE_TMP}/" in
  *"/../"*|*"/./"*)
    deploy_fail "REMOTE_TMP 不能包含 . 或 .. 路径段。"
    ;;
esac
case "${SERVICE_HOME}" in
  /srv/*)
    ;;
  *)
    deploy_fail "SERVICE_HOME 必须位于 /srv 下。"
    ;;
esac
case "/${SERVICE_HOME}/" in
  *"/../"*|*"/./"*)
    deploy_fail "SERVICE_HOME 不能包含 . 或 .. 路径段。"
    ;;
esac

if [ -z "${DEPLOY_HOST}" ]; then
  deploy_fail "DEPLOY_HOST 不能为空，请填写 ${DEPLOY_CONFIG}。"
fi
if [ "${UPLOAD_ENV}" = "1" ] && [ ! -f "${ENV_FILE}" ]; then
  deploy_fail "未找到生产环境文件：${ENV_FILE}"
fi
if [ "${UPLOAD_ENV}" = "1" ]; then
  deploy_log "校验生产环境文件"
  deploy_validate_production_env \
    "${ENV_FILE}" \
    "${SERVICE_MODE}" \
    "${CONFIRM_LIVE_DEPLOY}"
  chmod 600 "${ENV_FILE}"
fi

deploy_need_cmd git
deploy_need_cmd tar
deploy_need_cmd uv

UV_VERSION="${UV_VERSION:-$(uv --version | awk '{print $2}')}"
REVISION="$(git -C "${ROOT_DIR}" rev-parse --short=12 HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)"
STAMP="$(date -u +%Y%m%d%H%M%S)"
RELEASE_ID="${REVISION}-${STAMP}"
ARTIFACT_NAME="northstar-quant-${RELEASE_ID}.tar.gz"
ARTIFACT_PATH="${ARTIFACT_DIR}/${ARTIFACT_NAME}"

if [ "${ALLOW_DIRTY}" != "1" ] &&
  [ -n "$(git -C "${ROOT_DIR}" status --porcelain --untracked-files=normal)" ]; then
  deploy_fail "工作区存在未提交修改。请先提交，或仅在明确需要时设置 ALLOW_DIRTY=1。"
fi

cd "${ROOT_DIR}"
if [ "${SKIP_RUFF}" != "1" ]; then
  deploy_log "运行 Ruff"
  uv run ruff check .
fi
if [ "${SKIP_TESTS}" != "1" ]; then
  deploy_log "运行 Pytest"
  uv run pytest
fi

deploy_log "构建部署制品"
ARTIFACT_DIR="${ARTIFACT_DIR}" \
ARTIFACT_NAME="${ARTIFACT_NAME}" \
ARTIFACT_PATH="${ARTIFACT_PATH}" \
REVISION="${REVISION}" \
STAMP="${STAMP}" \
  bash "${ROOT_DIR}/scripts/deploy/build-artifact.sh"

if [ ! -f "${ARTIFACT_PATH}" ]; then
  deploy_fail "部署制品未生成：${ARTIFACT_PATH}"
fi

if command -v sha256sum >/dev/null 2>&1; then
  ARTIFACT_SHA256="$(sha256sum "${ARTIFACT_PATH}" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  ARTIFACT_SHA256="$(shasum -a 256 "${ARTIFACT_PATH}" | awk '{print $1}')"
else
  deploy_fail "缺少 SHA-256 工具，需要 sha256sum 或 shasum。"
fi

if [ "${DRY_RUN}" = "1" ]; then
  deploy_log "DRY_RUN 完成，未连接服务器"
  printf "artifact=%s\n" "${ARTIFACT_PATH}"
  exit 0
fi

deploy_need_cmd ssh
deploy_need_cmd scp

REMOTE_WORK_DIR="${REMOTE_TMP}/${APP_NAME}-deploy-${RELEASE_ID}"
REMOTE_ARTIFACT="${REMOTE_TMP}/${ARTIFACT_NAME}"
REMOTE_ENV="${REMOTE_TMP}/${APP_NAME}-${RELEASE_ID}.env"
SSH_CONTROL_PATH="${TMPDIR:-/tmp}/nq-ssh-${STAMP}-$$.sock"
CLEANUP_ARMED=0

cleanup_remote() {
  local cleanup_command

  if [ "${CLEAN_REMOTE_ON_EXIT}" != "1" ] || [ "${CLEANUP_ARMED}" != "1" ]; then
    return 0
  fi

  cleanup_command="rm -rf $(deploy_shell_quote "${REMOTE_WORK_DIR}")"
  cleanup_command+="; rm -f $(deploy_shell_quote "${REMOTE_ARTIFACT}")"
  if [ "${UPLOAD_ENV}" = "1" ]; then
    cleanup_command+=" $(deploy_shell_quote "${REMOTE_ENV}")"
  fi
  deploy_ssh "${DEPLOY_HOST}" "${cleanup_command}" >/dev/null 2>&1 || true
}

trap 'cleanup_remote; deploy_close_ssh_control' EXIT

if [ "${SSH_CONTROL}" = "1" ]; then
  deploy_log "建立复用 SSH 连接"
  deploy_start_ssh_control
fi

deploy_log "检查远程 Linux 和 sudo"
if [ "$(deploy_ssh "${DEPLOY_HOST}" "uname -s" | tr -d '\r')" != "Linux" ]; then
  deploy_fail "远程目标不是 Linux：${DEPLOY_HOST}"
fi
if ! deploy_ssh "${DEPLOY_HOST}" "sudo -n true" >/dev/null 2>&1; then
  deploy_fail "远程 SSH 用户必须具备非交互 sudo。请先确认 ssh ${DEPLOY_HOST} 'sudo -n true' 可通过。"
fi

deploy_log "创建远程临时目录"
deploy_ssh "${DEPLOY_HOST}" \
  "mkdir -p $(deploy_shell_quote "${REMOTE_WORK_DIR}")"
CLEANUP_ARMED=1

deploy_log "上传部署模块"
tar -C "${ROOT_DIR}" -czf - scripts/deploy |
  deploy_ssh "${DEPLOY_HOST}" \
    "tar -xzf - -C $(deploy_shell_quote "${REMOTE_WORK_DIR}")"

deploy_log "上传应用制品"
deploy_scp "${ARTIFACT_PATH}" "${DEPLOY_HOST}:${REMOTE_ARTIFACT}"

if [ "${UPLOAD_ENV}" = "1" ]; then
  deploy_log "上传生产环境文件"
  deploy_scp "${ENV_FILE}" "${DEPLOY_HOST}:${REMOTE_ENV}"
fi

REMOTE_COMMAND="sudo env"
REMOTE_COMMAND+=" APP_NAME=$(deploy_shell_quote "${APP_NAME}")"
REMOTE_COMMAND+=" SERVICE_USER=$(deploy_shell_quote "${SERVICE_USER}")"
REMOTE_COMMAND+=" SERVICE_HOME=$(deploy_shell_quote "${SERVICE_HOME}")"
REMOTE_COMMAND+=" SYSTEMD_SERVICE_NAME=$(deploy_shell_quote "${SYSTEMD_SERVICE_NAME}")"
REMOTE_COMMAND+=" SERVICE_MODE=$(deploy_shell_quote "${SERVICE_MODE}")"
REMOTE_COMMAND+=" PYTHON_VERSION=$(deploy_shell_quote "${PYTHON_VERSION}")"
REMOTE_COMMAND+=" UV_VERSION=$(deploy_shell_quote "${UV_VERSION}")"
REMOTE_COMMAND+=" KEEP_RELEASES=$(deploy_shell_quote "${KEEP_RELEASES}")"
REMOTE_COMMAND+=" SETUP_SERVER=$(deploy_shell_quote "${SETUP_SERVER}")"
REMOTE_COMMAND+=" CONFIRM_LIVE_DEPLOY=$(deploy_shell_quote "${CONFIRM_LIVE_DEPLOY}")"
REMOTE_COMMAND+=" RUNTIME_STORAGE_DIR=$(deploy_shell_quote "${RUNTIME_STORAGE_DIR}")"
REMOTE_COMMAND+=" RUNTIME_DOWNLOADS_DIR=$(deploy_shell_quote "${RUNTIME_DOWNLOADS_DIR}")"
REMOTE_COMMAND+=" RUNTIME_REPORTS_DIR=$(deploy_shell_quote "${RUNTIME_REPORTS_DIR}")"
REMOTE_COMMAND+=" RUNTIME_LOG_DIR=$(deploy_shell_quote "${RUNTIME_LOG_DIR}")"
REMOTE_COMMAND+=" RUNTIME_CACHE_DIR=$(deploy_shell_quote "${RUNTIME_CACHE_DIR}")"
REMOTE_COMMAND+=" RUNTIME_MATPLOTLIB_DIR=$(deploy_shell_quote "${RUNTIME_MATPLOTLIB_DIR}")"
REMOTE_COMMAND+=" ARTIFACT_TARBALL=$(deploy_shell_quote "${REMOTE_ARTIFACT}")"
REMOTE_COMMAND+=" ARTIFACT_SHA256=$(deploy_shell_quote "${ARTIFACT_SHA256}")"
REMOTE_COMMAND+=" RELEASE_ID=$(deploy_shell_quote "${RELEASE_ID}")"
if [ "${UPLOAD_ENV}" = "1" ]; then
  REMOTE_COMMAND+=" ENV_FILE_PATH=$(deploy_shell_quote "${REMOTE_ENV}")"
fi
REMOTE_COMMAND+=" bash $(deploy_shell_quote "${REMOTE_WORK_DIR}/scripts/deploy/provision.sh")"

deploy_log "执行远程部署"
deploy_ssh "${DEPLOY_HOST}" "${REMOTE_COMMAND}"

deploy_log "部署完成"
printf "host=%s\n" "${DEPLOY_HOST}"
printf "release=%s\n" "${RELEASE_ID}"
printf "service=%s.service\n" "${SYSTEMD_SERVICE_NAME}"
printf "mode=%s\n" "${SERVICE_MODE}"
printf "logs=ssh %s 'sudo journalctl -u %s -n 100 --no-pager'\n" \
  "${DEPLOY_HOST}" "${SYSTEMD_SERVICE_NAME}"
