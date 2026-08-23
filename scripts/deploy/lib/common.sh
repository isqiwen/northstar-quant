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
    ""|.*|*[!A-Za-z0-9._-]*)
      deploy_fail "${label} 只能包含 A-Z、a-z、0-9、点、下划线和连字符。"
      ;;
  esac
}

deploy_as_root() {
  if [ "${EUID}" -eq 0 ]; then
    "$@"
  else
    sudo -n -- "$@"
  fi
}

deploy_as_user() {
  local user="$1"
  shift

  if [ "${EUID}" -eq 0 ]; then
    runuser -u "${user}" -- "$@"
  else
    sudo -n -u "${user}" -H -- "$@"
  fi
}

deploy_validate_environment_file() {
  local env_file="$1"

  if [ ! -f "${env_file}" ] || [ -L "${env_file}" ]; then
    printf '环境文件必须是普通文件：%s\n' "${env_file}" >&2
    return 1
  fi

  awk '
    {
      sub(/\r$/, "")
    }
    /^[[:space:]]*$/ || /^[[:space:]]*#/ {
      next
    }
    !/^[A-Za-z_][A-Za-z0-9_]*=/ {
      printf "环境文件第 %d 行不是受支持的 KEY=VALUE 赋值。\n", NR > "/dev/stderr"
      invalid = 1
      next
    }
    {
      separator = index($0, "=")
      key = substr($0, 1, separator - 1)
      value = substr($0, separator + 1)
      if (seen[key]++) {
        printf "环境文件第 %d 行重复定义键：%s\n", NR, key > "/dev/stderr"
        invalid = 1
      }
      if (value ~ /\\$/) {
        printf "环境文件第 %d 行不允许续行语法：%s\n", NR, key > "/dev/stderr"
        invalid = 1
      }
      first = substr(value, 1, 1)
      last = substr(value, length(value), 1)
      if ((first == "\"" || first == "\047") && last != first) {
        printf "环境文件第 %d 行引号不闭合：%s\n", NR, key > "/dev/stderr"
        invalid = 1
      } else if (first != "\"" && first != "\047" && value ~ /[[:space:]]/) {
        printf "环境文件第 %d 行未加引号的值包含空白：%s\n", NR, key > "/dev/stderr"
        invalid = 1
      }
    }
    END {
      exit invalid
    }
  ' "${env_file}"
}

deploy_read_env_value() {
  local env_file="$1"
  local key="$2"

  deploy_validate_environment_file "${env_file}" || return 1
  awk -v key="${key}" '
    {
      sub(/\r$/, "")
    }
    $0 ~ "^" key "=" {
      value = substr($0, length(key) + 2)
      first = substr(value, 1, 1)
      last = substr(value, length(value), 1)
      if ((first == "\"" && last == "\"") || (first == "\047" && last == "\047")) {
        value = substr(value, 2, length(value) - 2)
      }
      print value
    }
  ' "${env_file}"
}
