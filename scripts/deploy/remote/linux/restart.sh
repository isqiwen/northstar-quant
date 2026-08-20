#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

SERVICE_NAME="${SYSTEMD_SERVICE_NAME:-northstar-quant}"
deploy_assert_safe_name "SYSTEMD_SERVICE_NAME" "${SERVICE_NAME}"
remote_linux_require_confirmation "CONFIRM_SERVICE_RESTART" "YES"

deploy_need_cmd systemctl
deploy_log "重启受控 systemd 服务：${SERVICE_NAME}.service"
deploy_as_root systemctl restart "${SERVICE_NAME}.service"
deploy_as_root systemctl is-active --quiet "${SERVICE_NAME}.service"
