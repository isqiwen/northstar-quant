#!/bin/bash -p
# This is a root-only entrypoint.  Do not inherit command lookup or startup
# hooks from an untrusted deployment shell.
unset BASH_ENV ENV CDPATH
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
set -euo pipefail

# 这个脚本只在远端以 root 身份执行。ntfy 是独立于 Northstar release 的持久服务：
# 普通应用发布可以更新 Compose/Caddy 配置，但绝不改写 server.yml 中的身份、ACL 或令牌。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/common.sh"
source "${SCRIPT_DIR}/lib.sh"

APP_ENV_FILE="${APP_ENV_FILE:-}"
NTFY_DEPLOY_ENABLED="${NTFY_DEPLOY_ENABLED:-0}"
UPLOAD_NTFY_BOOTSTRAP="${UPLOAD_NTFY_BOOTSTRAP:-0}"
NTFY_BOOTSTRAP_PATH="${NTFY_BOOTSTRAP_PATH:-}"
RELEASE_ID="${RELEASE_ID:-}"
DEPLOY_STATE_DIR="/var/lib/northstar/deploy-state"
NTFY_SERVICE_ACCOUNT="northstar-ntfy"
NTFY_PROJECT_NAME="northstar-ntfy"
MANAGED_BOOTSTRAP_CANDIDATE=""

COMPOSE_FILE=""
CADDY_FILE=""
SERVER_FILE=""
COMPOSE_TMP=""
CADDY_TMP=""
SERVER_TMP=""
PREVIOUS_COMPOSE=""
PREVIOUS_CADDY=""
PREVIOUS_SERVER=""
HAD_PREVIOUS_STACK=0

cleanup_temporary_files() {
  rm -f -- "${COMPOSE_TMP:-}" "${CADDY_TMP:-}" "${SERVER_TMP:-}" || true
  rm -f -- "${PREVIOUS_COMPOSE:-}" "${PREVIOUS_CADDY:-}" "${PREVIOUS_SERVER:-}" || true
}

cleanup_bootstrap_file() {
  if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ] && [ -n "${MANAGED_BOOTSTRAP_CANDIDATE}" ]; then
    rm -f -- "${MANAGED_BOOTSTRAP_CANDIDATE}" || true
  fi
}

cleanup_on_exit() {
  local exit_status="$?"

  cleanup_temporary_files
  # An interrupted root apply has an unknown outcome. Preserve the exact
  # verified candidate for later durable recovery; ordinary success/failure
  # remains safe to clean up because this one-time bootstrap has been consumed.
  if [ "${exit_status}" -lt 128 ]; then
    cleanup_bootstrap_file
  fi
  return "${exit_status}"
}

trap cleanup_on_exit EXIT

ntfy_require_root() {
  if [ "${EUID}" -ne 0 ]; then
    deploy_fail "私有 ntfy 部署必须以 root 身份运行。"
  fi
}

ntfy_assert_managed_bootstrap_candidate() {
  local expected_candidate
  local state_metadata
  local candidate_metadata

  deploy_assert_safe_name "RELEASE_ID" "${RELEASE_ID}"
  expected_candidate="${DEPLOY_STATE_DIR}/.ntfy-bootstrap.${RELEASE_ID}.candidate.env"
  if [ "${NTFY_BOOTSTRAP_PATH}" != "${expected_candidate}" ]; then
    deploy_fail "NTFY bootstrap 必须是当前 release 的受管候选文件。"
  fi

  # These are fixed Linux production paths. Check type and metadata without
  # dereferencing a link before root reads the one-time secret candidate.
  if [ ! -d "${DEPLOY_STATE_DIR}" ] || [ -L "${DEPLOY_STATE_DIR}" ]; then
    deploy_fail "NTFY bootstrap 受管状态目录必须是普通非链接目录。"
  fi
  state_metadata="$(stat -c '%u:%g:%a' -- "${DEPLOY_STATE_DIR}" 2>/dev/null || true)"
  if [ "${state_metadata}" != "0:0:700" ]; then
    deploy_fail "NTFY bootstrap 受管状态目录必须是 root:root 0700 的普通目录。"
  fi
  if [ ! -f "${NTFY_BOOTSTRAP_PATH}" ] || [ -L "${NTFY_BOOTSTRAP_PATH}" ]; then
    deploy_fail "NTFY bootstrap 候选文件必须是普通非链接文件。"
  fi
  candidate_metadata="$(stat -c '%u:%g:%a' -- "${NTFY_BOOTSTRAP_PATH}" 2>/dev/null || true)"
  if [ "${candidate_metadata}" != "0:0:600" ]; then
    deploy_fail "NTFY bootstrap 候选文件必须是 root:root 0600 的普通非链接文件。"
  fi

  MANAGED_BOOTSTRAP_CANDIDATE="${expected_candidate}"
}

ntfy_assert_docker_ready() {
  deploy_need_cmd docker
  deploy_need_cmd curl
  if ! docker info >/dev/null 2>&1; then
    deploy_fail "远端 Docker daemon 不可用。请先由服务器管理员安装并启动 Docker Engine。"
  fi
  if ! docker compose version >/dev/null 2>&1; then
    deploy_fail "远端缺少 Docker Compose v2（docker compose）。请先由服务器管理员安装。"
  fi
  if id -nG "${SERVICE_USER}" | tr ' ' '\n' | grep -Fxq docker; then
    deploy_fail "服务用户 ${SERVICE_USER} 不得属于 docker 组；请移除该高权限成员关系后重试。"
  fi
}

ntfy_ensure_service_account() {
  deploy_need_cmd awk
  deploy_need_cmd getent

  if ! id "${NTFY_SERVICE_ACCOUNT}" >/dev/null 2>&1; then
    adduser \
      --system \
      --group \
      --home /nonexistent \
      --shell /usr/sbin/nologin \
      "${NTFY_SERVICE_ACCOUNT}"
  fi

  if ! ntfy_assert_canonical_service_account; then
    deploy_fail "既有 ntfy 服务账户不符合专用非 root、nologin、私有主组与无补充组约束。"
  fi

  NTFY_SERVICE_UID="$(id -u "${NTFY_SERVICE_ACCOUNT}")"
  NTFY_SERVICE_GID="$(id -g "${NTFY_SERVICE_ACCOUNT}")"
}

ntfy_assert_canonical_service_account() {
  local account_record
  local account_name
  local ignored_password
  local account_uid
  local account_gid
  local ignored_gecos
  local account_home
  local account_shell
  local group_record
  local group_name
  local ignored_group_password
  local group_gid
  local group_members
  local foreign_primary_member

  account_record="$(getent passwd "${NTFY_SERVICE_ACCOUNT}")" || return 1
  IFS=: read -r \
    account_name \
    ignored_password \
    account_uid \
    account_gid \
    ignored_gecos \
    account_home \
    account_shell <<< "${account_record}"
  [ "${account_name}" = "${NTFY_SERVICE_ACCOUNT}" ] || return 1
  [ "${account_uid}" != "0" ] || return 1
  [ "${account_gid}" != "0" ] || return 1
  [ "${account_home}" = "/nonexistent" ] || return 1
  [ "${account_shell}" = "/usr/sbin/nologin" ] || return 1
  [ "$(id -gn "${NTFY_SERVICE_ACCOUNT}")" = "${NTFY_SERVICE_ACCOUNT}" ] || return 1
  [ "$(id -Gn "${NTFY_SERVICE_ACCOUNT}")" = "${NTFY_SERVICE_ACCOUNT}" ] || return 1

  group_record="$(getent group "${NTFY_SERVICE_ACCOUNT}")" || return 1
  IFS=: read -r \
    group_name \
    ignored_group_password \
    group_gid \
    group_members <<< "${group_record}"
  [ "${group_name}" = "${NTFY_SERVICE_ACCOUNT}" ] || return 1
  [ "${group_gid}" = "${account_gid}" ] || return 1
  [ -z "${group_members}" ] || return 1
  if ! foreign_primary_member="$(
    getent passwd | awk -F: -v group_gid="${group_gid}" -v service_account="${NTFY_SERVICE_ACCOUNT}" \
      '$1 != service_account && $4 == group_gid { print $1; exit }'
  )"; then
    return 1
  fi
  [ -z "${foreign_primary_member}" ]
}

ntfy_directory_matches_contract() {
  local directory_path="$1"
  local expected_uid="$2"
  local expected_gid="$3"
  local expected_mode="$4"
  local directory_metadata

  if [ ! -d "${directory_path}" ] || [ -L "${directory_path}" ]; then
    return 1
  fi
  directory_metadata="$(stat -c '%u:%g:%a' -- "${directory_path}" 2>/dev/null || true)"
  [ "${directory_metadata}" = "${expected_uid}:${expected_gid}:${expected_mode}" ]
}

ntfy_ensure_managed_directory() {
  local directory_path="$1"
  local owner_name="$2"
  local group_name="$3"
  local expected_uid="$4"
  local expected_gid="$5"
  local expected_mode="$6"

  # mkdir succeeds only for a new final path. Do not chown/chmod after a
  # failed mkdir: that object existed before this invocation and must already
  # satisfy the contract, otherwise an administrator must remediate it.
  if ! mkdir -m "${expected_mode}" -- "${directory_path}" 2>/dev/null; then
    if ntfy_directory_matches_contract \
      "${directory_path}" \
      "${expected_uid}" \
      "${expected_gid}" \
      "${expected_mode}"; then
      return
    fi
    deploy_fail "拒绝修改未受管的 ntfy 目录：${directory_path}"
  fi
  if ! chown "${owner_name}:${group_name}" -- "${directory_path}" ||
    ! chmod "${expected_mode}" -- "${directory_path}"; then
    deploy_fail "无法设置新建 ntfy 受管目录的所有权或权限：${directory_path}"
  fi
  if ! ntfy_directory_matches_contract \
    "${directory_path}" \
    "${expected_uid}" \
    "${expected_gid}" \
    "${expected_mode}"; then
    deploy_fail "新建 ntfy 目录未满足受管所有权与权限约束：${directory_path}"
  fi
}

ntfy_ensure_directories() {
  deploy_need_cmd chown
  deploy_need_cmd chmod
  deploy_need_cmd mkdir
  deploy_need_cmd stat
  ntfy_require_canonical_directories

  ntfy_ensure_managed_directory \
    "${NTFY_CONFIG_DIR}" root "${NTFY_SERVICE_ACCOUNT}" 0 "${NTFY_SERVICE_GID}" 750
  ntfy_ensure_managed_directory \
    "${NTFY_DATA_DIR}" root root 0 0 750
  ntfy_ensure_managed_directory \
    "${NTFY_DATA_DIR}/ntfy" \
    "${NTFY_SERVICE_ACCOUNT}" \
    "${NTFY_SERVICE_ACCOUNT}" \
    "${NTFY_SERVICE_UID}" \
    "${NTFY_SERVICE_GID}" \
    750
  ntfy_ensure_managed_directory \
    "${NTFY_DATA_DIR}/caddy" root root 0 0 700
  ntfy_ensure_managed_directory \
    "${NTFY_DATA_DIR}/caddy/data" root root 0 0 700
  ntfy_ensure_managed_directory \
    "${NTFY_DATA_DIR}/caddy/config" root root 0 0 700
}

ntfy_compose() {
  docker compose \
    --project-name "${NTFY_PROJECT_NAME}" \
    --project-directory "${NTFY_CONFIG_DIR}" \
    -f "${COMPOSE_FILE}" \
    "$@"
}

ntfy_sed_escape_replacement() {
  printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

ntfy_render_compose() {
  sed \
    -e "s|@NTFY_CADDY_IMAGE@|$(ntfy_sed_escape_replacement "${NTFY_CADDY_IMAGE}")|g" \
    -e "s|@NTFY_IMAGE@|$(ntfy_sed_escape_replacement "${NTFY_IMAGE}")|g" \
    -e "s|@NTFY_CONFIG_DIR@|$(ntfy_sed_escape_replacement "${NTFY_CONFIG_DIR}")|g" \
    -e "s|@NTFY_DATA_DIR@|$(ntfy_sed_escape_replacement "${NTFY_DATA_DIR}")|g" \
    -e "s|@NTFY_SERVICE_UID@|${NTFY_SERVICE_UID}|g" \
    -e "s|@NTFY_SERVICE_GID@|${NTFY_SERVICE_GID}|g" \
    "${SCRIPT_DIR}/compose.yaml.in" > "${COMPOSE_TMP}"
}

ntfy_render_caddyfile() {
  sed \
    -e "s|@NTFY_ACME_EMAIL@|$(ntfy_sed_escape_replacement "${NTFY_ACME_EMAIL}")|g" \
    -e "s|@NTFY_PUBLIC_HOST@|$(ntfy_sed_escape_replacement "${NTFY_PUBLIC_HOST}")|g" \
    "${SCRIPT_DIR}/Caddyfile.in" > "${CADDY_TMP}"
}

ntfy_backup_file() {
  local source_file="$1"
  local variable_name="$2"
  local backup_file=""

  if [ -f "${source_file}" ]; then
    backup_file="$(mktemp "${NTFY_CONFIG_DIR}/.$(basename "${source_file}").backup.XXXXXX")"
    cp -a -- "${source_file}" "${backup_file}"
    printf -v "${variable_name}" '%s' "${backup_file}"
  fi
}

ntfy_install_atomic() {
  local source_file="$1"
  local target_file="$2"
  local owner="$3"
  local group="$4"
  local mode="$5"

  chown "${owner}:${group}" "${source_file}"
  chmod "${mode}" "${source_file}"
  mv -Tf -- "${source_file}" "${target_file}"
}

ntfy_assert_existing_server_config() {
  local expected_base_url="https://${NTFY_PUBLIC_HOST}"
  local expected_token_line="  - \"northstar-publisher:${NORTHSTAR_NTFY_TOKEN}:Northstar Quant publisher\""
  local expected_publisher_acl="  - \"northstar-publisher:${NORTHSTAR_NTFY_TOPIC}:write-only\""
  local reader_acl_regex="^  - \"([A-Za-z0-9][A-Za-z0-9_-]{2,63}):${NORTHSTAR_NTFY_TOPIC}:read-only\"$"
  local auth_user_regex='^  - "([A-Za-z0-9][A-Za-z0-9_-]{2,63}):\$2[aby]\$[0-9]{2}\$[^:]+:(admin|user)"$'
  local line
  local auth_section=""
  local reader_username=""
  local index
  local username
  local role
  local -a auth_user_names=()
  local -a auth_user_roles=()
  local base_url_count=0
  local listen_http_count=0
  local cache_file_count=0
  local cache_duration_count=0
  local auth_file_count=0
  local deny_all_count=0
  local behind_proxy_count=0
  local login_enabled_count=0
  local signup_disabled_count=0
  local metrics_disabled_count=0
  local log_level_count=0
  local log_format_count=0
  local auth_users_section_count=0
  local auth_access_section_count=0
  local auth_tokens_section_count=0
  local publisher_acl_count=0
  local reader_acl_count=0
  local publisher_user_count=0
  local reader_user_count=0
  local admin_user_count=0
  local token_count=0
  local auth_access_entries=0
  local auth_token_entries=0
  local unsafe_policy=0

  if [ ! -f "${SERVER_FILE}" ]; then
    deploy_fail "私有 ntfy 尚未初始化；首次启用必须同时设置 UPLOAD_NTFY_BOOTSTRAP=1。"
  fi
  if [ "$(stat -c '%U' "${SERVER_FILE}")" != "root" ] ||
    [ "$(stat -c '%G' "${SERVER_FILE}")" != "${NTFY_SERVICE_ACCOUNT}" ] ||
    [ "$(stat -c '%a' "${SERVER_FILE}")" != "640" ]; then
    deploy_fail "${SERVER_FILE} 的权限必须为 root:${NTFY_SERVICE_ACCOUNT} 0640。"
  fi
  while IFS= read -r line || [ -n "${line}" ]; do
    if [ -z "${line}" ] || [[ "${line}" == \#* ]]; then
      auth_section=""
      continue
    fi

    if [[ "${line}" != [[:space:]]* ]]; then
      auth_section=""
      if [[ "${line}" == base-url:* ]]; then
        if [[ "${line}" == "base-url: \"${expected_base_url}\"" ]]; then
          ((base_url_count += 1))
        else
          unsafe_policy=1
        fi
      elif [[ "${line}" == cache-duration:* ]]; then
        if [[ "${line}" == "cache-duration: \"${NTFY_CACHE_DURATION}\"" ]]; then
          ((cache_duration_count += 1))
        else
          unsafe_policy=1
        fi
      else
        case "${line}" in
          'listen-http: ":80"')
            ((listen_http_count += 1))
            ;;
          'cache-file: "/var/lib/ntfy/cache.db"')
            ((cache_file_count += 1))
            ;;
          'auth-file: "/var/lib/ntfy/auth.db"')
            ((auth_file_count += 1))
            ;;
          'auth-default-access: "deny-all"')
            ((deny_all_count += 1))
            ;;
          'behind-proxy: true')
            ((behind_proxy_count += 1))
            ;;
          'enable-login: true')
            ((login_enabled_count += 1))
            ;;
          'enable-signup: false')
            ((signup_disabled_count += 1))
            ;;
          'enable-metrics: false')
            ((metrics_disabled_count += 1))
            ;;
          'log-level: "info"')
            ((log_level_count += 1))
            ;;
          'log-format: "json"')
            ((log_format_count += 1))
            ;;
          auth-users:)
            auth_section="auth-users"
            ((auth_users_section_count += 1))
            ;;
          auth-access:)
            auth_section="auth-access"
            ((auth_access_section_count += 1))
            ;;
          auth-tokens:)
            auth_section="auth-tokens"
            ((auth_tokens_section_count += 1))
            ;;
          *)
            # 不允许手工保留未受控的 upstream、附件、SMTP、Firebase 或其他服务端选项。
            unsafe_policy=1
            ;;
        esac
      fi
      continue
    fi

    case "${auth_section}" in
      auth-access)
        if [[ "${line}" == '  - '* ]]; then
          ((auth_access_entries += 1))
          if [[ "${line}" == "${expected_publisher_acl}" ]]; then
            ((publisher_acl_count += 1))
          elif [[ "${line}" =~ ${reader_acl_regex} ]]; then
            ((reader_acl_count += 1))
            reader_username="${BASH_REMATCH[1]}"
          else
            unsafe_policy=1
          fi
        else
          unsafe_policy=1
        fi
        ;;
      auth-tokens)
        if [[ "${line}" == '  - '* ]]; then
          ((auth_token_entries += 1))
          if [[ "${line}" == "${expected_token_line}" ]]; then
            ((token_count += 1))
          else
            unsafe_policy=1
          fi
        else
          unsafe_policy=1
        fi
        ;;
      auth-users)
        if [[ "${line}" =~ ${auth_user_regex} ]]; then
          auth_user_names+=("${BASH_REMATCH[1]}")
          auth_user_roles+=("${BASH_REMATCH[2]}")
        else
          unsafe_policy=1
        fi
        ;;
      *)
        unsafe_policy=1
        ;;
    esac
  done < "${SERVER_FILE}"

  for index in "${!auth_user_names[@]}"; do
    username="${auth_user_names[${index}]}"
    role="${auth_user_roles[${index}]}"
    if [ "${username}" = "northstar-publisher" ] && [ "${role}" = "user" ]; then
      ((publisher_user_count += 1))
    elif [ -n "${reader_username}" ] && [ "${username}" = "${reader_username}" ] && [ "${role}" = "user" ]; then
      ((reader_user_count += 1))
    elif [ "${username}" != "northstar-publisher" ] &&
      [ "${username}" != "${reader_username}" ] &&
      [ "${role}" = "admin" ]; then
      ((admin_user_count += 1))
    else
      unsafe_policy=1
    fi
  done

  if [ "${base_url_count}" != "1" ] ||
    [ "${listen_http_count}" != "1" ] ||
    [ "${cache_file_count}" != "1" ] ||
    [ "${cache_duration_count}" != "1" ] ||
    [ "${auth_file_count}" != "1" ] ||
    [ "${deny_all_count}" != "1" ] ||
    [ "${behind_proxy_count}" != "1" ] ||
    [ "${login_enabled_count}" != "1" ] ||
    [ "${signup_disabled_count}" != "1" ] ||
    [ "${metrics_disabled_count}" != "1" ] ||
    [ "${log_level_count}" != "1" ] ||
    [ "${log_format_count}" != "1" ] ||
    [ "${auth_users_section_count}" != "1" ] ||
    [ "${auth_access_section_count}" != "1" ] ||
    [ "${auth_tokens_section_count}" != "1" ] ||
    [ "${auth_access_entries}" != "2" ] ||
    [ "${publisher_acl_count}" != "1" ] ||
    [ "${reader_acl_count}" != "1" ] ||
    [ "${#auth_user_names[@]}" != "3" ] ||
    [ "${publisher_user_count}" != "1" ] ||
    [ "${reader_user_count}" != "1" ] ||
    [ "${admin_user_count}" != "1" ] ||
    [ "${auth_token_entries}" != "1" ] ||
    [ "${token_count}" != "1" ] ||
    [ "${unsafe_policy}" != "0" ]; then
    deploy_fail "既有 ntfy server.yml 偏离受控私有安全基线或与活动配置不一致；请审查后显式执行 UPLOAD_NTFY_BOOTSTRAP=1 进行受控更新。"
  fi
}

ntfy_read_bootstrap_file() {
  local line
  local normalized_line
  local key
  local value
  local seen_admin_username=0
  local seen_admin_password=0
  local seen_reader_username=0
  local seen_reader_password=0

  NTFY_ADMIN_USERNAME=""
  NTFY_ADMIN_PASSWORD=""
  NTFY_READER_USERNAME=""
  NTFY_READER_PASSWORD=""

  if [ "${NTFY_BOOTSTRAP_PATH}" != "${MANAGED_BOOTSTRAP_CANDIDATE}" ]; then
    deploy_fail "NTFY bootstrap 文件未通过受管候选文件验证。"
  fi
  # Recheck immediately before opening the file. This is redundant for the
  # root-only state directory, but keeps the parser fail-closed if its
  # metadata changes between validation and consumption.
  ntfy_assert_managed_bootstrap_candidate
  if [ ! -f "${NTFY_BOOTSTRAP_PATH}" ] || [ -L "${NTFY_BOOTSTRAP_PATH}" ]; then
    deploy_fail "未找到私有 ntfy bootstrap 文件：${NTFY_BOOTSTRAP_PATH}。"
  fi

  while IFS= read -r line || [ -n "${line}" ]; do
    normalized_line="$(printf '%s' "${line}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    case "${normalized_line}" in
      ''|\#*)
        continue
        ;;
      *=*)
        ;;
      *)
        deploy_fail "ntfy bootstrap 文件存在无效行。"
        ;;
    esac

    key="${normalized_line%%=*}"
    value="${normalized_line#*=}"
    key="$(printf '%s' "${key}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    value="$(printf '%s' "${value}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    case "${value}" in
      \"*\")
        value="${value#\"}"
        value="${value%\"}"
        ;;
      \'*\')
        value="${value#\'}"
        value="${value%\'}"
        ;;
    esac

    case "${key}" in
      NTFY_ADMIN_USERNAME)
        if [ "${seen_admin_username}" = "1" ]; then
          deploy_fail "ntfy bootstrap 文件重复定义 NTFY_ADMIN_USERNAME。"
        fi
        NTFY_ADMIN_USERNAME="${value}"
        seen_admin_username=1
        ;;
      NTFY_ADMIN_PASSWORD)
        if [ "${seen_admin_password}" = "1" ]; then
          deploy_fail "ntfy bootstrap 文件重复定义 NTFY_ADMIN_PASSWORD。"
        fi
        NTFY_ADMIN_PASSWORD="${value}"
        seen_admin_password=1
        ;;
      NTFY_READER_USERNAME)
        if [ "${seen_reader_username}" = "1" ]; then
          deploy_fail "ntfy bootstrap 文件重复定义 NTFY_READER_USERNAME。"
        fi
        NTFY_READER_USERNAME="${value}"
        seen_reader_username=1
        ;;
      NTFY_READER_PASSWORD)
        if [ "${seen_reader_password}" = "1" ]; then
          deploy_fail "ntfy bootstrap 文件重复定义 NTFY_READER_PASSWORD。"
        fi
        NTFY_READER_PASSWORD="${value}"
        seen_reader_password=1
        ;;
      *)
        deploy_fail "ntfy bootstrap 文件包含不支持的字段：${key}。"
        ;;
    esac
  done < "${NTFY_BOOTSTRAP_PATH}"

  ntfy_validate_username "NTFY_ADMIN_USERNAME" "${NTFY_ADMIN_USERNAME}"
  ntfy_validate_password "NTFY_ADMIN_PASSWORD" "${NTFY_ADMIN_PASSWORD}"
  ntfy_validate_username "NTFY_READER_USERNAME" "${NTFY_READER_USERNAME}"
  ntfy_validate_password "NTFY_READER_PASSWORD" "${NTFY_READER_PASSWORD}"
  if [ "${NTFY_ADMIN_USERNAME}" = "${NTFY_READER_USERNAME}" ] ||
    [ "${NTFY_ADMIN_USERNAME}" = "northstar-publisher" ] ||
    [ "${NTFY_READER_USERNAME}" = "northstar-publisher" ]; then
    deploy_fail "ntfy admin、reader 与 northstar-publisher 必须使用三个不同的用户名。"
  fi
}

ntfy_hash_password() {
  local password="$1"
  local hash

  hash="$(printf '%s\n%s\n' "${password}" "${password}" | docker run --rm -i "${NTFY_IMAGE}" user hash)"
  if [[ ! "${hash}" =~ ^\$2[aby]\$[0-9]{2}\$.+$ ]]; then
    deploy_fail "无法生成 ntfy bcrypt 密码哈希。"
  fi
  printf '%s' "${hash}"
}

ntfy_render_server_config() {
  local publisher_password
  local admin_password_hash
  local reader_password_hash
  local publisher_password_hash
  local line

  deploy_need_cmd openssl
  ntfy_read_bootstrap_file
  publisher_password="$(openssl rand -base64 48)"
  admin_password_hash="$(ntfy_hash_password "${NTFY_ADMIN_PASSWORD}")"
  reader_password_hash="$(ntfy_hash_password "${NTFY_READER_PASSWORD}")"
  publisher_password_hash="$(ntfy_hash_password "${publisher_password}")"

  # 不使用 sed、awk 或 grep 传递认证材料，避免令牌或密码哈希出现在外部进程参数中。
  while IFS= read -r line || [ -n "${line}" ]; do
    line="${line//@NTFY_PUBLIC_HOST@/${NTFY_PUBLIC_HOST}}"
    line="${line//@NTFY_CACHE_DURATION@/${NTFY_CACHE_DURATION}}"
    line="${line//@NTFY_ADMIN_USERNAME@/${NTFY_ADMIN_USERNAME}}"
    line="${line//@NTFY_ADMIN_PASSWORD_HASH@/${admin_password_hash}}"
    line="${line//@NTFY_READER_USERNAME@/${NTFY_READER_USERNAME}}"
    line="${line//@NTFY_READER_PASSWORD_HASH@/${reader_password_hash}}"
    line="${line//@NTFY_PUBLISHER_PASSWORD_HASH@/${publisher_password_hash}}"
    line="${line//@NORTHSTAR_NTFY_TOPIC@/${NORTHSTAR_NTFY_TOPIC}}"
    line="${line//@NORTHSTAR_NTFY_TOKEN@/${NORTHSTAR_NTFY_TOKEN}}"
    printf '%s\n' "${line}"
  done < "${SCRIPT_DIR}/server.yml.in" > "${SERVER_TMP}"

  unset NTFY_ADMIN_PASSWORD NTFY_READER_PASSWORD publisher_password
}

ntfy_read_application_settings() {
  local alert_mode
  local base_url

  if [ -z "${APP_ENV_FILE}" ] || [ ! -f "${APP_ENV_FILE}" ]; then
    deploy_fail "私有 ntfy 部署需要已安装的 Northstar 活动 .env。"
  fi

  alert_mode="$(deploy_read_env_value "${APP_ENV_FILE}" "NORTHSTAR_ALERT_MODE")"
  alert_mode="$(printf '%s' "${alert_mode}" | tr '[:upper:]' '[:lower:]')"
  base_url="$(deploy_read_env_value "${APP_ENV_FILE}" "NORTHSTAR_NTFY_BASE_URL")"
  NORTHSTAR_NTFY_TOPIC="$(deploy_read_env_value "${APP_ENV_FILE}" "NORTHSTAR_NTFY_TOPIC")"
  NORTHSTAR_NTFY_TOKEN="$(deploy_read_env_value "${APP_ENV_FILE}" "NORTHSTAR_NTFY_TOKEN")"
  base_url="${base_url%/}"

  if [ "${alert_mode}" != "ntfy" ]; then
    deploy_fail "启用私有 ntfy 部署时，活动 .env 必须设置 NORTHSTAR_ALERT_MODE=ntfy。"
  fi
  if [ "${base_url}" != "https://${NTFY_PUBLIC_HOST}" ]; then
    deploy_fail "NORTHSTAR_NTFY_BASE_URL 必须精确等于 https://${NTFY_PUBLIC_HOST}。"
  fi
  ntfy_validate_topic "${NORTHSTAR_NTFY_TOPIC}"
  ntfy_validate_token "${NORTHSTAR_NTFY_TOKEN}"
}

ntfy_wait_for_internal_health() {
  local attempt
  local health_response

  for attempt in $(seq 1 15); do
    if health_response="$(ntfy_compose exec -T ntfy wget -q --tries=1 -O - http://127.0.0.1/v1/health 2>/dev/null)" &&
      printf '%s' "${health_response}" | grep -Eq '"healthy"[[:space:]]*:[[:space:]]*true'; then
      return 0
    fi
    sleep 2
  done
  return 1
}

ntfy_wait_for_tls_health() {
  local attempt
  local health_response
  local health_url="https://${NTFY_PUBLIC_HOST}/v1/health"

  for attempt in $(seq 1 24); do
    if health_response="$(curl \
      --fail \
      --silent \
      --show-error \
      --noproxy '*' \
      --proto '=https' \
      --tlsv1.2 \
      --connect-timeout 5 \
      --max-time 15 \
      --resolve "${NTFY_PUBLIC_HOST}:443:127.0.0.1" \
      "${health_url}" 2>/dev/null)" &&
      printf '%s' "${health_response}" | grep -Eq '"healthy"[[:space:]]*:[[:space:]]*true'; then
      return 0
    fi
    sleep 5
  done
  return 1
}

ntfy_restore_previous_stack() {
  ntfy_compose down >/dev/null 2>&1 || true

  if [ -n "${PREVIOUS_COMPOSE}" ]; then
    mv -Tf -- "${PREVIOUS_COMPOSE}" "${COMPOSE_FILE}"
    PREVIOUS_COMPOSE=""
  else
    rm -f -- "${COMPOSE_FILE}"
  fi
  if [ -n "${PREVIOUS_CADDY}" ]; then
    mv -Tf -- "${PREVIOUS_CADDY}" "${CADDY_FILE}"
    PREVIOUS_CADDY=""
  else
    rm -f -- "${CADDY_FILE}"
  fi
  if [ -n "${PREVIOUS_SERVER}" ]; then
    mv -Tf -- "${PREVIOUS_SERVER}" "${SERVER_FILE}"
    PREVIOUS_SERVER=""
  elif [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then
    rm -f -- "${SERVER_FILE}"
  fi

  if [ "${HAD_PREVIOUS_STACK}" = "1" ] && [ -f "${COMPOSE_FILE}" ]; then
    ntfy_compose up -d >/dev/null 2>&1 || true
  fi
}

ntfy_apply_stack() {
  if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then
    ntfy_compose up -d --force-recreate ntfy caddy
  else
    ntfy_compose up -d
    ntfy_compose restart caddy
  fi
}

deploy_require_linux_x86_64
ntfy_require_root
ntfy_validate_deployment_config
deploy_assert_bool "UPLOAD_NTFY_BOOTSTRAP" "${UPLOAD_NTFY_BOOTSTRAP}"

if [ "${NTFY_DEPLOY_ENABLED}" = "0" ]; then
  if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then
    deploy_fail "UPLOAD_NTFY_BOOTSTRAP=1 时必须同时设置 NTFY_DEPLOY_ENABLED=1。"
  fi
  exit 0
fi

if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then
  if [ -z "${NTFY_BOOTSTRAP_PATH}" ]; then
    deploy_fail "UPLOAD_NTFY_BOOTSTRAP=1 时必须提供远端 NTFY_BOOTSTRAP_PATH。"
  fi
  ntfy_assert_managed_bootstrap_candidate
fi

ntfy_assert_docker_ready
ntfy_ensure_service_account
ntfy_ensure_directories
ntfy_read_application_settings

COMPOSE_FILE="${NTFY_CONFIG_DIR}/compose.yaml"
CADDY_FILE="${NTFY_CONFIG_DIR}/Caddyfile"
SERVER_FILE="${NTFY_CONFIG_DIR}/server.yml"
if [ -f "${COMPOSE_FILE}" ]; then
  HAD_PREVIOUS_STACK=1
fi

if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "0" ]; then
  ntfy_assert_existing_server_config
fi

COMPOSE_TMP="$(mktemp "${NTFY_CONFIG_DIR}/.compose.yaml.XXXXXX")"
CADDY_TMP="$(mktemp "${NTFY_CONFIG_DIR}/.Caddyfile.XXXXXX")"
ntfy_render_compose
ntfy_render_caddyfile
if ! docker compose -f "${COMPOSE_TMP}" config -q >/dev/null; then
  deploy_fail "生成的私有 ntfy Compose 配置无效。"
fi

if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then
  SERVER_TMP="$(mktemp "${NTFY_CONFIG_DIR}/.server.yml.XXXXXX")"
  ntfy_render_server_config
fi

ntfy_backup_file "${COMPOSE_FILE}" PREVIOUS_COMPOSE
ntfy_backup_file "${CADDY_FILE}" PREVIOUS_CADDY
if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then
  ntfy_backup_file "${SERVER_FILE}" PREVIOUS_SERVER
fi

ntfy_install_atomic "${COMPOSE_TMP}" "${COMPOSE_FILE}" root root 0644
COMPOSE_TMP=""
ntfy_install_atomic "${CADDY_TMP}" "${CADDY_FILE}" root root 0644
CADDY_TMP=""
if [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then
  ntfy_install_atomic \
    "${SERVER_TMP}" \
    "${SERVER_FILE}" \
    root \
    "${NTFY_SERVICE_ACCOUNT}" \
    0640
  SERVER_TMP=""
fi

deploy_log "启动私有 ntfy 与 Caddy"
if ! ntfy_apply_stack; then
  ntfy_restore_previous_stack
  deploy_fail "私有 ntfy 服务启动失败；已尝试恢复上一份服务配置。"
fi
if ! ntfy_wait_for_internal_health; then
  ntfy_restore_previous_stack
  deploy_fail "私有 ntfy 内部 /v1/health 未通过；已尝试恢复上一份服务配置。"
fi
if ! ntfy_wait_for_tls_health; then
  ntfy_restore_previous_stack
  deploy_fail "经 Caddy 的 HTTPS /v1/health 未通过。请检查 DNS、80/443 防火墙、ACME 证书与 Caddy 日志；已尝试恢复上一份服务配置。"
fi

deploy_log "私有 ntfy 部署完成"
