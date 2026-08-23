#!/usr/bin/env bash

# Canonical Linux production layout.  Application code and systemd control
# files are root-owned; the service identity receives only the state, cache
# and log directories it must mutate at runtime.
deploy_configure_linux_layout() {
  local expected_app_root="/opt/northstar"
  local expected_config_dir="/etc/northstar"
  local expected_state_dir="/var/lib/northstar"
  local expected_cache_dir="/var/cache/northstar"
  local expected_log_dir="/var/log/northstar"

  if [ "${APP_NAME}" != "northstar-quant" ] ||
    [ "${SERVICE_USER}" != "northstar" ] ||
    [ "${SYSTEMD_SERVICE_NAME}" != "northstar-quant" ]; then
    deploy_fail "Linux 生产身份固定为 northstar-quant / northstar；拒绝覆盖其他系统服务或账户。"
  fi

  deploy_need_cmd realpath
  APP_ROOT="$(realpath -m -- "${APP_ROOT:-${expected_app_root}}")"
  CONFIG_DIR="$(realpath -m -- "${CONFIG_DIR:-${expected_config_dir}}")"
  STATE_DIR="$(realpath -m -- "${STATE_DIR:-${expected_state_dir}}")"
  CACHE_DIR="$(realpath -m -- "${CACHE_DIR:-${expected_cache_dir}}")"
  LOG_DIR="$(realpath -m -- "${LOG_DIR:-${expected_log_dir}}")"
  SERVICE_HOME="$(realpath -m -- "${SERVICE_HOME:-${expected_state_dir}}")"

  if [ "${APP_ROOT}" != "${expected_app_root}" ] ||
    [ "${CONFIG_DIR}" != "${expected_config_dir}" ] ||
    [ "${STATE_DIR}" != "${expected_state_dir}" ] ||
    [ "${CACHE_DIR}" != "${expected_cache_dir}" ] ||
    [ "${LOG_DIR}" != "${expected_log_dir}" ] ||
    [ "${SERVICE_HOME}" != "${expected_state_dir}" ]; then
    deploy_fail "Linux 生产布局固定为 /opt/northstar、/etc/northstar、/var/lib/northstar、/var/cache/northstar 与 /var/log/northstar。"
  fi

  RELEASES_DIR="${APP_ROOT}/releases"
  CURRENT_LINK="${APP_ROOT}/current"
  ENV_FILE="${CONFIG_DIR}/${APP_NAME}.env"
  ENV_RELEASES_DIR="${CONFIG_DIR}/releases"
  SYSTEMD_UNIT_DIR="/etc/systemd/system"
  UV_PYTHON_INSTALL_DIR="${STATE_DIR}/python"
  UV_CACHE_DIR="${CACHE_DIR}/uv-cache"
  DEPLOY_STATE_DIR="${STATE_DIR}/deploy-state"
  UV_BOOTSTRAP_CACHE_DIR="${DEPLOY_STATE_DIR}/uv-bootstrap-cache"

  deploy_configure_runtime_paths "${APP_ROOT}" "${STATE_DIR}" "${CACHE_DIR}" "${LOG_DIR}"

  for runtime_path_name in \
    RUNTIME_STORAGE_DIR \
    RUNTIME_DOWNLOADS_DIR \
    RUNTIME_REPORTS_DIR \
    RUNTIME_LOG_DIR \
    RUNTIME_CACHE_DIR \
    RUNTIME_MATPLOTLIB_DIR; do
    runtime_path="${!runtime_path_name}"
    case "${runtime_path}" in
      "${APP_ROOT}"|"${APP_ROOT}/"*|"${CONFIG_DIR}"|"${CONFIG_DIR}/"*|\
      "${STATE_DIR}"|"${DEPLOY_STATE_DIR}"|"${DEPLOY_STATE_DIR}/"*|\
      "${CACHE_DIR}"|"${LOG_DIR}"|"${UV_PYTHON_INSTALL_DIR}"|"${UV_PYTHON_INSTALL_DIR}/"*|\
      "${UV_CACHE_DIR}")
        deploy_fail "${runtime_path_name} 必须是独立的运行时叶子目录，不能覆盖代码、配置、部署状态或运行时根目录。"
        ;;
    esac
  done

}

# Prepare every fixed privileged layout root before an installer writes below
# it.  Runtime leaves remain separately owned by the service identity; this
# function covers only root-controlled roots and their root-only children.
deploy_prepare_fixed_privileged_layout() {
  local service_group="$1"

  deploy_prepare_root_controlled_directory "${APP_ROOT}" root 755 || return 1
  deploy_prepare_root_controlled_directory "${RELEASES_DIR}" root 755 || return 1
  deploy_prepare_root_controlled_directory "${CONFIG_DIR}" "${service_group}" 750 || return 1
  deploy_prepare_root_controlled_directory "${ENV_RELEASES_DIR}" "${service_group}" 750 || return 1
  deploy_prepare_root_controlled_directory "${SYSTEMD_UNIT_DIR}" root 755 || return 1

  deploy_prepare_root_controlled_directory "${STATE_DIR}" "${service_group}" 750 || return 1
  deploy_prepare_root_controlled_directory "${CACHE_DIR}" "${service_group}" 750 || return 1
  deploy_prepare_root_controlled_directory "${LOG_DIR}" "${service_group}" 750 || return 1
  deploy_prepare_root_controlled_directory "${DEPLOY_STATE_DIR}" root 700 || return 1
  deploy_prepare_root_controlled_directory "${UV_BOOTSTRAP_CACHE_DIR}" root 700 || return 1
  deploy_prepare_root_controlled_directory "${UV_PYTHON_INSTALL_DIR}" root 755
}
