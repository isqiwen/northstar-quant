#!/bin/bash -p
#
# This entrypoint is not run from SSH staging.  The fixed root release gate
# invokes it only after it has verified a detached manifest signature and
# extracted this control bundle into a root-owned transaction directory.
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

RELEASE_TRANSACTION_ROOT="${RELEASE_TRANSACTION_ROOT:-/var/lib/northstar/deploy-state/transactions}"
RELEASE_MANIFEST_FILE="${RELEASE_MANIFEST_FILE:-}"
RELEASE_MANIFEST_SIGNATURE_FILE="${RELEASE_MANIFEST_SIGNATURE_FILE:-}"
SETUP_SERVER="${SETUP_SERVER:-0}"
RELEASE_ID="${RELEASE_ID:-}"

transition() {
  local state="$1"
  /usr/bin/python3 -I "${SCRIPT_DIR}/release_transaction_hook.py" \
    --root "${RELEASE_TRANSACTION_ROOT}" transition "${RELEASE_ID}" "${state}" >/dev/null
}

current_transaction_state() {
  /usr/bin/python3 -I "${SCRIPT_DIR}/release_transaction_hook.py" \
    --root "${RELEASE_TRANSACTION_ROOT}" inspect "${RELEASE_ID}" |
    /usr/bin/python3 -I -c 'import json, sys; print(json.load(sys.stdin)["last_state"] or "")'
}

record_failure() {
  local state
  state="$(current_transaction_state || true)"
  case "${state}" in
    received|verified|staging_started|staged)
      transition failed || true
      ;;
    migration_started|migrated|candidate_healthy|cutover_started|current_switched|post_start_healthy)
      # Forward schema migration or a cutover may have occurred. Never restart
      # the prior service automatically and never attempt a database rollback.
      transition recovery_required || true
      ;;
  esac
}

finish_gate() {
  local status="$?"
  trap - EXIT INT TERM
  if [ "${status}" -ne 0 ]; then
    record_failure || true
  fi
  exit "${status}"
}

prepare_environment_candidate() {
  local metadata
  if [ -z "${CANDIDATE_ENV_FILE}" ]; then
    return 0
  fi
  if ! require_root_owned_regular_file "${CANDIDATE_ENV_FILE}" 600; then
    deploy_fail "release gate environment candidate is not root-owned"
  fi
  chown root:"${SERVICE_USER}" -- "${CANDIDATE_ENV_FILE}"
  chmod 0640 -- "${CANDIDATE_ENV_FILE}"
  metadata="$(stat -c '%U:%G:%a:%F' -- "${CANDIDATE_ENV_FILE}" 2>/dev/null || true)"
  [ "${metadata}" = "root:${SERVICE_USER}:640:regular file" ] || \
    deploy_fail "release gate environment candidate ownership is invalid"
}

require_root_owned_regular_file() {
  local path="$1"
  local expected_mode="$2"
  local metadata
  metadata="$(stat -c '%u:%g:%a:%F' -- "${path}" 2>/dev/null || true)"
  [ "${metadata}" = "0:0:${expected_mode}:regular file" ]
}

trap finish_gate EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "${EUID}" -ne 0 ]; then
  deploy_fail "release gate entrypoint requires root"
fi
if [ "${SETUP_SERVER}" != "0" ] && [ "${SETUP_SERVER}" != "1" ]; then
  deploy_fail "SETUP_SERVER must be 0 or 1"
fi
deploy_assert_safe_name "RELEASE_ID" "${RELEASE_ID}"
if ! require_root_owned_regular_file "${RELEASE_MANIFEST_FILE}" 600 ||
  ! require_root_owned_regular_file "${RELEASE_MANIFEST_SIGNATURE_FILE}" 600; then
  deploy_fail "release gate manifest evidence is not root-owned and immutable"
fi

transition staging_started
if [ "${SETUP_SERVER}" = "1" ]; then
  /bin/bash -p "${SCRIPT_DIR}/install-runtime.sh"
fi
prepare_environment_candidate

# The root gate has written the artifact and optional environment candidate to
# their fixed FHS locations.  This process has no path originating from the
# deployment SSH account and invokes only sibling files from the signed,
# root-owned control bundle.
if ! env -i \
  PATH="${PATH}" \
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
  UV_VERSION="${UV_VERSION}" \
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
  RELEASE_TRANSACTION_ROOT="${RELEASE_TRANSACTION_ROOT}" \
  RELEASE_MANIFEST_FILE="${RELEASE_MANIFEST_FILE}" \
  RELEASE_MANIFEST_SIGNATURE_FILE="${RELEASE_MANIFEST_SIGNATURE_FILE}" \
  RELEASE_RUNNER_MODE="1" \
  /bin/bash -p "${SCRIPT_DIR}/install-release.sh"; then
  exit 1
fi

transition promoted
printf 'release_gate_result={"release_id":"%s","state":"promoted"}\n' "${RELEASE_ID}"
