#!/usr/bin/env bash

# Fixed production layout paths are security boundaries.  Do not use
# `realpath` here: it would resolve a pre-existing symbolic link before this
# helper had a chance to reject it.  The path grammar is deliberately small
# and textual so every component can be inspected as it is traversed.
deploy_validate_privileged_directory_path() {
  local directory_path="$1"

  case "${directory_path}" in
    /)
      return 0
      ;;
    /*)
      ;;
    *)
      printf 'privileged directory path must be absolute: %s\n' "${directory_path}" >&2
      return 1
      ;;
  esac
  case "${directory_path}" in
    */)
      printf 'privileged directory path must not have a trailing slash: %s\n' \
        "${directory_path}" >&2
      return 1
      ;;
  esac
  case "${directory_path}" in
    *"//"*|*/./*|*/../*|*/.|*/..)
      printf 'privileged directory path is not canonical: %s\n' "${directory_path}" >&2
      return 1
      ;;
  esac
  case "${directory_path}" in
    *[!A-Za-z0-9/._-]*)
      printf 'privileged directory path contains unsupported characters: %s\n' \
        "${directory_path}" >&2
      return 1
      ;;
  esac
}

deploy_assert_root_controlled_directory() {
  local directory_path="$1"
  local metadata
  local mode
  local group_mode
  local other_mode

  # The canonical-path grammar permits only absolute paths, so no component
  # can be parsed as a test option.
  if ! deploy_as_root test -d "${directory_path}" ||
    deploy_as_root test -L "${directory_path}" ||
    ! metadata="$(deploy_as_root stat -c '%u:%a' -- "${directory_path}")"; then
    printf 'privileged directory must be a non-symbolic-link directory: %s\n' \
      "${directory_path}" >&2
    return 1
  fi
  case "${metadata}" in
    0:[0-7][0-7][0-7])
      ;;
    *)
      printf 'privileged directory must be root-owned with a normal octal mode: %s\n' \
        "${directory_path}" >&2
      return 1
      ;;
  esac
  mode="${metadata#*:}"
  group_mode="${mode: -2:1}"
  other_mode="${mode: -1}"
  case "${group_mode}:${other_mode}" in
    2:*|3:*|6:*|7:*|*:2|*:3|*:6|*:7)
      printf 'privileged directory must not be group/other writable: %s\n' \
        "${directory_path}" >&2
      return 1
      ;;
  esac
}

# Validate every component from / through the requested directory.  A caller
# may create a child only after its direct parent has passed this check.  That
# parent is root-owned and not group/other writable, so an unprivileged actor
# cannot replace a checked component between validation and mkdir; mkdir is
# still deliberately non-recursive and fails closed on a racing creator.
deploy_assert_root_controlled_directory_chain() {
  local directory_path="$1"
  local remaining_path
  local component
  local current_path=""

  deploy_validate_privileged_directory_path "${directory_path}" || return 1
  deploy_assert_root_controlled_directory / || return 1
  if [ "${directory_path}" = / ]; then
    return 0
  fi

  remaining_path="${directory_path#/}"
  while [ -n "${remaining_path}" ]; do
    component="${remaining_path%%/*}"
    current_path="${current_path}/${component}"
    deploy_assert_root_controlled_directory "${current_path}" || return 1
    if [ "${remaining_path}" = "${component}" ]; then
      remaining_path=""
    else
      remaining_path="${remaining_path#*/}"
    fi
  done
}

deploy_assert_root_controlled_directory_layout() {
  local directory_path="$1"
  local expected_group="$2"
  local expected_mode="$3"
  local metadata

  if ! deploy_assert_root_controlled_directory "${directory_path}" ||
    ! metadata="$(deploy_as_root stat -c '%u:%G:%a' -- "${directory_path}")"; then
    return 1
  fi
  if [ "${metadata}" != "0:${expected_group}:${expected_mode}" ]; then
    printf 'privileged directory has unexpected owner, group, or mode: %s\n' \
      "${directory_path}" >&2
    return 1
  fi
}

# Prepare a fixed privileged directory without ever repairing an existing
# object.  Intermediate components may be created one at a time only after
# their own parent has been verified.  New intermediates are root:root 0755;
# callers must prepare layout roots from parent to child when a child needs a
# more restrictive final group or mode.
deploy_prepare_root_controlled_directory() {
  local directory_path="$1"
  local expected_group="$2"
  local expected_mode="$3"
  local remaining_path
  local component
  local current_path="/"
  local next_path
  local is_target
  local creation_group
  local creation_mode

  deploy_validate_privileged_directory_path "${directory_path}" || return 1
  if [ "${directory_path}" = / ]; then
    printf 'refusing to prepare filesystem root as a privileged layout target\n' >&2
    return 1
  fi
  case "${expected_group}" in
    ""|*[!A-Za-z0-9_-]*)
      printf 'privileged directory group is not a safe group name: %s\n' "${expected_group}" >&2
      return 1
      ;;
  esac
  case "${expected_mode}" in
    700|750|755)
      ;;
    *)
      printf 'privileged directory mode is not an approved layout mode: %s\n' \
        "${expected_mode}" >&2
      return 1
      ;;
  esac

  deploy_assert_root_controlled_directory / || return 1
  remaining_path="${directory_path#/}"
  while [ -n "${remaining_path}" ]; do
    component="${remaining_path%%/*}"
    if [ "${current_path}" = / ]; then
      next_path="/${component}"
    else
      next_path="${current_path}/${component}"
    fi
    if [ "${remaining_path}" = "${component}" ]; then
      is_target=true
      remaining_path=""
      creation_group="${expected_group}"
      creation_mode="${expected_mode}"
    else
      is_target=false
      remaining_path="${remaining_path#*/}"
      creation_group=root
      creation_mode=755
    fi

    if deploy_as_root test -e "${next_path}" || deploy_as_root test -L "${next_path}"; then
      if ! deploy_assert_root_controlled_directory "${next_path}"; then
        return 1
      fi
      if [ "${is_target}" = true ]; then
        # Existing privileged paths are validation-only.  A safe-but-wrong
        # layout is still unknown state and must be repaired explicitly by an
        # operator, not silently by a deployment.
        deploy_assert_root_controlled_directory_layout \
          "${next_path}" "${expected_group}" "${expected_mode}" || return 1
      fi
    else
      # current_path was either / or the preceding checked/just-created
      # component.  mkdir without -p therefore cannot traverse a mutable
      # parent; a racing creator makes this fail rather than get repaired.
      if ! deploy_as_root mkdir -m "${creation_mode}" -- "${next_path}" ||
        ! deploy_as_root chown "root:${creation_group}" -- "${next_path}" ||
        ! deploy_as_root chmod "${creation_mode}" -- "${next_path}"; then
        printf 'unable to create privileged directory safely: %s\n' "${next_path}" >&2
        return 1
      fi
      if [ "${is_target}" = true ]; then
        deploy_assert_root_controlled_directory_layout \
          "${next_path}" "${expected_group}" "${expected_mode}" || return 1
      else
        deploy_assert_root_controlled_directory "${next_path}" || return 1
      fi
    fi
    current_path="${next_path}"
  done
}
