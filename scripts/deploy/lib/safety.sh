deploy_validate_production_env() {
  local env_file="$1"
  local service_mode="$2"
  local confirm_live_deploy="$3"
  local broker
  local database_url
  local environment
  local live_enabled

  if ! deploy_validate_environment_file "${env_file}"; then
    deploy_fail "生产环境文件格式无效、包含重复键或不受支持的赋值。"
  fi

  environment="$(deploy_read_env_value "${env_file}" "NORTHSTAR_ENV")"
  if [ "${environment}" != "production" ]; then
    deploy_fail "服务器环境必须设置 NORTHSTAR_ENV=production。"
  fi

  database_url="$(deploy_read_env_value "${env_file}" "NORTHSTAR_DATABASE_URL")"
  case "${database_url}" in
    postgresql+psycopg://*)
      ;;
    *)
      deploy_fail "NORTHSTAR_DATABASE_URL 必须是有效的 PostgreSQL psycopg URL。"
      ;;
  esac
  case "${database_url}" in
    *CHANGE_ME*|*本地密码*)
      deploy_fail "NORTHSTAR_DATABASE_URL 仍包含示例占位符。"
      ;;
  esac

  broker="$(deploy_read_env_value "${env_file}" "NORTHSTAR_BROKER")"
  live_enabled="$(deploy_read_env_value "${env_file}" "NORTHSTAR_LIVE_TRADING_ENABLED")"
  broker="$(printf "%s" "${broker:-paper}" | tr '[:upper:]' '[:lower:]')"
  live_enabled="$(printf "%s" "${live_enabled:-false}" | tr '[:upper:]' '[:lower:]')"

  if [ "${service_mode}" != "scheduler" ]; then
    if [ "${broker}" != "paper" ] || [ "${live_enabled}" != "false" ]; then
      deploy_fail "health 模式要求 broker=paper 且 live trading=false。"
    fi
    return 0
  fi

  if [ "${broker}" = "paper" ]; then
    if [ "${live_enabled}" != "false" ]; then
      deploy_fail "paper 调度器要求 NORTHSTAR_LIVE_TRADING_ENABLED=false。"
    fi
    return 0
  fi

  if [ "${broker}" != "ctp" ] || [ "${live_enabled}" != "true" ]; then
    deploy_fail "非 paper 调度器要求 broker=ctp 且 live trading=true。"
  fi
  if [ "${confirm_live_deploy}" != "YES" ]; then
    deploy_fail "检测到真实交易调度器。必须显式设置 CONFIRM_LIVE_DEPLOY=YES。"
  fi
}
