read_env_value() {
  local key="$1"

  if [ ! -f "${ENV_FILE}" ]; then
    return 0
  fi

  awk -v key="${key}" '
    $0 ~ "^[[:space:]]*" key "=" {
      sub("^[[:space:]]*" key "=", "")
      print
      exit
    }
  ' "${ENV_FILE}"
}

strip_quotes() {
  local value="$1"

  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf "%s" "${value}"
}

set_env_value() {
  local key="$1"
  local value="$2"

  # 通过 stdin 传递值，避免密码或数据库 URL 出现在 awk/python 子进程的命令行参数中。
  # setup_dev.sh 会先完成 schema 同步，因此这里只允许更新已声明字段。
  printf '%s=%s\n' "${key}" "${value}" |
    uv run --no-sync python "${ENV_SCHEMA_SYNC_SCRIPT:-${ROOT_DIR}/scripts/dev/sync_env_schema.py}" \
      --active "${ENV_FILE}" \
      --set-stdin
}

generate_dev_password() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 18
    return 0
  fi

  date "+northstar_dev_%Y%m%d%H%M%S"
}

configure_dev_env() {
  local postgres_password
  local postgres_port

  if [ ! -f "${ENV_FILE}" ]; then
    log "创建本地 .env..."
    cp "${ROOT_DIR}/.env.example" "${ENV_FILE}"
  fi

  postgres_password="$(strip_quotes "$(read_env_value "POSTGRES_PASSWORD")")"
  if [ -z "${postgres_password}" ]; then
    postgres_password="$(generate_dev_password)"
    set_env_value "POSTGRES_PASSWORD" "${postgres_password}"
    log "已生成本地开发数据库密码并写入 .env。"
  else
    log "复用 .env 中已有 POSTGRES_PASSWORD。"
  fi

  case "${postgres_password}" in
    *[!A-Za-z0-9._~-]*)
      fail "POSTGRES_PASSWORD 包含需要 URL 编码的字符。开发脚本只接受 A-Z、a-z、0-9、._~-。"
      ;;
  esac

  postgres_port="$(strip_quotes "$(read_env_value "POSTGRES_PORT")")"
  if [ -z "${postgres_port}" ]; then
    postgres_port="5432"
    set_env_value "POSTGRES_PORT" "${postgres_port}"
  fi

  case "${postgres_port}" in
    *[!0-9]*)
      fail "POSTGRES_PORT 必须是数字端口。"
      ;;
  esac

  set_env_value "NORTHSTAR_DATABASE_URL" \
    "postgresql+psycopg://northstar:${postgres_password}@127.0.0.1:${postgres_port}/northstar"
  set_env_value "NORTHSTAR_TEST_DATABASE_URL" \
    "postgresql+psycopg://northstar:${postgres_password}@127.0.0.1:${postgres_port}/northstar_test"
  set_env_value "NORTHSTAR_ENV" "dev"
  set_env_value "NORTHSTAR_BROKER" "paper"
  set_env_value "NORTHSTAR_LIVE_TRADING_ENABLED" "false"
  set_env_value "NORTHSTAR_KILL_SWITCH_ENABLED" "false"
}
