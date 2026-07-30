#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/safety.sh"

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

if [ "$(uname -s)" != "Linux" ]; then
  deploy_fail "版本安装脚本只支持 Linux。"
fi
for required_command in cut find readlink realpath sha256sum sort systemctl tar; do
  deploy_need_cmd "${required_command}"
done

SERVICE_HOME="$(realpath -m -- "${SERVICE_HOME}")"
APP_ROOT="$(realpath -m -- "${APP_ROOT}")"

RELEASES_DIR="${APP_ROOT}/releases"
SHARED_DIR="${APP_ROOT}/shared"
CURRENT_LINK="${APP_ROOT}/current"
ENV_FILE="${SHARED_DIR}/.env"
STAGE_DIR=""
PREVIOUS_RELEASE=""

deploy_assert_safe_name "APP_NAME" "${APP_NAME}"
deploy_assert_safe_name "SERVICE_USER" "${SERVICE_USER}"
deploy_assert_safe_name "SYSTEMD_SERVICE_NAME" "${SYSTEMD_SERVICE_NAME}"
deploy_assert_safe_name "RELEASE_ID" "${RELEASE_ID}"

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
trap cleanup_stage EXIT

run_release_command() {
  local release_dir="$1"
  shift

  deploy_as_user "${SERVICE_USER}" env \
    HOME="${SERVICE_HOME}" \
    UV_CACHE_DIR="${SHARED_DIR}/uv-cache" \
    UV_PYTHON_INSTALL_DIR="${SHARED_DIR}/python" \
    NORTHSTAR_PROJECT_ROOT="${release_dir}" \
    NORTHSTAR_STORAGE_DIR="${SHARED_DIR}/storage" \
    NORTHSTAR_DOWNLOADS_DIR="${SHARED_DIR}/storage/downloads" \
    NORTHSTAR_REPORTS_DIR="${SHARED_DIR}/reports" \
    /bin/bash -c 'cd "$1"; shift; exec "$@"' bash "${release_dir}" "$@"
}

write_systemd_unit() {
  local unit_file="/etc/systemd/system/${SYSTEMD_SERVICE_NAME}.service"
  local template_file="${SCRIPT_DIR}/systemd/${SERVICE_MODE}.service.in"
  local temp_unit

  if [ ! -f "${template_file}" ]; then
    printf "systemd 模板不存在：%s\n" "${template_file}" >&2
    return 1
  fi

  temp_unit="$(mktemp)" || return 1
  if ! sed \
    -e "s|@SERVICE_USER@|${SERVICE_USER}|g" \
    -e "s|@CURRENT_LINK@|${CURRENT_LINK}|g" \
    -e "s|@ENV_FILE@|${ENV_FILE}|g" \
    -e "s|@SERVICE_HOME@|${SERVICE_HOME}|g" \
    -e "s|@SHARED_DIR@|${SHARED_DIR}|g" \
    "${template_file}" > "${temp_unit}"; then
    rm -f "${temp_unit}"
    return 1
  fi

  if ! deploy_as_root install -m 0644 -o root -g root "${temp_unit}" "${unit_file}"; then
    rm -f "${temp_unit}"
    return 1
  fi
  rm -f "${temp_unit}"
  deploy_as_root systemctl daemon-reload || return 1
  deploy_as_root systemctl enable "${SYSTEMD_SERVICE_NAME}.service" >/dev/null || return 1
}

activate_service() {
  deploy_as_root systemctl restart "${SYSTEMD_SERVICE_NAME}.service" || return 1
  if [ "${SERVICE_MODE}" = "scheduler" ]; then
    sleep 3
  fi
  deploy_as_root systemctl is-active --quiet "${SYSTEMD_SERVICE_NAME}.service"
}

rollback_release() {
  deploy_log "服务启动失败，回退到上一版本"
  deploy_as_root systemctl stop "${SYSTEMD_SERVICE_NAME}.service" >/dev/null 2>&1 || true

  if [ -n "${PREVIOUS_RELEASE}" ] && deploy_as_root test -d "${PREVIOUS_RELEASE}"; then
    local rollback_link="${APP_ROOT}/.current.rollback"
    deploy_as_root rm -f "${rollback_link}"
    deploy_as_root ln -s "${PREVIOUS_RELEASE}" "${rollback_link}"
    deploy_as_root mv -Tf "${rollback_link}" "${CURRENT_LINK}"
    if deploy_as_root systemctl restart "${SYSTEMD_SERVICE_NAME}.service" &&
      deploy_as_root systemctl is-active --quiet "${SYSTEMD_SERVICE_NAME}.service"; then
      deploy_log "上一版本已恢复：${PREVIOUS_RELEASE}"
      return 0
    fi
    printf "上一版本已恢复，但服务仍未通过检查。\n" >&2
    return 1
  fi

  deploy_as_root rm -f "${CURRENT_LINK}"
  printf "没有可回退的上一版本。\n" >&2
  return 1
}

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
  "${RELEASES_DIR}" \
  "${SHARED_DIR}/cache" \
  "${SHARED_DIR}/logs" \
  "${SHARED_DIR}/matplotlib" \
  "${SHARED_DIR}/reports" \
  "${SHARED_DIR}/storage" \
  "${SHARED_DIR}/uv-cache"

RELEASE_DIR="${RELEASES_DIR}/${RELEASE_ID}"
if deploy_as_root test -e "${RELEASE_DIR}"; then
  deploy_fail "版本目录已经存在：${RELEASE_DIR}"
fi

deploy_log "解压版本 ${RELEASE_ID}"
STAGE_DIR="$(deploy_as_root mktemp -d "${RELEASES_DIR}/.${RELEASE_ID}.stage.XXXXXX")"
deploy_as_root tar -xzf "${ARTIFACT_TARBALL}" -C "${STAGE_DIR}"

for required_path in pyproject.toml uv.lock alembic.ini src configs; do
  if ! deploy_as_root test -e "${STAGE_DIR}/${required_path}"; then
    deploy_fail "部署制品缺少：${required_path}"
  fi
done
for forbidden_path in .env .venv logs storage reports; do
  if deploy_as_root test -e "${STAGE_DIR}/${forbidden_path}"; then
    deploy_fail "部署制品不应包含运行时路径：${forbidden_path}"
  fi
done

deploy_as_root ln -s "${ENV_FILE}" "${STAGE_DIR}/.env"
deploy_as_root ln -s "${SHARED_DIR}/logs" "${STAGE_DIR}/logs"
deploy_as_root ln -s "${SHARED_DIR}/storage" "${STAGE_DIR}/storage"
deploy_as_root ln -s "${SHARED_DIR}/reports" "${STAGE_DIR}/reports"
deploy_as_root chown -R "${SERVICE_USER}:${SERVICE_USER}" "${STAGE_DIR}"

deploy_log "按 uv.lock 安装生产依赖"
deploy_as_user "${SERVICE_USER}" env \
  HOME="${SERVICE_HOME}" \
  UV_CACHE_DIR="${SHARED_DIR}/uv-cache" \
  UV_PYTHON_INSTALL_DIR="${SHARED_DIR}/python" \
  /usr/local/bin/uv sync \
  --directory "${STAGE_DIR}" \
  --frozen \
  --no-dev \
  --no-editable \
  --python "${PYTHON_VERSION}"

deploy_log "执行数据库迁移"
run_release_command "${STAGE_DIR}" "${STAGE_DIR}/.venv/bin/northstar" init-db

deploy_log "执行发布前健康检查"
run_release_command "${STAGE_DIR}" "${STAGE_DIR}/.venv/bin/northstar" health

deploy_as_root mv "${STAGE_DIR}" "${RELEASE_DIR}"
STAGE_DIR=""
PREVIOUS_RELEASE="$(readlink -f "${CURRENT_LINK}" 2>/dev/null || true)"

deploy_log "原子切换 current"
NEW_CURRENT_LINK="${APP_ROOT}/.current.${RELEASE_ID}"
deploy_as_root rm -f "${NEW_CURRENT_LINK}"
deploy_as_root ln -s "${RELEASE_DIR}" "${NEW_CURRENT_LINK}"
deploy_as_root mv -Tf "${NEW_CURRENT_LINK}" "${CURRENT_LINK}"

deploy_log "安装并启动 systemd 服务"
if ! write_systemd_unit || ! activate_service; then
  deploy_as_root journalctl -u "${SYSTEMD_SERVICE_NAME}" -n 80 --no-pager >&2 || true
  rollback_release || true
  deploy_fail "新版本服务启动失败。数据库迁移不会自动回滚，请检查兼容性。"
fi

prune_old_releases
trap - EXIT

deploy_log "版本部署完成"
printf "release=%s\n" "${RELEASE_DIR}"
printf "current=%s\n" "$(readlink -f "${CURRENT_LINK}")"
printf "service=%s.service\n" "${SYSTEMD_SERVICE_NAME}"
printf "mode=%s\n" "${SERVICE_MODE}"
