deploy_log() {
  printf "\n==> %s\n" "$1"
}

deploy_fail() {
  printf "\n部署错误：%s\n" "$1" >&2
  exit 1
}

deploy_need_cmd() {
  local command_name="$1"

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    deploy_fail "缺少命令：${command_name}"
  fi
}

deploy_assert_bool() {
  local name="$1"
  local value="$2"

  case "${value}" in
    0|1)
      ;;
    *)
      deploy_fail "${name} 只能是 0 或 1，当前值为：${value}"
      ;;
  esac
}

deploy_assert_safe_name() {
  local label="$1"
  local value="$2"

  case "${value}" in
    ""|*[!A-Za-z0-9._-]*)
      deploy_fail "${label} 只能包含 A-Z、a-z、0-9、点、下划线和连字符。"
      ;;
  esac
}

deploy_shell_quote() {
  local value="$1"

  value="${value//\'/\'\\\'\'}"
  printf "'%s'" "${value}"
}

deploy_as_root() {
  if [ "${EUID}" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

deploy_as_user() {
  local user="$1"
  shift

  if [ "${EUID}" -eq 0 ]; then
    runuser -u "${user}" -- "$@"
  else
    sudo -u "${user}" -H "$@"
  fi
}

deploy_read_env_value() {
  local env_file="$1"
  local key="$2"

  if [ ! -f "${env_file}" ]; then
    return 0
  fi

  awk -v key="${key}" '
    $0 ~ "^[[:space:]]*" key "=" {
      sub("^[[:space:]]*" key "=", "")
      value = $0
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      first = substr(value, 1, 1)
      last = substr(value, length(value), 1)
      if ((first == "\"" && last == "\"") || (first == "\047" && last == "\047")) {
        value = substr(value, 2, length(value) - 2)
      }
      print value
      exit
    }
  ' "${env_file}"
}
