#!/bin/bash -p
# Run privileged deployment entrypoints with a deterministic command lookup
# and without non-interactive Bash startup hooks inherited from the caller.
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/privileged_paths.sh"
source "${SCRIPT_DIR}/lib/release_environment.sh"
source "${SCRIPT_DIR}/lib/service_identity.sh"
source "${SCRIPT_DIR}/lib/safety.sh"
source "${SCRIPT_DIR}/lib/runtime_paths.sh"
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
SERVICE_MODE="${SERVICE_MODE:-health}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
CONFIRM_LIVE_DEPLOY="${CONFIRM_LIVE_DEPLOY:-NO}"
ARTIFACT_TARBALL="${ARTIFACT_TARBALL:-}"
ARTIFACT_SHA256="${ARTIFACT_SHA256:-}"
RELEASE_ID="${RELEASE_ID:-}"
CANDIDATE_ENV_FILE="${CANDIDATE_ENV_FILE:-}"
DASHBOARD_DEPLOY_ENABLED="${DASHBOARD_DEPLOY_ENABLED:-0}"
# Only the fixed, signature-verifying root gate may enable this mode.  It
# causes every release lifecycle boundary to be persisted before mutation and
# disables unsafe automatic service rollback after a migration attempt.
RELEASE_RUNNER_MODE="${RELEASE_RUNNER_MODE:-0}"
RELEASE_TRANSACTION_ROOT="${RELEASE_TRANSACTION_ROOT:-/var/lib/northstar/deploy-state/transactions}"
RELEASE_MANIFEST_FILE="${RELEASE_MANIFEST_FILE:-}"
RELEASE_MANIFEST_SIGNATURE_FILE="${RELEASE_MANIFEST_SIGNATURE_FILE:-}"

STAGE_DIR=""
RELEASE_DIR=""
PREVIOUS_RELEASE=""
CUTOVER_ACTIVE=false
MIGRATION_STARTED=false
DASHBOARD_HOME_DIR=""
DASHBOARD_DEPLOY_STATUS="disabled"
CANDIDATE_ENV_UPLOADED=false
RELEASE_ENV_FILE=""
# This is an ownership intent, not merely a post-copy success marker.  It is
# armed before the final snapshot rename so EXIT cleanup covers an interrupt
# immediately after that atomic operation.
RELEASE_ENV_FILE_CREATED=false
RELEASE_ENV_TEMP_FILE=""
PREVIOUS_SERVICE_ENABLED=false
PREVIOUS_SERVICE_ACTIVE=false

if [ "$(uname -s)" != "Linux" ]; then
  deploy_fail "版本安装脚本只支持 Linux。"
fi
for required_command in cp cut find getent install mktemp python3 readlink realpath sha256sum sort stat systemctl tar; do
  deploy_need_cmd "${required_command}"
done

deploy_assert_safe_name "APP_NAME" "${APP_NAME}"
deploy_assert_safe_name "SERVICE_USER" "${SERVICE_USER}"
deploy_assert_safe_name "SYSTEMD_SERVICE_NAME" "${SYSTEMD_SERVICE_NAME}"
deploy_assert_safe_name "RELEASE_ID" "${RELEASE_ID}"
deploy_assert_bool "DASHBOARD_DEPLOY_ENABLED" "${DASHBOARD_DEPLOY_ENABLED}"
deploy_assert_bool "RELEASE_RUNNER_MODE" "${RELEASE_RUNNER_MODE}"
deploy_configure_linux_layout

release_transaction_transition() {
  local state="$1"
  if [ "${RELEASE_RUNNER_MODE}" != "1" ]; then
    return 0
  fi
  /usr/bin/python3 -I "${SCRIPT_DIR}/release_transaction_hook.py" \
    --root "${RELEASE_TRANSACTION_ROOT}" transition "${RELEASE_ID}" "${state}" >/dev/null
}

assert_release_gate_manifest_evidence() {
  local expected_transaction_dir
  local manifest_metadata
  local signature_metadata

  if [ "${RELEASE_RUNNER_MODE}" != "1" ]; then
    return 0
  fi
  expected_transaction_dir="${DEPLOY_STATE_DIR}/transactions/${RELEASE_ID}"
  if [ "${RELEASE_MANIFEST_FILE}" != "${expected_transaction_dir}/release-manifest.json" ] ||
    [ "${RELEASE_MANIFEST_SIGNATURE_FILE}" != "${expected_transaction_dir}/release-manifest.sig" ]; then
    return 1
  fi
  manifest_metadata="$(deploy_as_root stat -c '%u:%g:%a:%h:%F' -- "${RELEASE_MANIFEST_FILE}" 2>/dev/null || true)"
  signature_metadata="$(deploy_as_root stat -c '%u:%g:%a:%h:%F' -- "${RELEASE_MANIFEST_SIGNATURE_FILE}" 2>/dev/null || true)"
  [ "${manifest_metadata}" = "0:0:600:1:regular file" ] &&
    [ "${signature_metadata}" = "0:0:600:1:regular file" ]
}

assert_managed_artifact_candidate() {
  local expected_candidate
  local parent_metadata
  local candidate_metadata

  expected_candidate="${DEPLOY_STATE_DIR}/.artifact.${RELEASE_ID}.candidate.tar.gz"
  if [ "${ARTIFACT_TARBALL}" != "${expected_candidate}" ]; then
    return 1
  fi
  if ! deploy_as_root test -d "${DEPLOY_STATE_DIR}" ||
    deploy_as_root test -L "${DEPLOY_STATE_DIR}"; then
    return 1
  fi
  parent_metadata="$(deploy_as_root stat -c '%u:%g:%a' -- "${DEPLOY_STATE_DIR}")" || return 1
  if [ "${parent_metadata}" != "0:0:700" ]; then
    return 1
  fi
  if ! deploy_as_root test -f "${ARTIFACT_TARBALL}" ||
    deploy_as_root test -L "${ARTIFACT_TARBALL}"; then
    return 1
  fi
  candidate_metadata="$(deploy_as_root stat -c '%u:%g:%a:%h' -- "${ARTIFACT_TARBALL}")" || return 1
  [ "${candidate_metadata}" = "0:0:600:1" ]
}

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  deploy_fail "服务用户不存在：${SERVICE_USER}。请先运行 install-runtime.sh。"
fi
if ! deploy_assert_canonical_service_identity; then
  deploy_fail "既有服务账户不符合受管 northstar 身份、主组、home 或 nologin shell 约束。"
fi
if ! deploy_prepare_fixed_privileged_layout "${SERVICE_USER}"; then
  deploy_fail "无法安全准备固定的受特权控制生产目录。"
fi
if ! assert_release_gate_manifest_evidence; then
  deploy_fail "release gate manifest evidence is missing or does not match the root transaction."
fi

ACTIVE_ENV_SNAPSHOT=""
if deploy_as_root test -e "${ENV_FILE}" ||
  deploy_as_root test -L "${ENV_FILE}" ||
  deploy_as_root test -e "${CURRENT_LINK}" ||
  deploy_as_root test -L "${CURRENT_LINK}"; then
  ACTIVE_ENV_SNAPSHOT="$(deploy_resolve_managed_active_environment_snapshot)" || \
    deploy_fail "已有活动部署必须具有完整且受管的环境快照链；拒绝在未知回滚状态下升级。"
  PREVIOUS_RELEASE="$(deploy_as_root readlink -- "${CURRENT_LINK}")" || \
    deploy_fail "无法读取已验证活动 release 的直接指针。"
else
  # A first installation is valid only when both public pointers are absent.
  # Any partial/legacy state above must have entered the guarded branch and
  # failed closed through deploy_resolve_managed_active_environment_snapshot.
  PREVIOUS_RELEASE=""
fi

if [ -z "${CANDIDATE_ENV_FILE}" ]; then
  if [ -z "${ACTIVE_ENV_SNAPSHOT}" ]; then
    deploy_fail "首次部署必须上传 root 管理的候选 .env。"
  fi
  CANDIDATE_ENV_FILE="${ACTIVE_ENV_SNAPSHOT}"
else
  CANDIDATE_ENV_FILE="$(realpath -m -- "${CANDIDATE_ENV_FILE}")"
  expected_candidate_env_file="${CONFIG_DIR}/.${APP_NAME}.${RELEASE_ID}.candidate.env"
  if [ "${CANDIDATE_ENV_FILE}" != "${expected_candidate_env_file}" ]; then
    deploy_fail "候选生产环境文件必须位于受管配置目录并绑定当前 release。"
  fi
  CANDIDATE_ENV_UPLOADED=true
fi

RELEASE_DIR="${RELEASES_DIR}/${RELEASE_ID}"
RELEASE_ENV_FILE="${ENV_RELEASES_DIR}/${RELEASE_ID}.env"
SYSTEMD_UNIT_FILE="${SYSTEMD_UNIT_DIR}/${SYSTEMD_SERVICE_NAME}.service"
DASHBOARD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME}-dashboard"
DASHBOARD_UNIT_FILE="${SYSTEMD_UNIT_DIR}/${DASHBOARD_SERVICE_NAME}.service"
# These are independently managed, direct service-writable leaves.  They
# must not live below RUNTIME_CACHE_DIR: that path is itself a service-owned
# leaf and therefore cannot safely contain another privileged deployment or
# systemd target.
DASHBOARD_HOME_DIR="${CACHE_DIR}/dashboard"
VENV_BUILD_ROOT="${CACHE_DIR}/venv-build"
VENV_BUILD_DIR="${VENV_BUILD_ROOT}/${RELEASE_ID}"

case "${SERVICE_MODE}" in
  health|scheduler) ;;
  *) deploy_fail "SERVICE_MODE 只能是 health 或 scheduler。" ;;
esac
case "${KEEP_RELEASES}" in
  *[!0-9]*|"") deploy_fail "KEEP_RELEASES 必须是整数。" ;;
esac
if [ "${KEEP_RELEASES}" -lt 2 ]; then
  deploy_fail "KEEP_RELEASES 至少为 2，以保留一个可回退版本。"
fi
if ! assert_managed_artifact_candidate; then
  deploy_fail "部署制品必须是绑定当前 release 的 root 管理候选文件。"
fi
if [[ ! "${ARTIFACT_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  deploy_fail "部署制品 SHA-256 必须是小写的 64 位十六进制摘要。"
fi
actual_artifact_sha256="$(sha256sum "${ARTIFACT_TARBALL}" | awk '{print $1}')"
if [ "${actual_artifact_sha256}" != "${ARTIFACT_SHA256}" ]; then
  deploy_fail "部署制品 SHA-256 校验失败。"
fi
if ! deploy_assert_managed_environment_file "${CANDIDATE_ENV_FILE}"; then
  deploy_fail "服务器候选生产环境文件必须是 root:${SERVICE_USER} 0640 的普通文件。"
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

validate_artifact_contents() {
  local unsafe_entry
  local unsupported_entry
  local duplicate_entry

  if ! unsafe_entry="$(
    LC_ALL=C tar -tvzf "${ARTIFACT_TARBALL}" |
      awk '$1 !~ /^[-d]/ || $1 ~ /[sStT]/ { if (!found) { print; found = 1 } }'
  )"; then
    deploy_fail "无法验证部署制品元数据。"
  fi
  if [ -n "${unsafe_entry}" ]; then
    deploy_fail "部署制品包含链接、特殊文件或特权模式：${unsafe_entry}"
  fi

  if ! unsupported_entry="$(
    LC_ALL=C tar -tzf "${ARTIFACT_TARBALL}" |
      awk '
        !/^[A-Za-z0-9._/-]+$/ { if (!found) { print; found = 1 }; next }
        $0 == "pyproject.toml" || $0 == "README.md" || $0 == "uv.lock" || $0 == "alembic.ini" ||
          $0 == "DEPLOY_ARTIFACT_META.txt" ||
          $0 == "scripts/ci/check_dependency_policy.py" ||
          $0 == "scripts/ci/bootstrap_pep517.py" ||
          $0 ~ /^(alembic|configs|src|templates|ontology|datasets)(\/|$)/ ||
          $0 == "infra" || $0 == "infra/" || $0 ~ /^infra\/systemd(\/|$)/ {
            next
          }
        { if (!found) { print; found = 1 } }
      '
  )"; then
    deploy_fail "无法校验部署制品成员路径。"
  fi
  if [ -n "${unsupported_entry}" ]; then
    deploy_fail "部署制品包含未授权成员路径：${unsupported_entry}"
  fi

  if ! duplicate_entry="$(
    LC_ALL=C tar -tzf "${ARTIFACT_TARBALL}" |
      LC_ALL=C sort |
      awk 'previous == $0 { if (!found) { print; found = 1 } } { previous = $0 }'
  )"; then
    deploy_fail "无法检查部署制品重复成员。"
  fi
  if [ -n "${duplicate_entry}" ]; then
    deploy_fail "部署制品包含重复成员：${duplicate_entry}"
  fi
}

validate_artifact_extraction_policy() {
  # Validate the complete logical tar stream before root asks GNU tar to
  # create anything in the release stage.  The validator has fixed member and
  # unpacked-byte limits and accepts no links, devices, sparse files, special
  # modes or ambiguous names.  It runs with an empty inherited environment.
  if ! deploy_as_root env -i \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    /usr/bin/python3 -I "${SCRIPT_DIR}/archive_policy.py" \
      --validate-deployment-artifact "${ARTIFACT_TARBALL}"; then
    deploy_fail "Deployment artifact does not satisfy the root extraction safety policy."
  fi
}

release_environment_snapshot_is_published() {
  local release_environment_link_target

  if [ -z "${RELEASE_DIR}" ] || [ -z "${RELEASE_ENV_FILE}" ] ||
    ! deploy_assert_root_owned_directory "${RELEASE_DIR}" 0 755 ||
    ! deploy_as_root test -L "${RELEASE_DIR}/.env" ||
    ! release_environment_link_target="$(deploy_as_root readlink -- "${RELEASE_DIR}/.env")" ||
    [ "${release_environment_link_target}" != "${RELEASE_ENV_FILE}" ] ||
    ! deploy_assert_managed_environment_file "${RELEASE_ENV_FILE}"; then
    return 1
  fi
}

assert_root_owned_tree_without_mounts() {
  local tree_path="$1"
  local tree_metadata

  if [ -z "${tree_path}" ] ||
    ! deploy_as_root test -d "${tree_path}" ||
    deploy_as_root test -L "${tree_path}" ||
    ! tree_metadata="$(deploy_as_root stat -c '%u:%g' -- "${tree_path}")" ||
    [ "${tree_metadata}" != "0:0" ]; then
    return 1
  fi
  deploy_as_root python3 -I "${SCRIPT_DIR}/mount_safety.py" "${tree_path}"
}

cleanup_stage() {
  # The dependency build directory is owned by the unprivileged service
  # account.  Never recursively inspect or remove that tree as root: a
  # failed build is cleaned by the same identity that created it.
  if [ -n "${VENV_BUILD_DIR}" ]; then
    deploy_as_user "${SERVICE_USER}" rm -rf -- "${VENV_BUILD_DIR}" || true
  fi
  if [ -n "${STAGE_DIR}" ] && deploy_as_root test -d "${STAGE_DIR}"; then
    if assert_root_owned_tree_without_mounts "${STAGE_DIR}"; then
      deploy_as_root rm -rf --one-file-system -- "${STAGE_DIR}" || true
    else
      printf '拒绝清理包含挂载点或非 root 所有的 release stage：%s\n' "${STAGE_DIR}" >&2
    fi
  fi
  if [ -n "${RELEASE_ENV_TEMP_FILE:-}" ] &&
    deploy_as_root test -f "${RELEASE_ENV_TEMP_FILE}"; then
    deploy_as_root rm -f -- "${RELEASE_ENV_TEMP_FILE}" || true
  fi
  if [ "${RELEASE_ENV_FILE_CREATED}" = true ] &&
    deploy_as_root test -f "${RELEASE_ENV_FILE}"; then
    # A signal can arrive after the atomic stage -> release rename but before
    # shell assignments below.  Preserve the snapshot whenever the published
    # release is already bound to it; otherwise clean the uncommitted copy.
    if release_environment_snapshot_is_published; then
      RELEASE_ENV_FILE_CREATED=false
    else
      deploy_as_root rm -f -- "${RELEASE_ENV_FILE}" || true
    fi
  fi
  if [ "${CANDIDATE_ENV_UPLOADED}" = true ] &&
    deploy_as_root test -f "${CANDIDATE_ENV_FILE}"; then
    deploy_as_root rm -f -- "${CANDIDATE_ENV_FILE}" || true
  fi
}
trap cleanup_stage EXIT

run_release_command() {
  local release_dir="$1"
  shift
  deploy_as_user "${SERVICE_USER}" env \
    HOME="${SERVICE_HOME}" \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_CACHE_DIR="${UV_CACHE_DIR}" \
    UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR}" \
    XDG_CACHE_HOME="${RUNTIME_CACHE_DIR}" \
    MPLCONFIGDIR="${RUNTIME_MATPLOTLIB_DIR}" \
    NORTHSTAR_PROJECT_ROOT="${release_dir}" \
    /bin/bash -c 'cd "$1"; shift; exec "$@"' bash "${release_dir}" "$@"
}

systemd_snapshot_path() {
  local release_dir="$1"
  local service_name="$2"
  printf '%s\n' "${release_dir}/.northstar/systemd/${service_name}.service"
}

render_systemd_snapshot() {
  local release_dir="$1"
  local template_name="$2"
  local service_name="$3"
  local template_file="${release_dir}/infra/systemd/${template_name}"
  local snapshot_file
  local rendered_file

  snapshot_file="$(systemd_snapshot_path "${release_dir}" "${service_name}")"
  if [ ! -f "${template_file}" ]; then
    printf 'systemd 模板不存在：%s\n' "${template_file}" >&2
    return 1
  fi
  deploy_as_root install -d -o root -g root -m 0755 "$(dirname -- "${snapshot_file}")" || return 1
  rendered_file="$(deploy_as_root mktemp "${DEPLOY_STATE_DIR}/.${service_name}.unit.XXXXXX")" || return 1
  if ! sed \
    -e "s|@SERVICE_USER@|${SERVICE_USER}|g" \
    -e "s|@CURRENT_LINK@|${CURRENT_LINK}|g" \
    -e "s|@SERVICE_HOME@|${SERVICE_HOME}|g" \
    -e "s|@STATE_DIR@|${STATE_DIR}|g" \
    -e "s|@UV_CACHE_DIR@|${UV_CACHE_DIR}|g" \
    -e "s|@UV_PYTHON_INSTALL_DIR@|${UV_PYTHON_INSTALL_DIR}|g" \
    -e "s|@RUNTIME_STORAGE_DIR@|${RUNTIME_STORAGE_DIR}|g" \
    -e "s|@RUNTIME_DOWNLOADS_DIR@|${RUNTIME_DOWNLOADS_DIR}|g" \
    -e "s|@RUNTIME_REPORTS_DIR@|${RUNTIME_REPORTS_DIR}|g" \
    -e "s|@RUNTIME_LOG_DIR@|${RUNTIME_LOG_DIR}|g" \
    -e "s|@RUNTIME_CACHE_DIR@|${RUNTIME_CACHE_DIR}|g" \
    -e "s|@RUNTIME_MATPLOTLIB_DIR@|${RUNTIME_MATPLOTLIB_DIR}|g" \
    -e "s|@DASHBOARD_HOME_DIR@|${DASHBOARD_HOME_DIR}|g" \
    -e "s|@RELEASE_ID@|${RELEASE_ID}|g" \
    -e "s|@ARTIFACT_SHA256@|${ARTIFACT_SHA256}|g" \
    "${template_file}" > "${rendered_file}"; then
    deploy_as_root rm -f -- "${rendered_file}" || true
    return 1
  fi
  if ! deploy_as_root install -m 0644 -o root -g root "${rendered_file}" "${snapshot_file}"; then
    deploy_as_root rm -f -- "${rendered_file}" || true
    return 1
  fi
  deploy_as_root rm -f -- "${rendered_file}"
}

render_systemd_unit() {
  render_systemd_snapshot "${STAGE_DIR}" "${SERVICE_MODE}.service.in" "${SYSTEMD_SERVICE_NAME}"
}

render_dashboard_systemd_unit() {
  render_systemd_snapshot "${STAGE_DIR}" "dashboard.service.in" "${DASHBOARD_SERVICE_NAME}"
}

assert_no_foreign_unit_fragment() {
  local service_name="$1"
  local managed_unit_file="$2"
  local load_state
  local fragment_path
  local unit_root
  local candidate_unit_file

  for unit_root in \
    "/etc/systemd/system.control" \
    "/run/systemd/system.control" \
    "/run/systemd/transient" \
    "/run/systemd/generator.early" \
    "/etc/systemd/system.attached" \
    "/run/systemd/system.attached" \
    "/run/systemd/system" \
    "/run/systemd/generator" \
    "/usr/local/lib/systemd/system" \
    "/usr/local/share/systemd/system" \
    "/usr/lib/systemd/system" \
    "/usr/share/systemd/system" \
    "/lib/systemd/system" \
    "/run/systemd/generator.late"; do
    candidate_unit_file="${unit_root}/${service_name}.service"
    # The active root-managed unit is expected at this exact path.  Every
    # other unit search root remains foreign and must fail closed.
    if [ "${candidate_unit_file}" = "${managed_unit_file}" ]; then
      continue
    fi
    if deploy_as_root test -e "${candidate_unit_file}" ||
      deploy_as_root test -L "${candidate_unit_file}"; then
      return 1
    fi
  done

  # `systemctl show -p FragmentPath` can fail for a genuinely absent unit.
  # First deployment is safe only when systemd explicitly reports not-found;
  # DBus/query failures and every other state remain fail-closed.
  load_state="$(deploy_as_root systemctl show -p LoadState --value "${service_name}.service")" || return 1
  case "${load_state}" in
    not-found)
      if deploy_as_root test -e "${managed_unit_file}" ||
        deploy_as_root test -L "${managed_unit_file}"; then
        return 1
      fi
      return 0
      ;;
    loaded)
      ;;
    *)
      return 1
      ;;
  esac

  fragment_path="$(deploy_as_root systemctl show -p FragmentPath --value "${service_name}.service")" || return 1
  [ "${fragment_path}" = "${managed_unit_file}" ]
}

assert_no_unit_dropins() {
  local service_name="$1"
  local dropin_dir
  local load_state
  local dropin_paths

  for dropin_dir in \
    "/etc/systemd/system.control/${service_name}.service.d" \
    "/run/systemd/system.control/${service_name}.service.d" \
    "/run/systemd/transient/${service_name}.service.d" \
    "/run/systemd/generator.early/${service_name}.service.d" \
    "/etc/systemd/system/${service_name}.service.d" \
    "/run/systemd/system/${service_name}.service.d" \
    "/run/systemd/generator/${service_name}.service.d" \
    "/etc/systemd/system.attached/${service_name}.service.d" \
    "/run/systemd/system.attached/${service_name}.service.d" \
    "/usr/local/lib/systemd/system/${service_name}.service.d" \
    "/usr/local/share/systemd/system/${service_name}.service.d" \
    "/usr/lib/systemd/system/${service_name}.service.d" \
    "/usr/share/systemd/system/${service_name}.service.d" \
    "/lib/systemd/system/${service_name}.service.d" \
    "/run/systemd/generator.late/${service_name}.service.d"; do
    if deploy_as_root test -e "${dropin_dir}" || deploy_as_root test -L "${dropin_dir}"; then
      return 1
    fi
  done

  # `DropInPaths` is not queryable for every absent unit.  A first deployment
  # may proceed only after a successful, explicit not-found result; all query
  # failures and non-loaded/non-absent states stay fail-closed.
  load_state="$(deploy_as_root systemctl show -p LoadState --value "${service_name}.service")" || return 1
  case "${load_state}" in
    not-found)
      return 0
      ;;
    loaded)
      ;;
    *)
      return 1
      ;;
  esac

  dropin_paths="$(deploy_as_root systemctl show -p DropInPaths --value "${service_name}.service")" || return 1
  [ -z "${dropin_paths}" ]
}

assert_managed_unit_snapshot() {
  local release_dir="$1"
  local service_name="$2"
  local unit_file="$3"
  local snapshot_file
  local installed_hash
  local snapshot_hash

  snapshot_file="$(systemd_snapshot_path "${release_dir}" "${service_name}")"
  if ! deploy_as_root test -f "${snapshot_file}" ||
    deploy_as_root test -L "${snapshot_file}" ||
    ! deploy_as_root test -f "${unit_file}" ||
    deploy_as_root test -L "${unit_file}" ||
    ! assert_no_unit_dropins "${service_name}" ||
    ! assert_no_foreign_unit_fragment "${service_name}" "${unit_file}"; then
    return 1
  fi
  installed_hash="$(deploy_as_root sha256sum "${unit_file}" | awk '{print $1}')" || return 1
  snapshot_hash="$(deploy_as_root sha256sum "${snapshot_file}" | awk '{print $1}')" || return 1
  [ "${installed_hash}" = "${snapshot_hash}" ]
}

prepare_systemd_rollback() {
  if ! assert_no_foreign_unit_fragment "${SYSTEMD_SERVICE_NAME}" "${SYSTEMD_UNIT_FILE}"; then
    deploy_fail "检测到非受管 systemd 单元；拒绝覆盖有效服务配置。"
  fi
  if ! assert_no_unit_dropins "${SYSTEMD_SERVICE_NAME}"; then
    deploy_fail "检测到未受管 systemd drop-in；拒绝覆盖有效服务配置。"
  fi
  if [ -n "${PREVIOUS_RELEASE}" ]; then
    if ! assert_managed_unit_snapshot \
      "${PREVIOUS_RELEASE}" "${SYSTEMD_SERVICE_NAME}" "${SYSTEMD_UNIT_FILE}"; then
      deploy_fail "当前 systemd 单元未与上一受管 release 快照一致；拒绝覆盖未知配置。"
    fi
  elif deploy_as_root test -e "${SYSTEMD_UNIT_FILE}" || deploy_as_root test -L "${SYSTEMD_UNIT_FILE}"; then
    deploy_fail "首次部署发现未受管 systemd 单元；拒绝覆盖未知配置。"
  fi
}

prepare_dashboard_systemd_transition() {
  local previous_snapshot

  if ! assert_no_foreign_unit_fragment "${DASHBOARD_SERVICE_NAME}" "${DASHBOARD_UNIT_FILE}"; then
    deploy_fail "检测到非受管 Dashboard systemd 单元；拒绝覆盖有效服务配置。"
  fi
  if ! assert_no_unit_dropins "${DASHBOARD_SERVICE_NAME}"; then
    deploy_fail "检测到未受管 Dashboard systemd drop-in；拒绝覆盖有效服务配置。"
  fi
  if [ -z "${PREVIOUS_RELEASE}" ]; then
    if deploy_as_root test -e "${DASHBOARD_UNIT_FILE}" ||
      deploy_as_root test -L "${DASHBOARD_UNIT_FILE}"; then
      deploy_fail "首次部署发现未受管 Dashboard systemd 单元；拒绝覆盖未知配置。"
    fi
    return
  fi

  previous_snapshot="$(systemd_snapshot_path "${PREVIOUS_RELEASE}" "${DASHBOARD_SERVICE_NAME}")"
  if deploy_as_root test -f "${previous_snapshot}"; then
    if deploy_as_root test -e "${DASHBOARD_UNIT_FILE}" ||
      deploy_as_root test -L "${DASHBOARD_UNIT_FILE}"; then
      if ! assert_managed_unit_snapshot \
        "${PREVIOUS_RELEASE}" "${DASHBOARD_SERVICE_NAME}" "${DASHBOARD_UNIT_FILE}"; then
        deploy_fail "当前 Dashboard systemd 单元未与上一受管 release 快照一致；拒绝覆盖未知配置。"
      fi
    elif deploy_as_root systemctl is-active --quiet "${DASHBOARD_SERVICE_NAME}.service"; then
      deploy_fail "Dashboard 服务仍在运行但缺少受管单元；拒绝覆盖未知配置。"
    fi
    return
  fi

  if deploy_as_root test -e "${DASHBOARD_UNIT_FILE}" ||
    deploy_as_root test -L "${DASHBOARD_UNIT_FILE}" ||
    deploy_as_root systemctl is-active --quiet "${DASHBOARD_SERVICE_NAME}.service"; then
    deploy_fail "上一 release 没有 Dashboard 快照，但发现活动 Dashboard 配置；拒绝覆盖未知配置。"
  fi
}

install_snapshot_as_active_unit() {
  local snapshot_file="$1"
  local unit_file="$2"
  local service_name="$3"
  deploy_as_root test -f "${snapshot_file}" || return 1
  deploy_as_root install -m 0644 -o root -g root "${snapshot_file}" "${unit_file}" || return 1
  deploy_as_root systemctl daemon-reload || return 1
}

enable_managed_unit() {
  local service_name="$1"
  deploy_as_root systemctl enable "${service_name}.service" >/dev/null
}

install_rendered_systemd_unit() {
  install_snapshot_as_active_unit \
    "$(systemd_snapshot_path "${RELEASE_DIR}" "${SYSTEMD_SERVICE_NAME}")" \
    "${SYSTEMD_UNIT_FILE}" \
    "${SYSTEMD_SERVICE_NAME}"
}

install_dashboard_systemd_unit() {
  install_snapshot_as_active_unit \
    "$(systemd_snapshot_path "${RELEASE_DIR}" "${DASHBOARD_SERVICE_NAME}")" \
    "${DASHBOARD_UNIT_FILE}" \
    "${DASHBOARD_SERVICE_NAME}" || return 1
  deploy_as_root systemctl restart "${DASHBOARD_SERVICE_NAME}.service" || return 1
  deploy_as_root systemctl is-active --quiet "${DASHBOARD_SERVICE_NAME}.service" || return 1
  enable_managed_unit "${DASHBOARD_SERVICE_NAME}"
}

restore_systemd_unit() {
  if [ -n "${PREVIOUS_RELEASE}" ]; then
    install_snapshot_as_active_unit \
      "$(systemd_snapshot_path "${PREVIOUS_RELEASE}" "${SYSTEMD_SERVICE_NAME}")" \
      "${SYSTEMD_UNIT_FILE}" \
      "${SYSTEMD_SERVICE_NAME}"
    return
  fi
  # A first-install failure can occur after the new unit was enabled.  Clear
  # and verify enablement while the unit still exists, before removing it.
  if ! reset_managed_service_to_disabled; then
    return 1
  fi
  deploy_as_root rm -f -- "${SYSTEMD_UNIT_FILE}" || return 1
  deploy_as_root systemctl daemon-reload
}

prepare_release_environment_snapshot() {
  deploy_assert_managed_environment_file "${CANDIDATE_ENV_FILE}" || return 1
  if deploy_as_root test -e "${RELEASE_ENV_FILE}" || deploy_as_root test -L "${RELEASE_ENV_FILE}"; then
    return 1
  fi
  # Arm cleanup before the final snapshot rename.  If a signal lands after
  # the rename but before another shell assignment, the exact new snapshot is
  # still removed unless a release has already been published with it.
  RELEASE_ENV_FILE_CREATED=true
  RELEASE_ENV_TEMP_FILE="$(deploy_as_root mktemp "${ENV_RELEASES_DIR}/.${RELEASE_ID}.env.XXXXXX")" || return 1
  if ! deploy_as_root install -m 0640 -o root -g "${SERVICE_USER}" \
    "${CANDIDATE_ENV_FILE}" "${RELEASE_ENV_TEMP_FILE}" ||
    ! deploy_as_root mv -Tf "${RELEASE_ENV_TEMP_FILE}" "${RELEASE_ENV_FILE}"; then
    deploy_as_root rm -f -- "${RELEASE_ENV_TEMP_FILE}" || true
    RELEASE_ENV_TEMP_FILE=""
    return 1
  fi
  RELEASE_ENV_TEMP_FILE=""
}

bind_staged_release_to_environment_snapshot() {
  local release_link_temp

  deploy_assert_managed_environment_file "${RELEASE_ENV_FILE}" || return 1
  release_link_temp="${STAGE_DIR}/.${APP_NAME}.env.release"
  deploy_as_root rm -f -- "${release_link_temp}" || return 1
  if ! deploy_as_root ln -s "${RELEASE_ENV_FILE}" "${release_link_temp}" ||
    ! deploy_as_root mv -Tf "${release_link_temp}" "${STAGE_DIR}/.env"; then
    deploy_as_root rm -f -- "${release_link_temp}" || true
    return 1
  fi
}

ensure_active_environment_pointer() {
  local pointer_temp

  if deploy_as_root test -e "${ENV_FILE}" || deploy_as_root test -L "${ENV_FILE}"; then
    [ -L "${ENV_FILE}" ] && [ "$(readlink -- "${ENV_FILE}")" = "${CURRENT_LINK}/.env" ]
    return
  fi
  pointer_temp="${CONFIG_DIR}/.${APP_NAME}.env.pointer.${RELEASE_ID}"
  if deploy_as_root test -e "${pointer_temp}" || deploy_as_root test -L "${pointer_temp}"; then
    return 1
  fi
  if ! deploy_as_root ln -s "${CURRENT_LINK}/.env" "${pointer_temp}" ||
    ! deploy_as_root mv -Tf "${pointer_temp}" "${ENV_FILE}"; then
    deploy_as_root rm -f -- "${pointer_temp}" || true
    return 1
  fi
}

dashboard_unit_file_exists() {
  deploy_as_root test -e "${DASHBOARD_UNIT_FILE}" ||
    deploy_as_root test -L "${DASHBOARD_UNIT_FILE}"
}

dashboard_service_is_inactive() {
  local active_state

  active_state="$(
    deploy_as_root systemctl show -p ActiveState --value "${DASHBOARD_SERVICE_NAME}.service"
  )" || return 1
  [ "${active_state}" = "inactive" ]
}

dashboard_service_enablement_state() {
  local enablement_state

  # `systemctl is-enabled` intentionally exits nonzero for `disabled`; its
  # textual state is therefore the contract. Callers decide whether a known
  # absent unit is safe; unknown/error states are never accepted.
  enablement_state="$(
    deploy_as_root systemctl is-enabled "${DASHBOARD_SERVICE_NAME}.service" 2>/dev/null || true
  )"
  case "${enablement_state}" in
    enabled|disabled|not-found)
      printf '%s\n' "${enablement_state}"
      ;;
    *)
      return 1
      ;;
  esac
}

disable_dashboard_and_verify() {
  local enablement_state

  if ! deploy_as_root systemctl disable --now "${DASHBOARD_SERVICE_NAME}.service" >/dev/null; then
    return 1
  fi
  if ! dashboard_service_is_inactive; then
    return 1
  fi
  if ! enablement_state="$(dashboard_service_enablement_state)" ||
    [ "${enablement_state}" != "disabled" ]; then
    return 1
  fi
}

dashboard_absent_unit_is_safe() {
  local enablement_state

  if ! dashboard_service_is_inactive ||
    ! enablement_state="$(dashboard_service_enablement_state)"; then
    return 1
  fi
  case "${enablement_state}" in
    disabled|not-found)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

disable_dashboard_systemd_unit() {
  if dashboard_unit_file_exists; then
    if ! disable_dashboard_and_verify; then
      printf '无法停止并精确禁用私网 Dashboard 服务：%s.service\n' "${DASHBOARD_SERVICE_NAME}" >&2
      return 1
    fi
    deploy_as_root rm -f -- "${DASHBOARD_UNIT_FILE}" || return 1
  elif ! dashboard_absent_unit_is_safe; then
    printf 'Dashboard unit 不存在但服务状态或开机启用状态不安全：%s.service\n' "${DASHBOARD_SERVICE_NAME}" >&2
    return 1
  fi
  deploy_as_root systemctl daemon-reload || return 1
  if dashboard_unit_file_exists; then
    return 1
  fi
  dashboard_absent_unit_is_safe
}

configure_dashboard_systemd_unit() {
  if [ "${DASHBOARD_DEPLOY_ENABLED}" = "0" ]; then
    disable_dashboard_systemd_unit
    DASHBOARD_DEPLOY_STATUS="disabled"
    return
  fi
  deploy_log "启动已冻结快照的私网 Dashboard 服务"
  install_dashboard_systemd_unit || return 1
  DASHBOARD_DEPLOY_STATUS="enabled"
}

fail_closed_dashboard_systemd_unit() {
  if dashboard_unit_file_exists; then
    if ! disable_dashboard_and_verify; then
      return 1
    fi
    if ! deploy_as_root rm -f -- "${DASHBOARD_UNIT_FILE}"; then
      return 1
    fi
  elif ! dashboard_absent_unit_is_safe; then
    return 1
  fi
  if ! deploy_as_root systemctl daemon-reload ||
    dashboard_unit_file_exists ||
    ! dashboard_absent_unit_is_safe; then
    return 1
  fi
  DASHBOARD_DEPLOY_STATUS="disabled_after_failure"
}

switch_current_release() {
  local new_current_link="${APP_ROOT}/.current.${RELEASE_ID}"
  deploy_as_root rm -f -- "${new_current_link}" || return 1
  deploy_as_root ln -s "${RELEASE_DIR}" "${new_current_link}" || return 1
  if ! deploy_as_root mv -Tf "${new_current_link}" "${CURRENT_LINK}"; then
    deploy_as_root rm -f -- "${new_current_link}" || true
    return 1
  fi
}

stop_current_service() {
  # A first deployment has neither a managed release nor a unit to stop.
  # Once a current release exists, prepare_systemd_rollback has already
  # verified the active unit against its immutable release snapshot.
  if [ -z "${PREVIOUS_RELEASE}" ]; then
    return 0
  fi
  deploy_as_root systemctl stop "${SYSTEMD_SERVICE_NAME}.service"
}

capture_current_service_state() {
  local enablement_state

  if [ -z "${PREVIOUS_RELEASE}" ]; then
    return 0
  fi
  if ! enablement_state="$(managed_service_enablement_state)"; then
    deploy_fail "无法确定当前服务的开机启用状态；拒绝切换。"
  fi
  case "${enablement_state}" in
    enabled) PREVIOUS_SERVICE_ENABLED=true ;;
    disabled) PREVIOUS_SERVICE_ENABLED=false ;;
    *) deploy_fail "当前服务的开机启用状态无效：${enablement_state}" ;;
  esac
  if deploy_as_root systemctl is-active --quiet "${SYSTEMD_SERVICE_NAME}.service"; then
    PREVIOUS_SERVICE_ACTIVE=true
  fi
}

managed_service_enablement_state() {
  local enablement_state

  enablement_state="$(
    deploy_as_root systemctl is-enabled "${SYSTEMD_SERVICE_NAME}.service" 2>/dev/null || true
  )"
  case "${enablement_state}" in
    enabled|disabled)
      printf '%s\n' "${enablement_state}"
      ;;
    *)
      return 1
      ;;
  esac
}

reset_managed_service_to_disabled() {
  local enablement_state

  if ! deploy_as_root systemctl disable "${SYSTEMD_SERVICE_NAME}.service" >/dev/null; then
    return 1
  fi
  if ! enablement_state="$(managed_service_enablement_state)" ||
    [ "${enablement_state}" != "disabled" ]; then
    return 1
  fi
}

disable_current_service() {
  if [ -z "${PREVIOUS_RELEASE}" ]; then
    return 0
  fi
  deploy_as_root systemctl disable "${SYSTEMD_SERVICE_NAME}.service" >/dev/null
}

restore_previous_service_enablement() {
  # A failed new-release enable may have created unit symlinks even when the
  # previous release was deliberately disabled.  Always reset to disabled
  # first, verify the exact state, and only then restore prior enablement.
  if ! reset_managed_service_to_disabled; then
    return 1
  fi
  if [ "${PREVIOUS_SERVICE_ENABLED}" != true ]; then
    return 0
  fi
  if ! enable_managed_unit "${SYSTEMD_SERVICE_NAME}"; then
    return 1
  fi
  if ! enablement_state="$(managed_service_enablement_state)" ||
    [ "${enablement_state}" != "enabled" ]; then
    return 1
  fi
}

activate_service() {
  deploy_as_root systemctl start "${SYSTEMD_SERVICE_NAME}.service" || return 1
  if [ "${SERVICE_MODE}" = "scheduler" ]; then
    sleep 3
  fi
  deploy_as_root systemctl is-active --quiet "${SYSTEMD_SERVICE_NAME}.service" || return 1
  deploy_log "执行切换后健康检查"
  run_release_command "${RELEASE_DIR}" "${RELEASE_DIR}/.venv/bin/northstar" health --fail-on-blocked
}

rollback_release() {
  deploy_log "服务切换失败，回退到上一版本"
  if [ "${RELEASE_RUNNER_MODE}" = "1" ] && [ "${MIGRATION_STARTED}" = true ]; then
    # A successful or interrupted forward migration may make the previous
    # executable incompatible with the database.  Keep the deployment
    # fail-closed: do not restart old code and do not attempt a DB downgrade.
    deploy_as_root systemctl stop "${SYSTEMD_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    printf 'release runner retained fail-closed state after migration intent; manual recovery is required.\n' >&2
    return 1
  fi
  deploy_as_root systemctl stop "${SYSTEMD_SERVICE_NAME}.service" >/dev/null 2>&1 || true
  if ! restore_systemd_unit; then
    printf '无法恢复上一版本的 systemd 服务配置。\n' >&2
    return 1
  fi
  if [ -n "${PREVIOUS_RELEASE}" ] && deploy_as_root test -d "${PREVIOUS_RELEASE}"; then
    local rollback_link="${APP_ROOT}/.current.rollback"
    if ! deploy_as_root rm -f -- "${rollback_link}" ||
      ! deploy_as_root ln -s "${PREVIOUS_RELEASE}" "${rollback_link}" ||
      ! deploy_as_root mv -Tf "${rollback_link}" "${CURRENT_LINK}"; then
      deploy_as_root rm -f -- "${rollback_link}" || true
      printf '无法切换回上一版本的 current 链接。\n' >&2
      return 1
    fi
    if ! restore_previous_service_enablement; then
      printf '无法恢复上一版本的开机启动状态。\n' >&2
      return 1
    fi
    if [ "${PREVIOUS_SERVICE_ACTIVE}" != true ]; then
      deploy_log "上一版本已恢复为切换前的未运行状态：${PREVIOUS_RELEASE}"
      return 0
    fi
    if deploy_as_root systemctl restart "${SYSTEMD_SERVICE_NAME}.service" &&
      deploy_as_root systemctl is-active --quiet "${SYSTEMD_SERVICE_NAME}.service" &&
      run_release_command "${PREVIOUS_RELEASE}" "${PREVIOUS_RELEASE}/.venv/bin/northstar" health --fail-on-blocked; then
      deploy_log "上一版本已恢复：${PREVIOUS_RELEASE}"
      return 0
    fi
    printf '上一版本未能通过恢复后健康检查。\n' >&2
    return 1
  fi
  deploy_as_root rm -f -- "${CURRENT_LINK}" || return 1
  printf '没有可回退的上一版本。\n' >&2
  return 1
}

recover_interrupted_cutover() {
  local status="$1"
  trap - ERR INT TERM
  if [ "${RELEASE_RUNNER_MODE}" = "1" ] && [ "${MIGRATION_STARTED}" = true ]; then
    deploy_as_root systemctl stop "${SYSTEMD_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    release_transaction_transition recovery_required || true
    printf 'release runner interrupted after migration intent; service remains stopped for manual recovery.\n' >&2
    exit "${status}"
  fi
  if [ "${CUTOVER_ACTIVE}" = true ]; then
    printf '部署切换被中断，尝试恢复上一版本服务。\n' >&2
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
  local pruned_release_id
  local active_release_target
  local active_release_id
  local expected_active_release_dir

  # Pruning is the only deliberate recursive deletion in this installer.  It
  # is confined to the canonical, root-owned releases directory and never
  # follows a mutable current pointer or a mount point.
  if ! deploy_assert_root_owned_directory "${RELEASES_DIR}" 0 755 ||
    ! deploy_as_root test -L "${CURRENT_LINK}" ||
    ! active_release_target="$(deploy_as_root readlink -- "${CURRENT_LINK}")"; then
    return 1
  fi
  case "${active_release_target}" in
    "${RELEASES_DIR}/"*)
      active_release_id="${active_release_target#"${RELEASES_DIR}/"}"
      ;;
    *)
      return 1
      ;;
  esac
  deploy_assert_safe_name "active release identifier" "${active_release_id}" || return 1
  expected_active_release_dir="${RELEASES_DIR}/${active_release_id}"
  if [ "${active_release_target}" != "${expected_active_release_dir}" ] ||
    ! deploy_assert_root_owned_directory "${expected_active_release_dir}" 0 755; then
    return 1
  fi

  while IFS= read -r release_dir; do
    count=$((count + 1))
    if [ "${count}" -le "${KEEP_RELEASES}" ]; then
      continue
    fi
    if [ "${release_dir}" = "${expected_active_release_dir}" ]; then
      continue
    fi
    pruned_release_id="${release_dir##*/}"
    deploy_assert_safe_name "pruned release identifier" "${pruned_release_id}"
    if [ "${release_dir}" != "${RELEASES_DIR}/${pruned_release_id}" ] ||
      ! deploy_assert_root_owned_directory "${release_dir}" 0 755; then
      return 1
    fi
    assert_root_owned_tree_without_mounts "${release_dir}" || return 1
    deploy_as_root rm -rf --one-file-system -- "${release_dir}" || return 1
    deploy_as_root rm -f -- "${ENV_RELEASES_DIR}/${pruned_release_id}.env" || return 1
  done < <(
    find -P "${RELEASES_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name '.*' \
      -printf '%T@ %p\n' | sort -nr | cut -d' ' -f2-
  )
}

seal_staged_release() {
  assert_root_owned_tree_without_mounts "${STAGE_DIR}" || return 1
  deploy_as_root find -P "${STAGE_DIR}" -xdev \( -type f -o -type d \) \
    -exec chown root:root -- {} + || return 1
  deploy_as_root find -P "${STAGE_DIR}" -xdev -type d -exec chmod 0755 -- {} + || return 1
  deploy_as_root find -P "${STAGE_DIR}" -xdev \( -type f -o -type d \) \
    -exec chmod a-s -- {} + || return 1
  deploy_as_root find -P "${STAGE_DIR}" -xdev -type f -exec chmod go-w -- {} + || return 1
  if deploy_as_root test -f "${STAGE_DIR}/configs/app.yaml"; then
    deploy_as_root chown root:"${SERVICE_USER}" "${STAGE_DIR}/configs/app.yaml" || return 1
    deploy_as_root chmod 0640 "${STAGE_DIR}/configs/app.yaml" || return 1
  fi
  if deploy_as_root test -e "${STAGE_DIR}/.venv"; then
    if ! deploy_as_root test -d "${STAGE_DIR}/.venv" || deploy_as_root test -L "${STAGE_DIR}/.venv"; then
      return 1
    fi
    deploy_as_root find -P "${STAGE_DIR}/.venv" -xdev \( -type f -o -type d \) \
      -exec chown root:"${SERVICE_USER}" -- {} + || return 1
    deploy_as_root find -P "${STAGE_DIR}/.venv" -xdev -type d -exec chmod 0750 -- {} + || return 1
    deploy_as_root find -P "${STAGE_DIR}/.venv" -xdev -type f -perm /111 \
      -exec chmod 0750 -- {} + || return 1
    deploy_as_root find -P "${STAGE_DIR}/.venv" -xdev -type f ! -perm /111 \
      -exec chmod 0640 -- {} + || return 1
  fi
}

publish_release_manifest_evidence() {
  if [ "${RELEASE_RUNNER_MODE}" != "1" ]; then
    return 0
  fi
  assert_release_gate_manifest_evidence || return 1
  deploy_as_root install -d -m 0755 -o root -g root -- "${STAGE_DIR}/.northstar" || return 1
  deploy_as_root install -m 0644 -o root -g root -- \
    "${RELEASE_MANIFEST_FILE}" "${STAGE_DIR}/.northstar/release-manifest.json" || return 1
  deploy_as_root install -m 0644 -o root -g root -- \
    "${RELEASE_MANIFEST_SIGNATURE_FILE}" "${STAGE_DIR}/.northstar/release-manifest.sig" || return 1
}

deploy_validate_production_env "${CANDIDATE_ENV_FILE:-${ENV_FILE}}" "${SERVICE_MODE}" "${CONFIRM_LIVE_DEPLOY}"
validate_artifact_extraction_policy
validate_artifact
validate_artifact_contents

for runtime_parent_dir in "${STATE_DIR}" "${CACHE_DIR}" "${LOG_DIR}"; do
  if ! deploy_prepare_runtime_parent_directory "${runtime_parent_dir}" "${SERVICE_USER}"; then
    deploy_fail "无法安全准备运行时 root 父目录：${runtime_parent_dir}"
  fi
done
for runtime_dir in \
  "${RUNTIME_CACHE_DIR}" "${RUNTIME_LOG_DIR}" "${RUNTIME_MATPLOTLIB_DIR}" \
  "${RUNTIME_REPORTS_DIR}" "${RUNTIME_STORAGE_DIR}" "${RUNTIME_DOWNLOADS_DIR}" \
  "${UV_CACHE_DIR}" "${VENV_BUILD_ROOT}"; do
  if ! deploy_prepare_runtime_leaf_directory "${runtime_dir}" "${SERVICE_USER}"; then
    deploy_fail "无法安全准备运行时可写叶子目录：${runtime_dir}"
  fi
done
if [ "${DASHBOARD_DEPLOY_ENABLED}" = "1" ]; then
  if ! deploy_prepare_runtime_leaf_directory "${DASHBOARD_HOME_DIR}" "${SERVICE_USER}"; then
    deploy_fail "无法安全准备 Dashboard 运行时叶子目录：${DASHBOARD_HOME_DIR}"
  fi
fi

if deploy_as_root test -e "${RELEASE_DIR}"; then
  deploy_fail "版本目录已经存在：${RELEASE_DIR}"
fi
deploy_log "解压版本 ${RELEASE_ID}"
STAGE_DIR="$(deploy_as_root mktemp -d "${RELEASES_DIR}/.${RELEASE_ID}.stage.XXXXXX")"
deploy_as_root tar --extract --gzip --file="${ARTIFACT_TARBALL}" --directory="${STAGE_DIR}" \
  --no-same-owner --no-same-permissions

for required_path in \
  pyproject.toml README.md uv.lock alembic.ini src configs configs/app.example.yaml \
  infra/systemd/health.service.in infra/systemd/scheduler.service.in infra/systemd/dashboard.service.in \
  scripts/ci/check_dependency_policy.py scripts/ci/bootstrap_pep517.py; do
  if ! deploy_as_root test -e "${STAGE_DIR}/${required_path}"; then
    deploy_fail "部署制品缺少：${required_path}"
  fi
done
for forbidden_path in .env .venv logs storage reports configs/app.yaml configs/app.local.yaml; do
  if deploy_as_root test -e "${STAGE_DIR}/${forbidden_path}"; then
    deploy_fail "部署制品不应包含运行时路径：${forbidden_path}"
  fi
done

# Artifact source and systemd templates must become root-owned before any
# unprivileged dependency build runs. The virtual environment itself stays
# outside the release while northstar builds it, then enters the release only
# through a root-side validated archive receiver.
publish_release_manifest_evidence || deploy_fail "无法将已验签的 release manifest 固化到候选 release。"
seal_staged_release || deploy_fail "无法冻结部署制品源码。"

deploy_as_root ln -s "${CANDIDATE_ENV_FILE}" "${STAGE_DIR}/.env"
deploy_log "从完整模板生成新版本活动应用配置"
deploy_write_active_app_config \
  "${STAGE_DIR}/configs/app.example.yaml" \
  "${STAGE_DIR}/configs/app.yaml" \
  "${SERVICE_USER}"
deploy_as_root ln -s "${RUNTIME_LOG_DIR}" "${STAGE_DIR}/logs"
deploy_as_root ln -s "${RUNTIME_STORAGE_DIR}" "${STAGE_DIR}/storage"
deploy_as_root ln -s "${RUNTIME_REPORTS_DIR}" "${STAGE_DIR}/reports"

deploy_log "按 uv.lock 安装生产依赖"
# VENV_BUILD_ROOT was just validated as a managed service-writable leaf with
# mode 0750.  Do not let an unprivileged `install -d -m 0700` silently mutate
# that boundary: only release-specific children are private build scratch.
deploy_as_user "${SERVICE_USER}" test -d "${VENV_BUILD_ROOT}"
deploy_as_user "${SERVICE_USER}" rm -rf -- "${VENV_BUILD_DIR}"
if ! assert_root_owned_tree_without_mounts "${UV_PYTHON_INSTALL_DIR}"; then
  deploy_fail "受 root 控制的 Python 目录不满足无挂载、只读边界。"
fi
MANAGED_BOOTSTRAP_PYTHON="$(
  deploy_as_user "${SERVICE_USER}" env -i \
    PATH="/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin" \
    HOME="${SERVICE_HOME}" \
    UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR}" \
    /usr/local/bin/uv python find --no-config --no-project --managed-python \
      --no-python-downloads --resolve-links "${PYTHON_VERSION}"
)" || deploy_fail "无法找到受 root 管理的 Python ${PYTHON_VERSION}。"
if [ -z "${MANAGED_BOOTSTRAP_PYTHON}" ] ||
  [[ "${MANAGED_BOOTSTRAP_PYTHON}" == *$'\n'* ]] ||
  [[ "${MANAGED_BOOTSTRAP_PYTHON}" == *$'\r'* ]]; then
  deploy_fail "受管理 Python 路径无效。"
fi
MANAGED_BOOTSTRAP_PYTHON="$(deploy_as_root realpath -e -- "${MANAGED_BOOTSTRAP_PYTHON}")" || \
  deploy_fail "无法解析受管理 Python 路径。"
case "${MANAGED_BOOTSTRAP_PYTHON}" in
  "${UV_PYTHON_INSTALL_DIR}"/*)
    ;;
  *)
    deploy_fail "受管理 Python 位于不允许的目录。"
    ;;
esac
if ! deploy_as_root test -f "${MANAGED_BOOTSTRAP_PYTHON}" ||
  deploy_as_root test -L "${MANAGED_BOOTSTRAP_PYTHON}" ||
  ! deploy_as_user "${SERVICE_USER}" test -x "${MANAGED_BOOTSTRAP_PYTHON}"; then
  deploy_fail "受管理 Python 不是可执行普通文件。"
fi
deploy_as_user "${SERVICE_USER}" env -i \
  PATH="/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin" \
  HOME="${SERVICE_HOME}" \
  PYTHONDONTWRITEBYTECODE=1 \
  XDG_CACHE_HOME="${RUNTIME_CACHE_DIR}" \
  MPLCONFIGDIR="${RUNTIME_MATPLOTLIB_DIR}" \
  "${MANAGED_BOOTSTRAP_PYTHON}" -I "${STAGE_DIR}/scripts/ci/bootstrap_pep517.py" \
    --profile release --project-root "${STAGE_DIR}" --venv "${VENV_BUILD_DIR}" \
    --link-mode copy --python "${MANAGED_BOOTSTRAP_PYTHON}" \
    --managed-python-dir "${UV_PYTHON_INSTALL_DIR}"
deploy_log "验证并导入由服务账户构建的虚拟环境"
deploy_as_user "${SERVICE_USER}" tar --create --dereference --hard-dereference --format=posix \
  --file=- -C "${VENV_BUILD_DIR}" . |
  deploy_as_root env -i \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    /usr/bin/python3 -I "${SCRIPT_DIR}/venv_archive.py" \
      --target-dir "${STAGE_DIR}/.venv" \
      --temporary-dir "${DEPLOY_STATE_DIR}" \
      --service-group "${SERVICE_USER}"
deploy_as_user "${SERVICE_USER}" rm -rf -- "${VENV_BUILD_DIR}"

# The candidate has now been fully assembled and validated below the private
# stage directory.  Persist this before the forward database migration intent;
# a transaction must never claim a migration before it has a complete stage.
release_transaction_transition staged || deploy_fail "cannot persist staged release transaction state"
deploy_log "执行数据库迁移"
if [ "${RELEASE_RUNNER_MODE}" = "1" ]; then
  MIGRATION_STARTED=true
  release_transaction_transition migration_started || deploy_fail "cannot persist database migration intent"
fi
run_release_command "${STAGE_DIR}" "${STAGE_DIR}/.venv/bin/northstar" init-db
release_transaction_transition migrated || deploy_fail "cannot persist database migration state"
deploy_log "执行发布前健康检查"
run_release_command "${STAGE_DIR}" "${STAGE_DIR}/.venv/bin/northstar" health --fail-on-blocked
release_transaction_transition candidate_healthy || deploy_fail "cannot persist candidate health state"

prepare_release_environment_snapshot || deploy_fail "无法固化 release 专属环境文件快照。"
bind_staged_release_to_environment_snapshot || deploy_fail "无法将待发布版本绑定到其环境文件快照。"
deploy_log "冻结已验证 release 与其 systemd 快照"
seal_staged_release || deploy_fail "无法冻结已验证 release。"
render_systemd_unit || deploy_fail "无法生成受 release 绑定的 systemd 服务快照。"
if [ "${DASHBOARD_DEPLOY_ENABLED}" = "1" ]; then
  render_dashboard_systemd_unit || deploy_fail "无法生成受 release 绑定的 Dashboard systemd 快照。"
fi
deploy_as_root mv "${STAGE_DIR}" "${RELEASE_DIR}"
STAGE_DIR=""
RELEASE_ENV_FILE_CREATED=false
if [ "${CANDIDATE_ENV_UPLOADED}" = true ]; then
  # Preserve the root-managed upload until the release and its immutable
  # snapshot are durably published.  A pre-publication failure can then clean
  # the snapshot without losing the caller's candidate configuration.
  deploy_as_root rm -f -- "${CANDIDATE_ENV_FILE}" || \
    deploy_fail "无法清理已发布 release 的候选环境文件。"
  CANDIDATE_ENV_UPLOADED=false
fi
# PREVIOUS_RELEASE was captured only after the complete active environment
# chain was validated above; never rediscover it through an unchecked pointer
# after publication.
prepare_systemd_rollback
prepare_dashboard_systemd_transition

capture_current_service_state
release_transaction_transition cutover_started || deploy_fail "cannot persist cutover intent"
CUTOVER_ACTIVE=true
deploy_log "停止并禁用当前服务，准备原子切换 current"
if ! stop_current_service; then
  rollback_release || true
  CUTOVER_ACTIVE=false
  deploy_fail "无法停止当前服务；已尝试恢复上一版本服务。"
fi
if ! disable_current_service; then
  rollback_release || true
  CUTOVER_ACTIVE=false
  deploy_fail "无法禁用当前服务；已尝试恢复上一版本服务。"
fi
deploy_log "安装新版本的受管 systemd 单元"
if ! install_rendered_systemd_unit; then
  rollback_release || true
  CUTOVER_ACTIVE=false
  deploy_fail "无法安装新版本 systemd 单元；已尝试恢复上一版本服务。"
fi
deploy_log "原子切换 current"
if ! switch_current_release; then
  rollback_release || true
  CUTOVER_ACTIVE=false
  deploy_fail "无法原子切换 current；已尝试恢复上一版本服务。"
fi
release_transaction_transition current_switched || deploy_fail "cannot persist current switch state"
deploy_log "验证活动环境文件指针"
if ! ensure_active_environment_pointer; then
  rollback_release || true
  CUTOVER_ACTIVE=false
  deploy_fail "无法验证或创建活动环境文件指针；已尝试恢复上一版本服务。"
fi
deploy_log "启动新版本服务"
if ! activate_service; then
  deploy_as_root journalctl -u "${SYSTEMD_SERVICE_NAME}" -n 80 --no-pager >&2 || true
  rollback_release || true
  CUTOVER_ACTIVE=false
  deploy_fail "新版本服务启动失败。数据库迁移不会自动回滚，请检查兼容性。"
fi
release_transaction_transition post_start_healthy || deploy_fail "cannot persist post-start health state"
if ! enable_managed_unit "${SYSTEMD_SERVICE_NAME}"; then
  rollback_release || true
  CUTOVER_ACTIVE=false
  deploy_fail "新版本服务无法设为开机启动；已尝试恢复上一版本服务。"
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
trap - ERR INT TERM
trap - EXIT

deploy_log "版本部署完成"
printf "release=%s\n" "${RELEASE_DIR}"
printf "current=%s\n" "$(readlink -f "${CURRENT_LINK}")"
printf "service=%s.service\n" "${SYSTEMD_SERVICE_NAME}"
printf "mode=%s\n" "${SERVICE_MODE}"
printf "dashboard=%s\n" "${DASHBOARD_DEPLOY_STATUS}"
