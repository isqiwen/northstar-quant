.DEFAULT_GOAL := help
.PHONY: help install up-data up-research up-live up-storage down-data down-research down-live down-storage ps-data ps-research ps-live ps-storage verify test

# Optional private configuration: make up-live ENV_FILE=/absolute/private/live.env
COMPOSE = docker compose $(if $(ENV_FILE),--env-file "$(ENV_FILE)")
BUILD_ENV = NORTHSTAR_GIT_REVISION="$$(uv run --project backend python -c 'from northstar_quant import code_revision; print(code_revision())')"

help:
	@echo 'make up-data / up-research / up-live       独立构建并启动应用'
	@echo 'make down-data / down-research / down-live 停止对应应用，保留数据'
	@echo 'make ps-data / ps-research / ps-live       查看对应应用状态'
	@echo 'make up-storage / down-storage           管理 Data/Research 共用存储'
	@echo 'make verify                              验证源码与行为（需要专用测试数据库）'

install:
	uv sync --project backend --locked

up-storage:
	$(BUILD_ENV) $(COMPOSE) -f deploy/storage/compose.yaml build initialize
	$(COMPOSE) -f deploy/storage/compose.yaml up -d --wait --wait-timeout 180 postgres
	$(COMPOSE) -f deploy/storage/compose.yaml run --rm initialize

up-data: up-storage
	$(BUILD_ENV) $(COMPOSE) -f deploy/data_hub/compose.yaml up --build -d --wait --wait-timeout 180

up-research: up-storage
	$(BUILD_ENV) $(COMPOSE) -f deploy/research/compose.yaml up --build -d --wait --wait-timeout 180

up-live:
	$(BUILD_ENV) $(COMPOSE) -f deploy/live/compose.yaml up --build -d --wait --wait-timeout 180

down-data:
	$(COMPOSE) -f deploy/data_hub/compose.yaml down

ps-data:
	$(COMPOSE) -f deploy/data_hub/compose.yaml ps

down-research:
	$(COMPOSE) -f deploy/research/compose.yaml down

ps-research:
	$(COMPOSE) -f deploy/research/compose.yaml ps

down-live:
	$(COMPOSE) -f deploy/live/compose.yaml down

ps-live:
	$(COMPOSE) -f deploy/live/compose.yaml ps

down-storage:
	$(COMPOSE) -f deploy/storage/compose.yaml down

ps-storage:
	$(COMPOSE) -f deploy/storage/compose.yaml ps

test:
	@test -n "$$NORTHSTAR_TEST_DATABASE_URL" || (echo 'NORTHSTAR_TEST_DATABASE_URL must name disposable northstar_quant_test' >&2; exit 2)
	uv run --project backend pytest backend/tests

verify:
	uv sync --project backend --locked
	npm --prefix frontend ci
	uv run --project backend python scripts/generate_api.py --check
	npm --prefix frontend run check
	npm --prefix frontend run build
	npm --prefix frontend test
	uv lock --project backend --check
	uv run --project backend ruff check --config backend/pyproject.toml backend/src backend/tests backend/scripts scripts
	uv run --project backend ruff format --check --config backend/pyproject.toml backend/src backend/tests backend/scripts scripts
	uv run --project backend mypy --config-file backend/pyproject.toml backend/src
	$(MAKE) test
