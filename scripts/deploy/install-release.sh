#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOYMENT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SYSTEMD_TEMPLATE_DIR="${DEPLOYMENT_ROOT}/infra/systemd"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/safety.sh"
source "${SCRIPT_DIR}/lib/runtime_paths.sh"

APP_NAME="${APP_NAME:-northstar-quant}"
SERVICE_USER="${SERVICE_USER:-northstar}"
SERVICE_HOME="${SERVICE_HOME:-/srv/${SERVICE_USER}}"
APP_ROOT="${APP_ROOT:-${SERVICE_HOME}/${APP_NAME}}"
SYSTEMD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME:-${APP_NAME}}"
SERVICE_MODE="${SERVICE_MODE:-health}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
CONFIRM_LIVE_DEPLOY="${CONFIRM_LIVE_DEPLOY:-NO}"
ARTIFACT_TARBALL="${ARTIFACT_TARBALL:-${1:-}}"
ARTIFACT_SHA256="${ARTIFACT_SHA256:-}"
RELEASE_ID="${RELEASE_ID:-}"
DASHBOARD_DEPLOY_ENABLED="${DASHBOARD_DEPLOY_ENABLED:-0}"

if [ "$(uname -s)" != "Linux" ]; then
  deploy_fail "版本安装脚本只支持 Linux。"
fi
for required_command in cp cut find mktemp readlink realpath sha256sum sort systemctl tar; do
  deploy_need_cmd "${required_command}"
done

SERVICE_HOME="$(realpath -m -- "${SERVICE_HOME}")"
APP_ROOT="$(realpath -m -- "${APP_ROOT}")"

RELEASES_DIR="${APP_ROOT}/releases"
SHARED_DIR="${APP_ROOT}/shared"
CURRENT_LINK="${APP_ROOT}/current"
ENV_FILE="${SHARED_DIR}/.env"
DASHBOARD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME}-dashboard"
DASHBOARD_UNIT_FILE="/etc/systemd/system/${DASHBOARD_SERVICE_NAME}.service"
DASHBOARD_HOME_DIR=""
DASHBOARD_DEPLOY_STATUS="disabled"
STAGE_DIR=""
PREVIOUS_RELEASE=""
SYSTEMD_UNIT_FILE="/etc/systemd/system/${SYSTEMD_SERVICE_NAME}.service"
PREVIOUS_SYSTEMD_UNIT_FILE=""
PREVIOUS_SYSTEMD_UNIT_EXISTS=false
SYSTEMD_UNIT_BACKUP_CREATED=false
RENDERED_SYSTEMD_UNIT_FILE=""
RENDERED_DASHBOARD_SYSTEMD_UNIT_FILE=""
CUTOVER_ACTIVE=false

deploy_assert_safe_name "APP_NAME" "${APP_NAME}"
deploy_assert_safe_name "SERVICE_USER" "${SERVICE_USER}"
deploy_assert_safe_name "SYSTEMD_SERVICE_NAME" "${SYSTEMD_SERVICE_NAME}"
deploy_assert_safe_name "RELEASE_ID" "${RELEASE_ID}"
deploy_assert_safe_name "DASHBOARD_SERVICE_NAME" "${DASHBOARD_SERVICE_NAME}"
deploy_assert_bool "DASHBOARD_DEPLOY_ENABLED" "${DASHBOARD_DEPLOY_ENABLED}"

case "${SERVICE_HOME}" in
  /srv/*)
    ;;
  *)
    deploy_fail "SERVICE_HOME 必须位于 /srv 下。"
    ;;
esac

case "${SERVICE_HOME}${APP_ROOT}" in
  *[!A-Za-z0-9/._-]*)
    deploy_fail "部署路径只能包含字母、数字、点、下划线、连字符和斜杠。"
    ;;
esac

case "${APP_ROOT}" in
  "${SERVICE_HOME}"/*)
    ;;
  *)
    deploy_fail "APP_ROOT 必须位于 SERVICE_HOME 下。"
    ;;
esac

deploy_configure_runtime_paths "${APP_ROOT}"
DASHBOARD_HOME_DIR="${RUNTIME_CACHE_DIR}/dashboard"

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
  deploy_fail "KEEP_RELEASES 至少为 2，以保留一个可回退版本。"
fi

if [ ! -f "${ARTIFACT_TARBALL}" ]; then
  deploy_fail "未找到部署制品：${ARTIFACT_TARBALL}"
fi
if [ -z "${ARTIFACT_SHA256}" ]; then
  deploy_fail "缺少部署制品 SHA-256。"
fi
actual_artifact_sha256="$(sha256sum "${ARTIFACT_TARBALL}" | awk '{print $1}')"
if [ "${actual_artifact_sha256}" != "${ARTIFACT_SHA256}" ]; then
  deploy_fail "部署制品 SHA-256 校验失败。"
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  deploy_fail "服务用户不存在：${SERVICE_USER}。请先运行 install-runtime.sh。"
fi
if [ ! -f "${ENV_FILE}" ]; then
  deploy_fail "服务器生产环境文件不存在：${ENV_FILE}"
fi

validate_artifact() {
  local invalid_entry

  invalid_entry="$(
    tar -tzf "${ARTIFACT_TARBALL}" |
      awk '/^\// || /(^|\/)\.\.(\/|$)/ { print; exit }'
  )"
  if [ -n "${invalid_entry}" ]; then
    deploy_fail "部署制品包含不安全路径：${invalid_entry}"
  fi
}

cleanup_stage() {
  if [ -n "${STAGE_DIR}" ]; then
    deploy_as_root rm -rf "${STAGE_DIR}"
  fi
}

cleanup_systemd_unit_backup() {
  if [ -n "${PREVIOUS_SYSTEMD_UNIT_FILE}" ]; then
    deploy_as_root rm -f "${PREVIOUS_SYSTEMD_UNIT_FILE}" || true
    PREVIOUS_SYSTEMD_UNIT_FILE=""
  fi
}

cleanup_rendered_systemd_unit() {
  if [ -n "${RENDERED_SYSTEMD_UNIT_FILE}" ]; then
    rm -f "${RENDERED_SYSTEMD_UNIT_FILE}" || true
    RENDERED_SYSTEMD_UNIT_FILE=""
  fi
}

cleanup_rendered_dashboard_systemd_unit() {
  if [ -n "${RENDERED_DASHBOARD_SYSTEMD_UNIT_FILE}" ]; then
    rm -f "${RENDERED_DASHBOARD_SYSTEMD_UNIT_FILE}" || true
    RENDERED_DASHBOARD_SYSTEMD_UNIT_FILE=""
  fi
}

cleanup_deployment_temporary_files() {
  cleanup_stage
  cleanup_systemd_unit_backup
  cleanup_rendered_systemd_unit
  cleanup_rendered_dashboard_systemd_unit
}
trap cleanup_deployment_temporary_files EXIT

run_release_command() {
  local release_dir="$1"
  shift

  deploy_as_user "${SERVICE_USER}" env \
    HOME="${SERVICE_HOME}" \
    UV_CACHE_DIR="${SHARED_DIR}/uv-cache" \
    UV_PYTHON_INSTALL_DIR="${SHARED_DIR}/python" \
    XDG_CACHE_HOME="${RUNTIME_CACHE_DIR}" \
    MPLCONFIGDIR="${RUNTIME_MATPLOTLIB_DIR}" \
    NORTHSTAR_PROJECT_ROOT="${release_dir}" \
    /bin/bash -c 'cd "$1"; shift; exec "$@"' bash "${release_dir}" "$@"
}

render_systemd_unit() {
  local template_file="${SYSTEMD_TEMPLATE_DIR}/${SERVICE_MODE}.service.in"

  if [ ! -f "${template_file}" ]; then
    printf "systemd 模板不存在：%s\n" "${template_file}" >&2
    return 1
  fi

  RENDERED_SYSTEMD_UNIT_FILE="$(mktemp)" || return 1
  if ! sed \
    -e "s|@SERVICE_USER@|${SERVICE_USER}|g" \
    -e "s|@CURRENT_LINK@|${CURRENT_LINK}|g" \
    -e "s|@ENV_FILE@|${ENV_FILE}|g" \
    -e "s|@SERVICE_HOME@|${SERVICE_HOME}|g" \
    -e "s|@SHARED_DIR@|${SHARED_DIR}|g" \
    -e "s|@RUNTIME_STORAGE_DIR@|${RUNTIME_STORAGE_DIR}|g" \
    -e "s|@RUNTIME_DOWNLOADS_DIR@|${RUNTIME_DOWNLOADS_DIR}|g" \
    -e "s|@RUNTIME_REPORTS_DIR@|${RUNTIME_REPORTS_DIR}|g" \
    -e "s|@RUNTIME_LOG_DIR@|${RUNTIME_LOG_DIR}|g" \
    -e "s|@RUNTIME_CACHE_DIR@|${RUNTIME_CACHE_DIR}|g" \
    -e "s|@RUNTIME_MATPLOTLIB_DIR@|${RUNTIME_MATPLOTLIB_DIR}|g" \
    "${template_file}" > "${RENDERED_SYSTEMD_UNIT_FILE}"; then
    cleanup_rendered_systemd_unit
    return 1
  fi
}

render_dashboard_systemd_unit() {
  local template_file="${SYSTEMD_TEMPLATE_DIR}/dashboard.service.in"

  if [ ! -f "${template_file}" ]; then
    printf "Dashboard systemd 模板不存在：%s\n" "${template_file}" >&2
    return 1
  fi

  RENDERED_DASHBOARD_SYSTEMD_UNIT_FILE="$(mktemp)" || return 1
  if ! sed \
    -e "s|@SERVICE_USER@|${SERVICE_USER}|g" \
    -e "s|@CURRENT_LINK@|${CURRENT_LINK}|g" \
    -e "s|@ENV_FILE@|${ENV_FILE}|g" \
    -e "s|@SERVICE_HOME@|${SERVICE_HOME}|g" \
    -e "s|@SHARED_DIR@|${SHARED_DIR}|g" \
    -e "s|@DASHBOARD_HOME_DIR@|${DASHBOARD_HOME_DIR}|g" \
    -e "s|@RUNTIME_LOG_DIR@|${RUNTIME_LOG_DIR}|g" \
    -e "s|@RUNTIME_CACHE_DIR@|${RUNTIME_CACHE_DIR}|g" \
    -e "s|@RUNTIME_MATPLOTLIB_DIR@|${RUNTIME_MATPLOTLIB_DIR}|g" \
    "${template_file}" > "${RENDERED_DASHBOARD_SYSTEMD_UNIT_FILE}"; then
    cleanup_rendered_dashboard_systemd_unit
    return 1
  fi
}

install_dashboard_systemd_unit() {
  if [ -z "${RENDERED_DASHBOARD_SYSTEMD_UNIT_FILE}" ]; then
    printf "尚未渲染 Dashboard systemd 服务配置。\n" >&2
    return 1
  fi

  if ! deploy_as_root install -m 0644 -o root -g root \
    "${RENDERED_DASHBOARD_SYSTEMD_UNIT_FILE}" "${DASHBOARD_UNIT_FILE}"; then
    return 1
  fi
  deploy_as_root systemctl daemon-reload || return 1
  deploy_as_root systemctl enable "${DASHBOARD_SERVICE_NAME}.service" >/dev/null || return 1
  deploy_as_root systemctl restart "${DASHBOARD_SERVICE_NAME}.service" || return 1
  deploy_as_root systemctl is-active --quiet "${DASHBOARD_SERVICE_NAME}.service"
}

disable_dashboard_systemd_unit() {
  # 即使 unit 文件已被人工删除，已加载的旧服务也可能仍在运行；默认关闭必须先尝试停用它。
  deploy_as_root systemctl disable --now "${DASHBOARD_SERVICE_NAME}.service" >/dev/null 2>&1 || true
  if deploy_as_root systemctl is-active --quiet "${DASHBOARD_SERVICE_NAME}.service"; then
    printf "无法停止私网 Dashboard 服务：%s.service\n" "${DASHBOARD_SERVICE_NAME}" >&2
    return 1
  fi
  if deploy_as_root test -e "${DASHBOARD_UNIT_FILE}" ||
    deploy_as_root test -L "${DASHBOARD_UNIT_FILE}"; then
    deploy_as_root rm -f "${DASHBOARD_UNIT_FILE}" || return 1
  fi
  deploy_as_root systemctl daemon-reload
}

configure_dashboard_systemd_unit() {
  if [ "${DASHBOARD_DEPLOY_ENABLED}" = "0" ]; then
    disable_dashboard_systemd_unit
    DASHBOARD_DEPLOY_STATUS="disabled"
    return
  fi

  deploy_log "渲染并启动私网 Dashboard 服务"
  render_dashboard_systemd_unit || return 1
  install_dashboard_systemd_unit || return 1
  DASHBOARD_DEPLOY_STATUS="enabled"
}

fail_closed_dashboard_systemd_unit() {
  deploy_as_root systemctl disable --now "${DASHBOARD_SERVICE_NAME}.service" >/dev/null 2>&1 || true
  deploy_as_root rm -f "${DASHBOARD_UNIT_FILE}" >/dev/null 2>&1 || true
  deploy_as_root systemctl daemon-reload >/dev/null 2>&1 || true
  if deploy_as_root systemctl is-active --quiet "${DASHBOARD_SERVICE_NAME}.service"; then
    return 1
  fi
  if deploy_as_root test -e "${DASHBOARD_UNIT_FILE}" ||
    deploy_as_root test -L "${DASHBOARD_UNIT_FILE}"; then
    return 1
  fi
  DASHBOARD_DEPLOY_STATUS="disabled_after_failure"
}

install_rendered_systemd_unit() {
  if [ -z "${RENDERED_SYSTEMD_UNIT_FILE}" ]; then
    printf "尚未渲染 systemd 服务配置。\n" >&2
    return 1
  fi

  if ! deploy_as_root install -m 0644 -o root -g root \
    "${RENDERED_SYSTEMD_UNIT_FILE}" "${SYSTEMD_UNIT_FILE}"; then
    return 1
  fi
  deploy_as_root systemctl daemon-reload || return 1
  deploy_as_root systemctl enable "${SYSTEMD_SERVICE_NAME}.service" >/dev/null || return 1
}

backup_systemd_unit() {
  if [ "${SYSTEMD_UNIT_BACKUP_CREATED}" = true ]; then
    return 0
  fi

  if deploy_as_root test -f "${SYSTEMD_UNIT_FILE}"; then
    PREVIOUS_SYSTEMD_UNIT_FILE="$(
      deploy_as_root mktemp "${SHARED_DIR}/incoming/.${SYSTEMD_SERVICE_NAME}.service.XXXXXX"
    )" || return 1
    if ! deploy_as_root cp "${SYSTEMD_UNIT_FILE}" "${PREVIOUS_SYSTEMD_UNIT_FILE}"; then
      cleanup_systemd_unit_backup
      return 1
    fi
    PREVIOUS_SYSTEMD_UNIT_EXISTS=true
  else
    PREVIOUS_SYSTEMD_UNIT_EXISTS=false
  fi
  SYSTEMD_UNIT_BACKUP_CREATED=true
}

restore_systemd_unit() {
  if [ "${SYSTEMD_UNIT_BACKUP_CREATED}" != true ]; then
    return 0
  fi

  if [ "${PREVIOUS_SYSTEMD_UNIT_EXISTS}" = true ]; then
    if ! deploy_as_root install -m 0644 -o root -g root \
      "${PREVIOUS_SYSTEMD_UNIT_FILE}" "${SYSTEMD_UNIT_FILE}"; then
      return 1
    fi
  else
    deploy_as_root rm -f "${SYSTEMD_UNIT_FILE}" || return 1
    deploy_as_root systemctl disable "${SYSTEMD_SERVICE_NAME}.service" >/dev/null 2>&1 || true
  fi
  deploy_as_root systemctl daemon-reload
}

switch_current_release() {
  local new_current_link="${APP_ROOT}/.current.${RELEASE_ID}"

  deploy_as_root rm -f "${new_current_link}" || return 1
  deploy_as_root ln -s "${RELEASE_DIR}" "${new_current_link}" || return 1
  if ! deploy_as_root mv -Tf "${new_current_link}" "${CURRENT_LINK}"; then
    deploy_as_root rm -f "${new_current_link}" || true
    return 1
  fi
}

activate_service() {
  deploy_as_root systemctl start "${SYSTEMD_SERVICE_NAME}.service" || return 1
  if [ "${SERVICE_MODE}" = "scheduler" ]; then
    sleep 3
  fi
  deploy_as_root systemctl is-active --quiet "${SYSTEMD_SERVICE_NAME}.service"
}

rollback_release() {
  deploy_log "服务切换失败，回退到上一版本"
  deploy_as_root systemctl stop "${SYSTEMD_SERVICE_NAME}.service" >/dev/null 2>&1 || true
  if ! restore_systemd_unit; then
    printf "无法恢复上一版本的 systemd 服务配置。\n" >&2
    return 1
  fi

  if [ -n "${PREVIOUS_RELEASE}" ] && deploy_as_root test -d "${PREVIOUS_RELEASE}"; then
    local rollback_link="${APP_ROOT}/.current.rollback"
    if ! deploy_as_root rm -f "${rollback_link}" ||
      ! deploy_as_root ln -s "${PREVIOUS_RELEASE}" "${rollback_link}" ||
      ! deploy_as_root mv -Tf "${rollback_link}" "${CURRENT_LINK}"; then
      deploy_as_root rm -f "${rollback_link}" || true
      printf "无法切换回上一版本的 current 链接。\n" >&2
      return 1
    fi
    if deploy_as_root systemctl restart "${SYSTEMD_SERVICE_NAME}.service" &&
      deploy_as_root systemctl is-active --quiet "${SYSTEMD_SERVICE_NAME}.service"; then
      deploy_log "上一版本已恢复：${PREVIOUS_RELEASE}"
      return 0
    fi
    printf "上一版本已恢复，但服务仍未通过检查。\n" >&2
    return 1
  fi

  deploy_as_root rm -f "${CURRENT_LINK}" || return 1
  printf "没有可回退的上一版本。\n" >&2
  return 1
}

recover_interrupted_cutover() {
  local status="$1"

  trap - ERR INT TERM
  if [ "${CUTOVER_ACTIVE}" = true ]; then
    printf "部署切换被中断，尝试恢复上一版本服务。\n" >&2
    rollback_release || true
  fi
  exit "${status}"
}

trap 'recover_interrupted_cutover $?' ERR
trap 'recover_interrupted_cutover 130' INT
trap 'recover_interrupted_cutover 143' TERM

prune_old_releases() {
  local count=0
  local release_dir

  while IFS= read -r release_dir; do
    count=$((count + 1))
    if [ "${count}" -le "${KEEP_RELEASES}" ]; then
      continue
    fi
    if [ "${release_dir}" = "$(readlink -f "${CURRENT_LINK}")" ]; then
      continue
    fi
    deploy_as_root rm -rf "${release_dir}"
  done < <(
    find "${RELEASES_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name '.*' \
      -printf '%T@ %p\n' |
      sort -nr |
      cut -d' ' -f2-
  )
}

deploy_validate_production_env "${ENV_FILE}" "${SERVICE_MODE}" "${CONFIRM_LIVE_DEPLOY}"
validate_artifact

deploy_as_root install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 \
  "${SHARED_DIR}" \
  "${SHARED_DIR}/incoming" \
  "${RUNTIME_CACHE_DIR}" \
  "${RUNTIME_LOG_DIR}" \
  "${RUNTIME_MATPLOTLIB_DIR}" \
  "${RUNTIME_REPORTS_DIR}" \
  "${RUNTIME_STORAGE_DIR}" \
  "${RUNTIME_DOWNLOADS_DIR}" \
  "${SHARED_DIR}/python" \
  "${SHARED_DIR}/uv-cache"

if [ "${DASHBOARD_DEPLOY_ENABLED}" = "1" ]; then
  deploy_as_root install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 \
    "${DASHBOARD_HOME_DIR}"
fi

deploy_as_root install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 \
  "${RELEASES_DIR}"

RELEASE_DIR="${RELEASES_DIR}/${RELEASE_ID}"
if deploy_as_root test -e "${RELEASE_DIR}"; then
  deploy_fail "版本目录已经存在：${RELEASE_DIR}"
fi

deploy_log "解压版本 ${RELEASE_ID}"
STAGE_DIR="$(deploy_as_root mktemp -d "${RELEASES_DIR}/.${RELEASE_ID}.stage.XXXXXX")"
deploy_as_root tar -xzf "${ARTIFACT_TARBALL}" -C "${STAGE_DIR}"

for required_path in pyproject.toml uv.lock alembic.ini src configs configs/app.example.yaml; do
  if ! deploy_as_root test -e "${STAGE_DIR}/${required_path}"; then
    deploy_fail "部署制品缺少：${required_path}"
  fi
done
for forbidden_path in .env .venv logs storage reports configs/app.yaml configs/app.local.yaml; do
  if deploy_as_root test -e "${STAGE_DIR}/${forbidden_path}"; then
    deploy_fail "部署制品不应包含运行时路径：${forbidden_path}"
  fi
done

deploy_as_root ln -s "${ENV_FILE}" "${STAGE_DIR}/.env"
deploy_log "从完整模板生成新版本活动应用配置"
deploy_write_active_app_config \
  "${STAGE_DIR}/configs/app.example.yaml" \
  "${STAGE_DIR}/configs/app.yaml" \
  "${SERVICE_USER}"
deploy_as_root ln -s "${RUNTIME_LOG_DIR}" "${STAGE_DIR}/logs"
deploy_as_root ln -s "${RUNTIME_STORAGE_DIR}" "${STAGE_DIR}/storage"
deploy_as_root ln -s "${RUNTIME_REPORTS_DIR}" "${STAGE_DIR}/reports"
deploy_as_root chown -R "${SERVICE_USER}:${SERVICE_USER}" "${STAGE_DIR}"

deploy_log "按 uv.lock 安装生产依赖"
deploy_as_user "${SERVICE_USER}" env \
  HOME="${SERVICE_HOME}" \
  UV_CACHE_DIR="${SHARED_DIR}/uv-cache" \
  UV_PYTHON_INSTALL_DIR="${SHARED_DIR}/python" \
  XDG_CACHE_HOME="${RUNTIME_CACHE_DIR}" \
  MPLCONFIGDIR="${RUNTIME_MATPLOTLIB_DIR}" \
  /usr/local/bin/uv sync \
  --directory "${STAGE_DIR}" \
  --frozen \
  --no-dev \
  --no-editable \
  --python "${PYTHON_VERSION}"

deploy_log "执行数据库迁移"
run_release_command "${STAGE_DIR}" "${STAGE_DIR}/.venv/bin/northstar" init-db

deploy_log "执行发布前健康检查"
run_release_command "${STAGE_DIR}" "${STAGE_DIR}/.venv/bin/northstar" health --fail-on-blocked

deploy_as_root mv "${STAGE_DIR}" "${RELEASE_DIR}"
STAGE_DIR=""
PREVIOUS_RELEASE="$(readlink -f "${CURRENT_LINK}" 2>/dev/null || true)"

deploy_log "渲染新版本 systemd 服务配置"
if ! render_systemd_unit || ! backup_systemd_unit; then
  deploy_fail "无法准备 systemd 服务配置；保留当前运行版本。"
fi

deploy_log "停止当前服务，准备原子切换 current"
if ! deploy_as_root systemctl stop "${SYSTEMD_SERVICE_NAME}.service"; then
  deploy_fail "无法停止当前服务；保留当前运行版本。"
fi
CUTOVER_ACTIVE=true

deploy_log "安装新版本 systemd 服务配置"
if ! install_rendered_systemd_unit; then
  rollback_release || true
  CUTOVER_ACTIVE=false
  deploy_fail "无法安装新版本 systemd 服务配置；已尝试恢复上一版本服务。"
fi

deploy_log "原子切换 current"
if ! switch_current_release; then
  rollback_release || true
  CUTOVER_ACTIVE=false
  deploy_fail "无法原子切换 current；已尝试恢复上一版本服务。"
fi

deploy_log "启动新版本服务"
if ! activate_service; then
  deploy_as_root journalctl -u "${SYSTEMD_SERVICE_NAME}" -n 80 --no-pager >&2 || true
  rollback_release || true
  CUTOVER_ACTIVE=false
  deploy_fail "新版本服务启动失败。数据库迁移不会自动回滚，请检查兼容性。"
fi

CUTOVER_ACTIVE=false
if ! configure_dashboard_systemd_unit; then
  if ! fail_closed_dashboard_systemd_unit; then
    deploy_as_root journalctl -u "${DASHBOARD_SERVICE_NAME}" -n 80 --no-pager >&2 || true
    deploy_fail "主服务已切换成功，但无法确认私网 Dashboard 已关闭；请立即人工检查 ${DASHBOARD_SERVICE_NAME}.service。"
  fi
  deploy_log "私网 Dashboard 配置或启动失败，已关闭该观察服务；主 health/scheduler 服务保持本次发布版本。"
fi

prune_old_releases
cleanup_systemd_unit_backup
cleanup_rendered_systemd_unit
cleanup_rendered_dashboard_systemd_unit
trap - ERR INT TERM
trap - EXIT

deploy_log "版本部署完成"
printf "release=%s\n" "${RELEASE_DIR}"
printf "current=%s\n" "$(readlink -f "${CURRENT_LINK}")"
printf "service=%s.service\n" "${SYSTEMD_SERVICE_NAME}"
printf "mode=%s\n" "${SERVICE_MODE}"
printf "dashboard=%s\n" "${DASHBOARD_DEPLOY_STATUS}"
