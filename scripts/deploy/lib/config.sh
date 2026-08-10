_deploy_assign_config_value() {
  local key="$1"
  local value="$2"

  case "${key}" in
    DEPLOY_HOST|APP_NAME|SERVICE_USER|SERVICE_HOME|SYSTEMD_SERVICE_NAME|\
      SERVICE_MODE|PYTHON_VERSION|KEEP_RELEASES|REMOTE_TMP|\
      RUNTIME_STORAGE_DIR|RUNTIME_DOWNLOADS_DIR|RUNTIME_REPORTS_DIR|\
      RUNTIME_LOG_DIR|RUNTIME_CACHE_DIR|RUNTIME_MATPLOTLIB_DIR)
      ;;
    *)
      deploy_fail "部署配置包含不支持的字段：${key}"
      ;;
  esac

  if eval "[ \"\${${key}+x}\" != x ]"; then
    printf -v "${key}" "%s" "${value}"
  fi
}

deploy_load_config() {
  local config_file="$1"
  local line
  local normalized_line
  local key
  local value

  if [ ! -f "${config_file}" ]; then
    deploy_fail "未找到部署配置：${config_file}。请先执行 cp deploy.env.example deploy.env。"
  fi

  while IFS= read -r line || [ -n "${line}" ]; do
    normalized_line="$(
      printf "%s" "${line}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
    )"
    case "${normalized_line}" in
      ""|\#*)
        continue
        ;;
      *=*)
        ;;
      *)
        deploy_fail "部署配置存在无效行：${line}"
        ;;
    esac

    key="${normalized_line%%=*}"
    value="${normalized_line#*=}"
    key="$(printf "%s" "${key}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    value="$(printf "%s" "${value}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

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

    _deploy_assign_config_value "${key}" "${value}"
  done < "${config_file}"
}
