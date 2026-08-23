#!/bin/bash -p
# This process receives deployment-user input and launches root-owned install
# steps.  Harden its interpreter before resolving SCRIPT_DIR or sourcing any
# helper; supported root children receive their own empty environment below.
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/release_environment.sh"
source "${SCRIPT_DIR}/lib/service_identity.sh"
source "${SCRIPT_DIR}/lib/safety.sh"
source "${SCRIPT_DIR}/lib/runtime_paths.sh"
source "${SCRIPT_DIR}/lib/layout.sh"
source "${SCRIPT_DIR}/ntfy/lib.sh"

APP_NAME="${APP_NAME:-northstar-quant}"
SERVICE_USER="${SERVICE_USER:-northstar}"
SYSTEMD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME:-northstar-quant}"
SERVICE_HOME="${SERVICE_HOME:-/var/lib/northstar}"
APP_ROOT="${APP_ROOT:-/opt/northstar}"
CONFIG_DIR="${CONFIG_DIR:-/etc/northstar}"
STATE_DIR="${STATE_DIR:-/var/lib/northstar}"
CACHE_DIR="${CACHE_DIR:-/var/cache/northstar}"
LOG_DIR="${LOG_DIR:-/var/log/northstar}"
SERVICE_MODE="${SERVICE_MODE:-health}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
UV_VERSION="${UV_VERSION:-}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
REMOTE_TMP="${REMOTE_TMP:-}"
SETUP_SERVER="${SETUP_SERVER:-0}"
CONFIRM_LIVE_DEPLOY="${CONFIRM_LIVE_DEPLOY:-NO}"
UPLOADED_ARTIFACT_TARBALL="${UPLOADED_ARTIFACT_TARBALL:-}"
ARTIFACT_TARBALL=""
ARTIFACT_SHA256="${ARTIFACT_SHA256:-}"
RELEASE_ID="${RELEASE_ID:-}"
ENV_FILE_PATH="${ENV_FILE_PATH:-}"
CANDIDATE_ARTIFACT_FILE=""
CANDIDATE_ENV_FILE=""
CANDIDATE_NTFY_BOOTSTRAP_FILE=""
ACTIVE_ENV_SNAPSHOT=""
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

deploy_assert_bool "SETUP_SERVER" "${SETUP_SERVER}"
deploy_assert_bool "DASHBOARD_DEPLOY_ENABLED" "${DASHBOARD_DEPLOY_ENABLED}"
deploy_assert_bool "UPLOAD_NTFY_BOOTSTRAP" "${UPLOAD_NTFY_BOOTSTRAP}"
deploy_assert_safe_name "APP_NAME" "${APP_NAME}"
deploy_assert_safe_name "SERVICE_USER" "${SERVICE_USER}"
deploy_assert_safe_name "SYSTEMD_SERVICE_NAME" "${SYSTEMD_SERVICE_NAME}"
deploy_assert_safe_name "RELEASE_ID" "${RELEASE_ID}"
deploy_configure_linux_layout
ntfy_validate_deployment_config

if [ "$(uname -s)" != "Linux" ]; then
  deploy_fail "远程部署目标必须是 Linux。"
fi
if [ "${EUID}" -eq 0 ]; then
  deploy_fail "provision.sh 必须由独立的非 root SSH 部署身份运行。"
fi
if [ -z "${REMOTE_TMP}" ]; then
  deploy_fail "REMOTE_TMP 不能为空。"
fi
deploy_need_cmd realpath
deploy_need_cmd id
deploy_need_cmd mkdir
deploy_need_cmd rmdir
deploy_need_cmd stat
deploy_need_cmd getent
deploy_need_cmd awk
REMOTE_TMP="$(realpath -m -- "${REMOTE_TMP}")"

if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ] && [ "${NTFY_DEPLOY_ENABLED}" != "1" ]; then
  deploy_fail "UPLOAD_NTFY_BOOTSTRAP=1 时必须同时设置 NTFY_DEPLOY_ENABLED=1。"
fi
if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ] && [ -z "${NTFY_BOOTSTRAP_PATH}" ]; then
  deploy_fail "UPLOAD_NTFY_BOOTSTRAP=1 时必须提供远程一次性 NTFY_BOOTSTRAP_PATH。"
fi

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
umask 077
# The deployment mutex never lives in deployment-user-owned staging.  Layout
# validation above fixes DEPLOY_STATE_DIR to /var/lib/northstar/deploy-state;
# no controller input can select this root-owned path.
DEPLOY_LOCK_PATH="${DEPLOY_STATE_DIR}/deployment.lock"
DEPLOY_LOCK_ACQUIRED=0
DEPLOY_LOCK_METADATA=""
DEPLOY_LOCK_RELEASE_ID=""
DEPLOY_LOCK_RELEASE_ALLOWED=0

assert_deployment_lock_parent() {
  local parent_metadata

  # install-runtime creates this exact directory during first setup.  On an
  # upgrade it must already be the same root-only directory; never repair an
  # unexpected parent because that could adopt deployment-user state.
  if ! deploy_assert_root_controlled_directory_chain "${DEPLOY_STATE_DIR}"; then
    deploy_fail "部署互斥锁的受管状态目录祖先链不符合 root 控制边界。"
  fi
  if ! deploy_as_root test -d "${DEPLOY_STATE_DIR}" ||
    deploy_as_root test -L "${DEPLOY_STATE_DIR}"; then
    deploy_fail "部署互斥锁的受管状态目录必须是普通非链接目录。"
  fi
  if ! parent_metadata="$(
    deploy_as_root stat -c '%u:%g:%a' -- "${DEPLOY_STATE_DIR}" 2>/dev/null
  )"; then
    deploy_fail "无法验证部署互斥锁的受管状态目录。"
  fi
  if [ "${parent_metadata}" != "0:0:700" ]; then
    deploy_fail "部署互斥锁的受管状态目录必须是 root:root 0700。"
  fi
}

acquire_deployment_lock() {
  local lock_metadata

  assert_deployment_lock_parent

  # mkdir is root-side and atomic.  Any pre-existing final object is
  # deliberately untrusted: it can be a surviving interrupted deployment or
  # an unexpected object, neither of which may be reopened or repaired.
  if ! deploy_as_root mkdir -m 0700 -- "${DEPLOY_LOCK_PATH}" 2>/dev/null; then
    if deploy_as_root test -L "${DEPLOY_LOCK_PATH}"; then
      deploy_fail "部署互斥锁不得是符号链接。"
    fi
    deploy_fail "部署互斥锁已存在或无法原子创建；拒绝并发或遗留的未验证锁。"
  fi
  if ! deploy_as_root test -d "${DEPLOY_LOCK_PATH}" ||
    deploy_as_root test -L "${DEPLOY_LOCK_PATH}"; then
    deploy_fail "部署互斥锁目录必须是 root 创建的普通非链接目录。"
  fi
  if ! lock_metadata="$(
    deploy_as_root stat -c '%u:%g:%a:%d:%i' -- "${DEPLOY_LOCK_PATH}" 2>/dev/null
  )"; then
    deploy_fail "无法验证部署互斥锁目录。"
  fi
  case "${lock_metadata}" in
    "0:0:700:"*:*)
      ;;
    *)
    deploy_fail "部署互斥锁目录不是 root:root 0700 的受管目录。"
      ;;
  esac
  DEPLOY_LOCK_METADATA="${lock_metadata}"
  DEPLOY_LOCK_RELEASE_ID="${RELEASE_ID}"
  DEPLOY_LOCK_ACQUIRED=1
  DEPLOY_LOCK_RELEASE_ALLOWED=1
}

retain_deployment_lock() {
  # A signal or an explicitly reported unknown root-child outcome can leave
  # state between steps.  Preserve the fixed root lock as evidence and block
  # concurrent deployment until a human follows the recovery procedure.
  DEPLOY_LOCK_RELEASE_ALLOWED=0
}

retain_deployment_lock_on_signal() {
  retain_deployment_lock
  exit "$1"
}

release_deployment_lock() {
  local exit_status="$?"
  local current_metadata

  # Ordinary exits may leave a root-owned artifact candidate behind when a
  # later unprivileged validation fails before its local error branch.  Clean
  # only those known outcomes; signal/unknown outcomes retain exact evidence.
  if [ "${DEPLOY_LOCK_RELEASE_ALLOWED}" = "1" ] &&
    [ "${exit_status}" -gt 0 ] && [ "${exit_status}" -lt 128 ] &&
    declare -F cleanup_known_failed_handoffs >/dev/null; then
    if ! cleanup_known_failed_handoffs; then
      # A failed root-side candidate cleanup means the ordinary outcome is no
      # longer fully known. Keep the mutex and exact evidence for recovery.
      retain_deployment_lock
    fi
  fi

  # Do not release after an interrupted deployment. A root child may have an
  # unknown outcome, so a stale lock is safer than a concurrent deployment.
  if [ "${DEPLOY_LOCK_ACQUIRED}" = "1" ] &&
    [ "${DEPLOY_LOCK_RELEASE_ALLOWED}" = "1" ] &&
    [ "${DEPLOY_LOCK_RELEASE_ID}" = "${RELEASE_ID}" ] &&
    [ "${exit_status}" -lt 128 ]; then
    current_metadata="$(
      deploy_as_root stat -c '%u:%g:%a:%d:%i' -- "${DEPLOY_LOCK_PATH}" 2>/dev/null || true
    )"
    if deploy_as_root test -d "${DEPLOY_LOCK_PATH}" &&
      ! deploy_as_root test -L "${DEPLOY_LOCK_PATH}" &&
      [ "${current_metadata}" = "${DEPLOY_LOCK_METADATA}" ]; then
      deploy_as_root rmdir -- "${DEPLOY_LOCK_PATH}" || true
    fi
  fi
  return "${exit_status}"
}

handoff_unprivileged_upload() {
  local handoff_kind="$1"
  local uploaded_path="$2"
  local expected_path="$3"
  local uploaded_fd
  local handoff_status

  uploaded_path="$(realpath -m -- "${uploaded_path}")"
  if [ "${uploaded_path}" != "${expected_path}" ]; then
    deploy_fail "待交接文件不位于当前受限部署暂存目录。"
  fi
  if [ ! -f "${uploaded_path}" ] || [ -L "${uploaded_path}" ]; then
    deploy_fail "待交接文件必须是普通文件，且不得是符号链接。"
  fi

  # Open the deployment-user-owned pathname exactly once as the unprivileged
  # SSH identity. The root receiver receives only this already-open stream,
  # never a mutable /tmp or /var/tmp pathname.
  if ! exec {uploaded_fd}<"${uploaded_path}"; then
    deploy_fail "无法打开待交接的部署上传文件。"
  fi
  if [ ! -f "/proc/self/fd/${uploaded_fd}" ]; then
    exec {uploaded_fd}<&- || true
    deploy_fail "待交接文件打开后不再是普通文件。"
  fi

  if cat <&"${uploaded_fd}" |
    deploy_as_root env -i \
      PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
      /usr/bin/python3 -I "${SCRIPT_DIR}/secure_handoff.py" "${handoff_kind}" "${RELEASE_ID}"; then
    :
  else
    handoff_status=$?
    exec {uploaded_fd}<&- || true
    if [ "${handoff_status}" -ge 128 ]; then
      retain_deployment_lock
      deploy_fail "候选机密文件交接被中断；为避免删除未知状态，已保留受管候选文件。"
    fi
    deploy_fail "无法将部署上传文件安全交接给 root 管理的候选文件。"
  fi
  exec {uploaded_fd}<&-
}

handoff_unprivileged_artifact() {
  local uploaded_path="$1"
  local expected_path="$2"
  local uploaded_fd
  local handoff_status

  uploaded_path="$(realpath -m -- "${uploaded_path}")"
  if [ "${uploaded_path}" != "${expected_path}" ]; then
    deploy_fail "部署制品不位于当前受限部署暂存目录。"
  fi
  if [ ! -f "${uploaded_path}" ] || [ -L "${uploaded_path}" ]; then
    deploy_fail "部署制品必须是普通文件，且不得是符号链接。"
  fi

  # Open the deployment-user-owned pathname exactly once before root receives
  # any bytes.  The privileged receiver gets stdin plus the expected digest,
  # never a mutable /tmp or /var/tmp artifact pathname.
  if ! exec {uploaded_fd}<"${uploaded_path}"; then
    deploy_fail "无法打开部署上传制品。"
  fi
  if [ ! -f "/proc/self/fd/${uploaded_fd}" ]; then
    exec {uploaded_fd}<&- || true
    deploy_fail "部署制品打开后不再是普通文件。"
  fi

  if cat <&"${uploaded_fd}" |
    deploy_as_root env -i \
      PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
      /usr/bin/python3 -I "${SCRIPT_DIR}/artifact_handoff.py" \
        "${RELEASE_ID}" "${ARTIFACT_SHA256}"; then
    :
  else
    handoff_status=$?
    exec {uploaded_fd}<&- || true
    if [ "${handoff_status}" -ge 128 ]; then
      retain_deployment_lock
      deploy_fail "部署制品交接被中断；为避免删除未知状态，已保留 root 管理的候选制品。"
    fi
    deploy_fail "无法将部署上传制品安全交接给 root 管理的候选文件。"
  fi
  exec {uploaded_fd}<&-
}

cleanup_managed_artifact_candidate() {
  local expected_candidate

  if [ -z "${CANDIDATE_ARTIFACT_FILE}" ]; then
    return 0
  fi
  expected_candidate="${DEPLOY_STATE_DIR}/.artifact.${RELEASE_ID}.candidate.tar.gz"
  if [ "${CANDIDATE_ARTIFACT_FILE}" != "${expected_candidate}" ]; then
    return 1
  fi
  deploy_as_root rm -f -- "${CANDIDATE_ARTIFACT_FILE}"
}

cleanup_known_failed_handoffs() {
  local managed_candidate
  local cleanup_failed=0

  # This is deliberately called only on a child process's ordinary failure.
  # An EXIT/INT/TERM trap would erase evidence or a valid candidate after an
  # unknown interruption; durable interrupted-cutover recovery is handled by
  # later platform work.
  if ! cleanup_managed_artifact_candidate; then
    deploy_log "警告：无法清理已知失败部署的 root 管理候选制品。"
    cleanup_failed=1
  fi
  for managed_candidate in \
    "${CANDIDATE_ENV_FILE}" \
    "${CANDIDATE_NTFY_BOOTSTRAP_FILE}"; do
    if [ -z "${managed_candidate}" ]; then
      continue
    fi
    if ! deploy_as_root rm -f -- "${managed_candidate}"; then
      deploy_log "警告：无法清理已知失败部署的 root 管理候选文件。"
      cleanup_failed=1
    fi
  done
  return "${cleanup_failed}"
}

cleanup_known_failed_handoffs_or_retain_lock() {
  if ! cleanup_known_failed_handoffs; then
    retain_deployment_lock
    deploy_log "警告：候选文件清理失败；已保留 root 部署互斥锁，拒绝后续并发部署。"
  fi
}

validate_managed_production_environment() {
  local managed_environment_file="$1"

  deploy_as_root env -i \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    DEPLOY_SCRIPT_DIR="${SCRIPT_DIR}" \
    ENVIRONMENT_FILE="${managed_environment_file}" \
    SERVICE_MODE="${SERVICE_MODE}" \
    CONFIRM_LIVE_DEPLOY="${CONFIRM_LIVE_DEPLOY}" \
    /bin/bash -p -c '
      set -euo pipefail
      source "${DEPLOY_SCRIPT_DIR}/lib/common.sh"
      source "${DEPLOY_SCRIPT_DIR}/lib/safety.sh"
      deploy_validate_production_env \
        "${ENVIRONMENT_FILE}" \
        "${SERVICE_MODE}" \
        "${CONFIRM_LIVE_DEPLOY}"
    '
}

assert_candidate_ntfy_configuration_matches_active_snapshot() {
  local candidate_environment_file="$1"
  local active_environment_file="$2"

  # Compare parsed values inside the root boundary. Never emit values here:
  # the NTFY token is secret and a mismatch must fail closed before the NTFY
  # root provisioner can mutate a still-active deployment's alert endpoint.
  deploy_as_root env -i \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    DEPLOY_SCRIPT_DIR="${SCRIPT_DIR}" \
    CANDIDATE_ENVIRONMENT_FILE="${candidate_environment_file}" \
    ACTIVE_ENVIRONMENT_FILE="${active_environment_file}" \
    /bin/bash -p -c '
      set -euo pipefail
      source "${DEPLOY_SCRIPT_DIR}/lib/common.sh"
      for environment_key in \
        NORTHSTAR_ALERT_MODE \
        NORTHSTAR_NTFY_BASE_URL \
        NORTHSTAR_NTFY_TOPIC \
        NORTHSTAR_NTFY_TOKEN; do
        candidate_value="$(
          deploy_read_env_value "${CANDIDATE_ENVIRONMENT_FILE}" "${environment_key}"
        )" || exit 1
        active_value="$(
          deploy_read_env_value "${ACTIVE_ENVIRONMENT_FILE}" "${environment_key}"
        )" || exit 1
        [ "${candidate_value}" = "${active_value}" ] || exit 1
      done
    '
}

validate_candidate_environment_cutover_precondition() {
  local pointer_state

  # A candidate can replace an already active release only after the existing
  # ENV_FILE -> current -> release snapshot chain has passed the same root-side
  # verification used by the final release installer.  Treat every partial or
  # dangling state as an upgrade, never as a first deployment.
  if ! pointer_state="$(
    deploy_as_root env -i \
      PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
      ENVIRONMENT_FILE="${ENV_FILE}" \
      CURRENT_RELEASE_LINK="${CURRENT_LINK}" \
      /bin/bash -p -c '
        set -euo pipefail
        if [ -e "${ENVIRONMENT_FILE}" ] ||
          [ -L "${ENVIRONMENT_FILE}" ] ||
          [ -e "${CURRENT_RELEASE_LINK}" ] ||
          [ -L "${CURRENT_RELEASE_LINK}" ]; then
          printf present
        else
          printf absent
        fi
      '
  )"; then
    return 1
  fi
  case "${pointer_state}" in
    absent)
      return 0
      ;;
    present)
      ;;
    *)
      return 1
      ;;
  esac

  if [ "${pointer_state}" != "present" ]; then
    return 1
  fi

  deploy_resolve_managed_active_environment_snapshot
}

if [ "${SETUP_SERVER}" = "1" ]; then
  deploy_log "初始化服务器运行时"
  deploy_as_root env -i \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    APP_NAME="${APP_NAME}" \
    SERVICE_USER="${SERVICE_USER}" \
    SYSTEMD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME}" \
    SERVICE_HOME="${SERVICE_HOME}" \
    APP_ROOT="${APP_ROOT}" \
    CONFIG_DIR="${CONFIG_DIR}" \
    STATE_DIR="${STATE_DIR}" \
    CACHE_DIR="${CACHE_DIR}" \
    LOG_DIR="${LOG_DIR}" \
    PYTHON_VERSION="${PYTHON_VERSION}" \
    UV_VERSION="${UV_VERSION}" \
    RUNTIME_STORAGE_DIR="${RUNTIME_STORAGE_DIR}" \
    RUNTIME_DOWNLOADS_DIR="${RUNTIME_DOWNLOADS_DIR}" \
    RUNTIME_REPORTS_DIR="${RUNTIME_REPORTS_DIR}" \
    RUNTIME_LOG_DIR="${RUNTIME_LOG_DIR}" \
    RUNTIME_CACHE_DIR="${RUNTIME_CACHE_DIR}" \
    RUNTIME_MATPLOTLIB_DIR="${RUNTIME_MATPLOTLIB_DIR}" \
    /bin/bash -p "${SCRIPT_DIR}/install-runtime.sh"
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  deploy_fail "服务用户不存在：${SERVICE_USER}。首次部署请设置 SETUP_SERVER=1。"
fi
if ! deploy_assert_canonical_service_identity; then
  deploy_fail "既有服务账户不符合受管 northstar 身份、主组、home 或 nologin shell 约束。"
fi

# On the very first setup only, two callers can reach install-runtime before
# this state directory exists.  That bootstrap handles fixed root paths and
# the fixed service identity only; it receives no uploaded artifact, secret,
# release tree, environment snapshot, or current-pointer transition.  A
# concurrent bootstrap therefore fails closed on the underlying root setup
# rather than crossing an untrusted deployment input boundary.  The root-only
# state directory and service identity are now verified, so every subsequent
# privileged handoff, release transition, and ordinary cleanup is serialized
# by the fixed root-owned mutex.
acquire_deployment_lock
trap release_deployment_lock EXIT
trap 'retain_deployment_lock_on_signal 129' HUP
trap 'retain_deployment_lock_on_signal 130' INT
trap 'retain_deployment_lock_on_signal 131' QUIT
trap 'retain_deployment_lock_on_signal 141' PIPE
trap 'retain_deployment_lock_on_signal 143' TERM

UPLOAD_DIRECTORY="${REMOTE_TMP}/${APP_NAME}-deploy-${RELEASE_ID}"
EXPECTED_ARTIFACT_PATH="${UPLOAD_DIRECTORY}/${APP_NAME}-${RELEASE_ID}.tar.gz"
EXPECTED_ENV_FILE_PATH="${UPLOAD_DIRECTORY}/active.env"
EXPECTED_NTFY_BOOTSTRAP_PATH="${UPLOAD_DIRECTORY}/private-ntfy.bootstrap.env"

if [ -z "${UPLOADED_ARTIFACT_TARBALL}" ]; then
  deploy_fail "缺少部署上传制品路径。"
fi
if [[ ! "${ARTIFACT_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  deploy_fail "部署制品 SHA-256 必须是小写的 64 位十六进制摘要。"
fi

if [ -n "${ENV_FILE_PATH}" ] || [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then
  if ! ACTIVE_ENV_SNAPSHOT="$(validate_candidate_environment_cutover_precondition)"; then
    deploy_fail "候选升级或 NTFY bootstrap 前的活动环境必须是完整、受管的 current release 快照链；仅当 ${ENV_FILE} 与 ${CURRENT_LINK} 都不存在时才允许首次部署。"
  fi
fi
if [ -n "${ACTIVE_ENV_SNAPSHOT}" ] && [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then
  deploy_fail "已有活动 release 时禁止上传 NTFY bootstrap；bootstrap 仅允许首次部署。"
fi

deploy_log "以流式方式暂存并校验 root 管理的部署制品"
handoff_unprivileged_artifact \
  "${UPLOADED_ARTIFACT_TARBALL}" \
  "${EXPECTED_ARTIFACT_PATH}"
CANDIDATE_ARTIFACT_FILE="${DEPLOY_STATE_DIR}/.artifact.${RELEASE_ID}.candidate.tar.gz"
ARTIFACT_TARBALL="${CANDIDATE_ARTIFACT_FILE}"

if [ -n "${ENV_FILE_PATH}" ]; then
  deploy_log "以流式方式暂存 root 管理的候选生产环境文件"
  handoff_unprivileged_upload \
    "environment" \
    "${ENV_FILE_PATH}" \
    "${EXPECTED_ENV_FILE_PATH}"
  CANDIDATE_ENV_FILE="${CONFIG_DIR}/.${APP_NAME}.${RELEASE_ID}.candidate.env"
  if ! deploy_assert_managed_environment_file "${CANDIDATE_ENV_FILE}" ||
    ! validate_managed_production_environment "${CANDIDATE_ENV_FILE}"; then
    cleanup_known_failed_handoffs_or_retain_lock
    deploy_fail "候选生产环境文件未通过服务器生产与交易安全门禁。"
  fi
fi

if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then
  deploy_log "以流式方式暂存 root 管理的一次性 NTFY bootstrap 文件"
  handoff_unprivileged_upload \
    "ntfy-bootstrap" \
    "${NTFY_BOOTSTRAP_PATH}" \
    "${EXPECTED_NTFY_BOOTSTRAP_PATH}"
  CANDIDATE_NTFY_BOOTSTRAP_FILE="${DEPLOY_STATE_DIR}/.ntfy-bootstrap.${RELEASE_ID}.candidate.env"
fi

if [ "${NTFY_DEPLOY_ENABLED}" = "1" ]; then
  # The root ntfy provisioner may read only a managed candidate or the
  # resolved immutable active snapshot, never a deployment-user-owned upload
  # path or a mutable current pointer.
  if [ -n "${CANDIDATE_ENV_FILE}" ]; then
    NTFY_APP_ENV_FILE="${CANDIDATE_ENV_FILE}"
  elif ! NTFY_APP_ENV_FILE="$(deploy_resolve_managed_active_environment_snapshot)"; then
    cleanup_known_failed_handoffs_or_retain_lock
    deploy_fail "私有 ntfy 缺少指向受管 release 环境快照的活动 .env；首次部署请同时设置 UPLOAD_ENV=1。"
  fi
  if ! deploy_assert_managed_environment_file "${NTFY_APP_ENV_FILE}" ||
    ! validate_managed_production_environment "${NTFY_APP_ENV_FILE}"; then
    cleanup_known_failed_handoffs_or_retain_lock
    deploy_fail "私有 ntfy 缺少可验证的活动 .env；首次部署请同时设置 UPLOAD_ENV=1。"
  fi
  if [ -n "${CANDIDATE_ENV_FILE}" ] && [ -n "${ACTIVE_ENV_SNAPSHOT}" ] &&
    ! assert_candidate_ntfy_configuration_matches_active_snapshot \
      "${CANDIDATE_ENV_FILE}" \
      "${ACTIVE_ENV_SNAPSHOT}"; then
    cleanup_known_failed_handoffs_or_retain_lock
    deploy_fail "候选环境改变了活动 release 的 NTFY 身份；应用切换完成前拒绝更新私有 NTFY。"
  fi
  deploy_log "部署私有 ntfy 与 Caddy TLS 反向代理"
  if deploy_as_root env -i \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    HOME="/root" \
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
    RELEASE_ID="${RELEASE_ID}" \
    RUNTIME_STORAGE_DIR="${RUNTIME_STORAGE_DIR}" \
    RUNTIME_DOWNLOADS_DIR="${RUNTIME_DOWNLOADS_DIR}" \
    RUNTIME_REPORTS_DIR="${RUNTIME_REPORTS_DIR}" \
    RUNTIME_LOG_DIR="${RUNTIME_LOG_DIR}" \
    RUNTIME_CACHE_DIR="${RUNTIME_CACHE_DIR}" \
    RUNTIME_MATPLOTLIB_DIR="${RUNTIME_MATPLOTLIB_DIR}" \
    UPLOAD_NTFY_BOOTSTRAP="${UPLOAD_NTFY_BOOTSTRAP}" \
    NTFY_BOOTSTRAP_PATH="${CANDIDATE_NTFY_BOOTSTRAP_FILE}" \
    /bin/bash -p "${SCRIPT_DIR}/ntfy/provision-ntfy.sh"; then
    :
  else
    ntfy_status=$?
    if [ "${ntfy_status}" -lt 128 ]; then
      cleanup_known_failed_handoffs_or_retain_lock
    fi
    if [ "${ntfy_status}" -ge 128 ]; then
      retain_deployment_lock
      deploy_fail "私有 ntfy 部署被中断；为避免删除未知状态，已保留受管候选文件。"
    fi
    deploy_fail "私有 ntfy 部署失败；已清理本次已知失败的候选文件。"
  fi
fi

if [ -z "${CANDIDATE_ENV_FILE}" ] &&
  ! deploy_resolve_managed_active_environment_snapshot >/dev/null; then
  cleanup_known_failed_handoffs_or_retain_lock
  deploy_fail "服务器缺少 ${ENV_FILE}。首次部署请设置 UPLOAD_ENV=1。"
fi

deploy_log "安装应用版本"
if deploy_as_root env -i \
  PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  APP_NAME="${APP_NAME}" \
  SERVICE_USER="${SERVICE_USER}" \
  SYSTEMD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME}" \
  SERVICE_HOME="${SERVICE_HOME}" \
  APP_ROOT="${APP_ROOT}" \
  CONFIG_DIR="${CONFIG_DIR}" \
  STATE_DIR="${STATE_DIR}" \
  CACHE_DIR="${CACHE_DIR}" \
  LOG_DIR="${LOG_DIR}" \
  SERVICE_MODE="${SERVICE_MODE}" \
  PYTHON_VERSION="${PYTHON_VERSION}" \
  KEEP_RELEASES="${KEEP_RELEASES}" \
  CONFIRM_LIVE_DEPLOY="${CONFIRM_LIVE_DEPLOY}" \
  RUNTIME_STORAGE_DIR="${RUNTIME_STORAGE_DIR}" \
  RUNTIME_DOWNLOADS_DIR="${RUNTIME_DOWNLOADS_DIR}" \
  RUNTIME_REPORTS_DIR="${RUNTIME_REPORTS_DIR}" \
  RUNTIME_LOG_DIR="${RUNTIME_LOG_DIR}" \
  RUNTIME_CACHE_DIR="${RUNTIME_CACHE_DIR}" \
  RUNTIME_MATPLOTLIB_DIR="${RUNTIME_MATPLOTLIB_DIR}" \
  DASHBOARD_DEPLOY_ENABLED="${DASHBOARD_DEPLOY_ENABLED}" \
  ARTIFACT_TARBALL="${ARTIFACT_TARBALL}" \
  ARTIFACT_SHA256="${ARTIFACT_SHA256}" \
  RELEASE_ID="${RELEASE_ID}" \
  CANDIDATE_ENV_FILE="${CANDIDATE_ENV_FILE}" \
  /bin/bash -p "${SCRIPT_DIR}/install-release.sh"; then
  :
else
  release_status=$?
  if [ "${release_status}" -lt 128 ]; then
    cleanup_known_failed_handoffs_or_retain_lock
  fi
  if [ "${release_status}" -ge 128 ]; then
    retain_deployment_lock
    deploy_fail "应用版本安装被中断；为避免删除未知状态，已保留受管候选文件。"
  fi
  deploy_fail "应用版本安装失败；已清理本次已知失败的候选文件。"
fi

if ! cleanup_managed_artifact_candidate; then
  retain_deployment_lock
  deploy_log "警告：release 已完成，但无法清理 root 管理的候选制品；已保留部署互斥锁，请按 P6-WP09 恢复流程人工检查。"
else
  CANDIDATE_ARTIFACT_FILE=""
fi

deploy_log "远程部署流程完成"
