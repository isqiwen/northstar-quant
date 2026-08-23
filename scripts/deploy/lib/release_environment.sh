#!/usr/bin/env bash

# Release-bound environment configuration is a security boundary.  A normal
# candidate may be a root-managed file under CONFIG_DIR, while an already
# active configuration must follow the exact immutable chain:
#
# ENV_FILE -> CURRENT_LINK/.env -> ENV_RELEASES_DIR/<release-id>.env
#
# These helpers deliberately use deploy_as_root even when sourced by a root
# release installer, so provision-time and final-consumer validation share the
# same checks rather than drifting apart.

if ! declare -F deploy_assert_root_controlled_directory_chain >/dev/null 2>&1; then
  _DEPLOY_RELEASE_ENVIRONMENT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck disable=SC1091
  source "${_DEPLOY_RELEASE_ENVIRONMENT_LIB_DIR}/privileged_paths.sh"
  unset _DEPLOY_RELEASE_ENVIRONMENT_LIB_DIR
fi

deploy_service_group_id() {
  deploy_as_root id -g "${SERVICE_USER}"
}

deploy_assert_root_owned_directory() {
  local directory_path="$1"
  local expected_group_id="$2"
  local expected_mode="$3"
  local metadata

  if ! deploy_assert_root_controlled_directory_chain "${directory_path}" ||
    ! deploy_as_root test -d "${directory_path}" ||
    deploy_as_root test -L "${directory_path}"; then
    return 1
  fi
  metadata="$(deploy_as_root stat -c '%u:%g:%a' -- "${directory_path}")" || return 1
  [ "${metadata}" = "0:${expected_group_id}:${expected_mode}" ]
}

deploy_assert_managed_environment_file() {
  local environment_file="$1"
  local parent_directory
  local service_group_id
  local metadata

  parent_directory="${environment_file%/*}"
  if [ -z "${parent_directory}" ] || [ "${parent_directory}" = "${environment_file}" ]; then
    return 1
  fi
  service_group_id="$(deploy_service_group_id)" || return 1
  if ! deploy_assert_root_owned_directory "${parent_directory}" "${service_group_id}" 750 ||
    ! deploy_as_root test -f "${environment_file}" ||
    deploy_as_root test -L "${environment_file}"; then
    return 1
  fi
  metadata="$(deploy_as_root stat -c '%u:%g:%a:%h' -- "${environment_file}")" || return 1
  [ "${metadata}" = "0:${service_group_id}:640:1" ]
}

deploy_resolve_managed_active_environment_snapshot() {
  local service_group_id
  local active_pointer_target
  local current_release_target
  local current_release_id
  local expected_release_directory
  local expected_snapshot
  local active_snapshot_target
  local resolved_snapshot

  service_group_id="$(deploy_service_group_id)" || return 1
  if ! deploy_assert_root_owned_directory "${APP_ROOT}" 0 755 ||
    ! deploy_assert_root_owned_directory "${RELEASES_DIR}" 0 755 ||
    ! deploy_assert_root_owned_directory "${CONFIG_DIR}" "${service_group_id}" 750 ||
    ! deploy_assert_root_owned_directory "${ENV_RELEASES_DIR}" "${service_group_id}" 750 ||
    ! deploy_as_root test -L "${ENV_FILE}" ||
    ! active_pointer_target="$(deploy_as_root readlink -- "${ENV_FILE}")" ||
    [ "${active_pointer_target}" != "${CURRENT_LINK}/.env" ] ||
    ! deploy_as_root test -L "${CURRENT_LINK}" ||
    ! current_release_target="$(deploy_as_root readlink -- "${CURRENT_LINK}")"; then
    return 1
  fi

  case "${current_release_target}" in
    "${RELEASES_DIR}/"*)
      current_release_id="${current_release_target#"${RELEASES_DIR}/"}"
      ;;
    *)
      return 1
      ;;
  esac
  deploy_assert_safe_name "active release identifier" "${current_release_id}" || return 1
  expected_release_directory="${RELEASES_DIR}/${current_release_id}"
  if [ "${current_release_target}" != "${expected_release_directory}" ] ||
    ! deploy_assert_root_owned_directory "${expected_release_directory}" 0 755; then
    return 1
  fi

  expected_snapshot="${ENV_RELEASES_DIR}/${current_release_id}.env"
  if ! deploy_as_root test -L "${CURRENT_LINK}/.env" ||
    ! active_snapshot_target="$(deploy_as_root readlink -- "${CURRENT_LINK}/.env")" ||
    [ "${active_snapshot_target}" != "${expected_snapshot}" ] ||
    ! resolved_snapshot="$(deploy_as_root readlink -f -- "${ENV_FILE}")" ||
    [ "${resolved_snapshot}" != "${expected_snapshot}" ] ||
    ! deploy_assert_managed_environment_file "${resolved_snapshot}"; then
    return 1
  fi

  printf '%s\n' "${resolved_snapshot}"
}
