#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/runtime_paths.sh"

APP_NAME="${APP_NAME:-northstar-quant}"
SERVICE_USER="${SERVICE_USER:-northstar}"
SERVICE_HOME="${SERVICE_HOME:-/srv/${SERVICE_USER}}"
APP_ROOT="${APP_ROOT:-${SERVICE_HOME}/${APP_NAME}}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
UV_VERSION="${UV_VERSION:-}"

deploy_need_cmd realpath
SERVICE_HOME="$(realpath -m -- "${SERVICE_HOME}")"
APP_ROOT="$(realpath -m -- "${APP_ROOT}")"

deploy_assert_safe_name "APP_NAME" "${APP_NAME}"
deploy_assert_safe_name "SERVICE_USER" "${SERVICE_USER}"

case "${SERVICE_HOME}" in
  /srv/*)
    ;;
  *)
    deploy_fail "SERVICE_HOME 必须位于 /srv 下，当前值为：${SERVICE_HOME}"
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

deploy_configure_runtime_paths "${APP_ROOT}"

if [ "$(uname -s)" != "Linux" ]; then
  deploy_fail "生产运行时安装脚本只支持 Linux。"
fi

if [ ! -r /etc/os-release ]; then
  deploy_fail "无法识别 Linux 发行版。"
fi

# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}" in
  ubuntu|debian)
    ;;
  *)
    deploy_fail "当前仅支持 Ubuntu 或 Debian，检测到：${PRETTY_NAME:-unknown}"
    ;;
esac

deploy_log "安装 Linux 运行时依赖"
deploy_as_root apt-get update
deploy_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential \
  ca-certificates \
  curl \
  libpq-dev \
  postgresql-client \
  python3 \
  python3-venv \
  tar \
  tzdata

install_uv() {
  local current_version=""
  local install_url="https://astral.sh/uv/install.sh"
  local installer

  if command -v uv >/dev/null 2>&1; then
    current_version="$(uv --version | awk '{print $2}')"
  fi
  if [ -n "${UV_VERSION}" ] && [ "${current_version}" = "${UV_VERSION}" ]; then
    deploy_log "复用 uv ${UV_VERSION}"
    return 0
  fi
  if [ -z "${UV_VERSION}" ] && [ -n "${current_version}" ]; then
    deploy_log "复用 uv ${current_version}"
    return 0
  fi
  if [ -n "${UV_VERSION}" ]; then
    install_url="https://astral.sh/uv/${UV_VERSION}/install.sh"
  fi

  installer="$(mktemp)"
  curl -LsSf "${install_url}" -o "${installer}"
  deploy_as_root env UV_UNMANAGED_INSTALL=/usr/local/bin sh "${installer}"
  rm -f "${installer}"
}

deploy_log "安装 uv"
install_uv

deploy_log "创建服务用户和持久化目录"
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  deploy_as_root adduser \
    --system \
    --group \
    --home "${SERVICE_HOME}" \
    --shell /usr/sbin/nologin \
    "${SERVICE_USER}"
fi

for runtime_dir in \
  "${SERVICE_HOME}" \
  "${APP_ROOT}" \
  "${APP_ROOT}/releases" \
  "${SHARED_DIR}" \
  "${SHARED_DIR}/incoming" \
  "${RUNTIME_LOG_DIR}" \
  "${RUNTIME_REPORTS_DIR}" \
  "${RUNTIME_STORAGE_DIR}" \
  "${RUNTIME_DOWNLOADS_DIR}" \
  "${RUNTIME_CACHE_DIR}" \
  "${RUNTIME_MATPLOTLIB_DIR}" \
  "${SHARED_DIR}/python" \
  "${SHARED_DIR}/uv-cache"; do
  deploy_as_root install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 "${runtime_dir}"
done

deploy_log "准备 Python ${PYTHON_VERSION}"
deploy_as_user "${SERVICE_USER}" env \
  UV_CACHE_DIR="${SHARED_DIR}/uv-cache" \
  UV_PYTHON_INSTALL_DIR="${SHARED_DIR}/python" \
  /usr/local/bin/uv python install "${PYTHON_VERSION}"

deploy_log "Linux 运行时安装完成"
printf "uv=%s\n" "$(/usr/local/bin/uv --version)"
printf "service_user=%s\n" "${SERVICE_USER}"
printf "app_root=%s\n" "${APP_ROOT}"
