wait_for_postgres() {
  local attempt

  for attempt in $(seq 1 60); do
    if docker compose exec -T postgres pg_isready -U northstar -d postgres >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  docker compose logs --tail=80 postgres >&2 || true
  fail "PostgreSQL 未在预期时间内就绪。"
}

ensure_database() {
  local database_name="$1"

  if docker compose exec -T postgres psql -U northstar -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${database_name}'" | grep -q "1"; then
    return 0
  fi

  docker compose exec -T postgres createdb -U northstar "${database_name}"
}

start_postgres() {
  docker compose up -d postgres
  wait_for_postgres
  ensure_database "northstar"
  ensure_database "northstar_test"
}
