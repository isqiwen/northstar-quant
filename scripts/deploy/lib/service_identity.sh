#!/usr/bin/env bash

# The release installer must re-check this boundary on every deployment.  An
# account that has gained supplementary groups or a different primary group
# can turn otherwise correct root:group 0640 configuration into an exposure.

deploy_assert_canonical_service_identity() {
  local account_record
  local account_name
  local account_password
  local account_uid
  local account_gid
  local account_gecos
  local account_home
  local account_shell
  local group_record
  local group_name
  local group_password
  local group_gid
  local group_members
  local foreign_primary_member

  account_record="$(getent passwd "${SERVICE_USER}")" || return 1
  IFS=: read -r \
    account_name \
    account_password \
    account_uid \
    account_gid \
    account_gecos \
    account_home \
    account_shell <<< "${account_record}"
  [ "${account_name}" = "${SERVICE_USER}" ] || return 1
  [ "${account_uid}" != "0" ] || return 1
  [ "${account_home}" = "${SERVICE_HOME}" ] || return 1
  [ "${account_shell}" = "/usr/sbin/nologin" ] || return 1
  [ "$(id -gn "${SERVICE_USER}")" = "${SERVICE_USER}" ] || return 1
  [ "$(id -Gn "${SERVICE_USER}")" = "${SERVICE_USER}" ] || return 1

  group_record="$(getent group "${SERVICE_USER}")" || return 1
  IFS=: read -r group_name group_password group_gid group_members <<< "${group_record}"
  [ "${group_name}" = "${SERVICE_USER}" ] || return 1
  [ "${group_gid}" = "${account_gid}" ] || return 1
  [ -z "${group_members}" ] || return 1
  foreign_primary_member="$(
    getent passwd | awk -F: -v group_gid="${group_gid}" -v service_user="${SERVICE_USER}" \
      '$1 != service_user && $4 == group_gid { print $1; exit }'
  )"
  [ -z "${foreign_primary_member}" ]
}
