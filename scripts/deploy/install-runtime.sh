#!/bin/bash -p
# Run privileged deployment entrypoints with a deterministic command lookup
# and without non-interactive Bash startup hooks inherited from the caller.
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/service_identity.sh"
source "${SCRIPT_DIR}/lib/runtime_paths.sh"
source "${SCRIPT_DIR}/lib/privileged_paths.sh"
source "${SCRIPT_DIR}/lib/layout.sh"

APP_NAME="${APP_NAME:-northstar-quant}"
SERVICE_USER="${SERVICE_USER:-northstar}"
SYSTEMD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME:-northstar-quant}"
SERVICE_HOME="${SERVICE_HOME:-/var/lib/northstar}"
APP_ROOT="${APP_ROOT:-/opt/northstar}"
CONFIG_DIR="${CONFIG_DIR:-/etc/northstar}"
STATE_DIR="${STATE_DIR:-/var/lib/northstar}"
CACHE_DIR="${CACHE_DIR:-/var/cache/northstar}"
LOG_DIR="${LOG_DIR:-/var/log/northstar}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
UV_VERSION="${UV_VERSION:-}"

deploy_assert_safe_name "APP_NAME" "${APP_NAME}"
deploy_assert_safe_name "SERVICE_USER" "${SERVICE_USER}"
deploy_assert_safe_name "SYSTEMD_SERVICE_NAME" "${SYSTEMD_SERVICE_NAME}"
deploy_need_cmd getent
deploy_need_cmd id
deploy_need_cmd stat
deploy_configure_linux_layout

deploy_require_linux_x86_64
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
  libcap2-bin \
  libpq-dev \
  postgresql-client \
  python3 \
  python3-venv \
  tar \
  tzdata
deploy_need_cmd getcap

verify_trusted_uv() {
  local current_version=""
  local owner=""
  local group=""
  local mode=""
  local capabilities=""
  local uv_binary="/usr/local/bin/uv"

  if [ -z "${UV_VERSION}" ]; then
    deploy_fail "缺少受控端传入的 UV_VERSION；拒绝使用未固定的构建工具。"
  fi
  if [ ! -f "${uv_binary}" ] || [ ! -x "${uv_binary}" ] || [ -L "${uv_binary}" ]; then
    deploy_fail "缺少受 root 管理的 /usr/local/bin/uv；请通过受审计的系统供应流程预装指定版本。"
  fi
  owner="$(stat -c '%U' "${uv_binary}")"
  group="$(stat -c '%G' "${uv_binary}")"
  mode="$(stat -c '%a' "${uv_binary}")"
  capabilities="$(getcap -- "${uv_binary}" 2>/dev/null || true)"
  if [ "${owner}" != root ] || [ "${group}" != root ] || [ "${mode}" != "755" ] ||
    [ -n "${capabilities}" ]; then
    deploy_fail "/usr/local/bin/uv 必须是无 capabilities 的 root:root 0755 普通文件。"
  fi
  current_version="$("${uv_binary}" --version | awk '{print $2}')"
  if [ "${current_version}" != "${UV_VERSION}" ]; then
    deploy_fail "受控 uv 版本与部署控制端不一致：需要 ${UV_VERSION}，实际为 ${current_version:-unknown}。"
  fi
}

deploy_log "验证受 root 管理的 uv ${UV_VERSION}"
verify_trusted_uv

deploy_log "创建固定的生产身份与目录边界"
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  deploy_as_root adduser \
    --system \
    --group \
    --no-create-home \
    --home "${SERVICE_HOME}" \
    --shell /usr/sbin/nologin \
    "${SERVICE_USER}"
fi

if ! deploy_assert_canonical_service_identity; then
  deploy_fail "既有服务账户不符合受管 northstar 身份、主组、home 或 nologin shell 约束。"
fi

# Code, release pointers, deployment metadata and secret configuration are
# controlled only by root. The service identity receives exact writable
# leaves below state/cache/log roots, never their administrative parents.
if ! deploy_prepare_fixed_privileged_layout "${SERVICE_USER}"; then
  deploy_fail "无法安全准备固定的受特权控制生产目录。"
fi

# The root-controlled parents are created only when absent and otherwise
# validated without repairing ownership/mode.  This keeps every later
# service-writable target a direct leaf instead of an arbitrary nested path.
for runtime_parent_dir in "${STATE_DIR}" "${CACHE_DIR}" "${LOG_DIR}"; do
  if ! deploy_prepare_runtime_parent_directory "${runtime_parent_dir}" "${SERVICE_USER}"; then
    deploy_fail "无法安全准备运行时 root 目录：${runtime_parent_dir}"
  fi
done

for runtime_dir in \
  "${RUNTIME_LOG_DIR}" \
  "${RUNTIME_REPORTS_DIR}" \
  "${RUNTIME_STORAGE_DIR}" \
  "${RUNTIME_DOWNLOADS_DIR}" \
  "${RUNTIME_CACHE_DIR}" \
  "${RUNTIME_MATPLOTLIB_DIR}" \
  "${UV_CACHE_DIR}"; do
  if ! deploy_prepare_runtime_leaf_directory "${runtime_dir}" "${SERVICE_USER}"; then
    deploy_fail "无法安全准备运行时可写叶子目录：${runtime_dir}"
  fi
done

assert_managed_root_tree_has_no_mounts() {
  local tree_path="$1"

  deploy_as_root env -i \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    /usr/bin/python3 -I "${SCRIPT_DIR}/mount_safety.py" "${tree_path}"
}

# The managed Python tree can exist on a repeated setup.  Reject a mount or
# same-device bind mount before root gives uv any write access, then check a
# second time before recursively sealing the result.  ``find -xdev`` alone
# does not distinguish a same-device bind mount.
if ! assert_managed_root_tree_has_no_mounts "${UV_BOOTSTRAP_CACHE_DIR}" ||
  ! assert_managed_root_tree_has_no_mounts "${UV_PYTHON_INSTALL_DIR}"; then
  deploy_fail "受 root 控制的 Python 目录包含挂载点，拒绝执行 uv 写入。"
fi
deploy_log "准备受 root 控制的 Python ${PYTHON_VERSION}"
deploy_as_root env \
  UV_CACHE_DIR="${UV_BOOTSTRAP_CACHE_DIR}" \
  UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR}" \
  /usr/local/bin/uv python install "${PYTHON_VERSION}"

# Limit metadata changes to ordinary files and directories so a symlink
# operand can never make chown/chmod follow an unexpected target.
if ! assert_managed_root_tree_has_no_mounts "${UV_PYTHON_INSTALL_DIR}"; then
  deploy_fail "受 root 控制的 Python 目录包含挂载点，拒绝递归修改所有权或权限。"
fi
if ! deploy_as_root find -P "${UV_PYTHON_INSTALL_DIR}" -xdev \
  \( -type f -o -type d \) -exec chown root:root -- {} +; then
  deploy_fail "无法安全固化受 root 控制的 Python 文件所有权。"
fi
if ! deploy_as_root find -P "${UV_PYTHON_INSTALL_DIR}" -xdev \
  \( -type f -o -type d \) -exec chmod go-w -- {} +; then
  deploy_fail "无法安全固化受 root 控制的 Python 文件权限。"
fi

deploy_log "Linux 运行时安装完成"
printf "uv=%s\n" "$(/usr/local/bin/uv --version)"
printf "service_user=%s\n" "${SERVICE_USER}"
printf "app_root=%s\n" "${APP_ROOT}"
printf "env_file=%s\n" "${ENV_FILE}"
