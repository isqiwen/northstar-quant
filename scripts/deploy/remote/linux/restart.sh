#!/bin/bash -p
set -euo pipefail

# Set the command search path before deriving SCRIPT_DIR or loading any
# helper.  A direct privileged invocation must not inherit a PATH that can
# shadow dirname, uname, systemctl, or an identity-check dependency.
PATH="/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
readonly PATH
unset BASH_ENV ENV CDPATH

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"
# shellcheck disable=SC1091
source "${DEPLOY_DIR}/lib/service_identity.sh"
# shellcheck disable=SC1091
source "${DEPLOY_DIR}/lib/release_environment.sh"

# A restart is a privileged state change, not a generic systemctl wrapper.
# Do not let a caller turn an otherwise-safe service-looking value into a
# request to restart sshd, Docker, PostgreSQL, or another host service.
readonly CANONICAL_APP_NAME="northstar-quant"
readonly CANONICAL_SERVICE_USER="northstar"
readonly CANONICAL_SERVICE_HOME="/var/lib/northstar"
readonly CANONICAL_SERVICE_NAME="northstar-quant"
readonly CANONICAL_APP_ROOT="/opt/northstar"
readonly CANONICAL_RELEASES_DIR="/opt/northstar/releases"
readonly CANONICAL_CURRENT_LINK="/opt/northstar/current"
readonly CANONICAL_CONFIG_DIR="/etc/northstar"
readonly CANONICAL_ENV_FILE="/etc/northstar/northstar-quant.env"
readonly CANONICAL_ENV_RELEASES_DIR="/etc/northstar/releases"
readonly CANONICAL_SYSTEMD_UNIT_FILE="/etc/systemd/system/northstar-quant.service"

assert_canonical_restart_setting() {
  local setting_name="$1"
  local expected_value="$2"
  local actual_value

  if [[ -v ${setting_name} ]]; then
    actual_value="${!setting_name}"
    if [ "${actual_value}" != "${expected_value}" ]; then
      deploy_fail "远端重启只允许受管 Northstar 身份；${setting_name} 不能覆盖为其他值。"
    fi
  fi
}

assert_root_controlled_restart_directory() {
  local path="$1"
  local metadata
  local mode
  local group_mode
  local other_mode

  if ! deploy_as_root test -d "${path}" || \
    deploy_as_root test -L "${path}" || \
    ! metadata="$(deploy_as_root stat -c '%u:%a' -- "${path}")"; then
    return 1
  fi
  [ "${metadata%%:*}" = "0" ] || return 1
  mode="${metadata#*:}"
  group_mode="${mode: -2:1}"
  other_mode="${mode: -1}"
  case "${group_mode}:${other_mode}" in
    2:*|3:*|6:*|7:*|*:2|*:3|*:6|*:7)
      return 1
      ;;
  esac
}

assert_root_controlled_restart_file() {
  local path="$1"
  local metadata
  local mode
  local group_mode
  local other_mode

  if ! deploy_as_root test -f "${path}" || \
    deploy_as_root test -L "${path}" || \
    ! metadata="$(deploy_as_root stat -c '%u:%a' -- "${path}")"; then
    return 1
  fi
  [ "${metadata%%:*}" = "0" ] || return 1
  mode="${metadata#*:}"
  group_mode="${mode: -2:1}"
  other_mode="${mode: -1}"
  case "${group_mode}:${other_mode}" in
    2:*|3:*|6:*|7:*|*:2|*:3|*:6|*:7)
      return 1
      ;;
  esac
}

assert_root_owned_current_restart_link() {
  local owner

  if ! deploy_as_root test -L "${CANONICAL_CURRENT_LINK}" || \
    ! owner="$(deploy_as_root stat -c '%u' -- "${CANONICAL_CURRENT_LINK}")"; then
    return 1
  fi
  [ "${owner}" = "0" ]
}

assert_no_foreign_restart_unit_fragment() {
  local unit_root
  local candidate_unit_file
  local load_state
  local fragment_path

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
    candidate_unit_file="${unit_root}/${CANONICAL_SERVICE_NAME}.service"
    if deploy_as_root test -e "${candidate_unit_file}" || \
      deploy_as_root test -L "${candidate_unit_file}"; then
      return 1
    fi
  done

  load_state="$(deploy_as_root systemctl show -p LoadState --value "${CANONICAL_SERVICE_NAME}.service")" || return 1
  [ "${load_state}" = "loaded" ] || return 1
  fragment_path="$(deploy_as_root systemctl show -p FragmentPath --value "${CANONICAL_SERVICE_NAME}.service")" || return 1
  [ "${fragment_path}" = "${CANONICAL_SYSTEMD_UNIT_FILE}" ]
}

assert_no_restart_unit_dropins() {
  local dropin_dir
  local dropin_paths

  for dropin_dir in \
    "/etc/systemd/system.control/${CANONICAL_SERVICE_NAME}.service.d" \
    "/run/systemd/system.control/${CANONICAL_SERVICE_NAME}.service.d" \
    "/run/systemd/transient/${CANONICAL_SERVICE_NAME}.service.d" \
    "/run/systemd/generator.early/${CANONICAL_SERVICE_NAME}.service.d" \
    "/etc/systemd/system/${CANONICAL_SERVICE_NAME}.service.d" \
    "/run/systemd/system/${CANONICAL_SERVICE_NAME}.service.d" \
    "/run/systemd/generator/${CANONICAL_SERVICE_NAME}.service.d" \
    "/etc/systemd/system.attached/${CANONICAL_SERVICE_NAME}.service.d" \
    "/run/systemd/system.attached/${CANONICAL_SERVICE_NAME}.service.d" \
    "/usr/local/lib/systemd/system/${CANONICAL_SERVICE_NAME}.service.d" \
    "/usr/local/share/systemd/system/${CANONICAL_SERVICE_NAME}.service.d" \
    "/usr/lib/systemd/system/${CANONICAL_SERVICE_NAME}.service.d" \
    "/usr/share/systemd/system/${CANONICAL_SERVICE_NAME}.service.d" \
    "/lib/systemd/system/${CANONICAL_SERVICE_NAME}.service.d" \
    "/run/systemd/generator.late/${CANONICAL_SERVICE_NAME}.service.d"; do
    if deploy_as_root test -e "${dropin_dir}" || deploy_as_root test -L "${dropin_dir}"; then
      return 1
    fi
  done

  dropin_paths="$(deploy_as_root systemctl show -p DropInPaths --value "${CANONICAL_SERVICE_NAME}.service")" || return 1
  [ -z "${dropin_paths}" ]
}

assert_managed_restart_environment_snapshot() {
  local release_id="$1"
  local active_snapshot

  if ! active_snapshot="$(deploy_resolve_managed_active_environment_snapshot)"; then
    return 1
  fi
  [ "${active_snapshot}" = "${CANONICAL_ENV_RELEASES_DIR}/${release_id}.env" ]
}

assert_managed_restart_target() {
  local current_target
  local release_id
  local release_dir
  local snapshot_file
  local installed_hash
  local snapshot_hash

  if ! assert_root_controlled_restart_directory "/opt" || \
    ! assert_root_controlled_restart_directory "${CANONICAL_APP_ROOT}" || \
    ! assert_root_controlled_restart_directory "${CANONICAL_RELEASES_DIR}" || \
    ! assert_root_owned_current_restart_link || \
    ! current_target="$(deploy_as_root readlink -- "${CANONICAL_CURRENT_LINK}")"; then
    return 1
  fi
  case "${current_target}" in
    "${CANONICAL_RELEASES_DIR}/"*)
      ;;
    *)
      return 1
      ;;
  esac
  release_id="${current_target#"${CANONICAL_RELEASES_DIR}/"}"
  case "${release_id}" in
    ""|.*|*/*|*[!A-Za-z0-9._-]*)
      return 1
      ;;
  esac
  release_dir="${CANONICAL_RELEASES_DIR}/${release_id}"
  snapshot_file="${release_dir}/.northstar/systemd/${CANONICAL_SERVICE_NAME}.service"

  if [ "${current_target}" != "${release_dir}" ] || \
    ! assert_root_controlled_restart_directory "${release_dir}" || \
    ! assert_root_controlled_restart_directory "${release_dir}/.northstar" || \
    ! assert_root_controlled_restart_directory "${release_dir}/.northstar/systemd" || \
    ! assert_root_controlled_restart_file "${snapshot_file}" || \
    ! assert_root_controlled_restart_file "${CANONICAL_SYSTEMD_UNIT_FILE}" || \
    ! assert_managed_restart_environment_snapshot "${release_id}" || \
    ! assert_no_restart_unit_dropins || \
    ! assert_no_foreign_restart_unit_fragment; then
    return 1
  fi

  installed_hash="$(deploy_as_root sha256sum "${CANONICAL_SYSTEMD_UNIT_FILE}" | awk '{print $1}')" || return 1
  snapshot_hash="$(deploy_as_root sha256sum "${snapshot_file}" | awk '{print $1}')" || return 1
  [ "${installed_hash}" = "${snapshot_hash}" ]
}

assert_canonical_restart_setting "APP_NAME" "${CANONICAL_APP_NAME}"
assert_canonical_restart_setting "SERVICE_USER" "${CANONICAL_SERVICE_USER}"
assert_canonical_restart_setting "SERVICE_HOME" "${CANONICAL_SERVICE_HOME}"
assert_canonical_restart_setting "SYSTEMD_SERVICE_NAME" "${CANONICAL_SERVICE_NAME}"
assert_canonical_restart_setting "SERVICE_NAME" "${CANONICAL_SERVICE_NAME}"

APP_NAME="${CANONICAL_APP_NAME}"
SERVICE_USER="${CANONICAL_SERVICE_USER}"
SERVICE_HOME="${CANONICAL_SERVICE_HOME}"
SYSTEMD_SERVICE_NAME="${CANONICAL_SERVICE_NAME}"
APP_ROOT="${CANONICAL_APP_ROOT}"
RELEASES_DIR="${CANONICAL_RELEASES_DIR}"
CURRENT_LINK="${CANONICAL_CURRENT_LINK}"
CONFIG_DIR="${CANONICAL_CONFIG_DIR}"
ENV_FILE="${CANONICAL_ENV_FILE}"
ENV_RELEASES_DIR="${CANONICAL_ENV_RELEASES_DIR}"
readonly APP_NAME SERVICE_USER SERVICE_HOME SYSTEMD_SERVICE_NAME
readonly APP_ROOT RELEASES_DIR CURRENT_LINK CONFIG_DIR ENV_FILE ENV_RELEASES_DIR

remote_linux_require_confirmation "CONFIRM_SERVICE_RESTART" "YES"

for required_command in awk getent id readlink sha256sum stat systemctl; do
  deploy_need_cmd "${required_command}"
done
if ! deploy_assert_canonical_service_identity; then
  deploy_fail "受管 Northstar 服务账户不满足专属身份、主组、home 或 nologin 约束；拒绝重启。"
fi
if ! assert_managed_restart_target; then
  deploy_fail "Northstar systemd 单元未与 current release 的受管快照完全匹配；拒绝重启未知服务。"
fi

deploy_log "重启受管 systemd 服务：${CANONICAL_SERVICE_NAME}.service"
deploy_as_root systemctl restart "${CANONICAL_SERVICE_NAME}.service"
deploy_as_root systemctl is-active --quiet "${CANONICAL_SERVICE_NAME}.service"
