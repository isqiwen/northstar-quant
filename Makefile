.DEFAULT_GOAL := help
.PHONY: help install up-data up-research up-live up-database down-data down-research down-live down-database ps-data ps-research ps-live ps-database backup-database verify test

# Python 执行器默认读取所属应用的 .env；ENV_FILE 可覆盖。
OPERATE = python3 scripts/operations/compose.py

help:
	@echo 'make up-data / up-research / up-live       独立构建并启动应用'
	@echo 'make down-data / down-research / down-live 停止对应应用，保留数据'
	@echo 'make ps-data / ps-research / ps-live       查看对应应用状态'
	@echo 'make up-database / down-database           在 core 管理 Data Hub 独立数据库'
	@echo 'make verify                              验证源码与行为（需要专用测试数据库）'

install:
	uv sync --project backend --locked

up-database:
	$(OPERATE) deploy database $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

down-database:
	$(OPERATE) stop database $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

ps-database:
	$(OPERATE) status database $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

up-data:
	$(OPERATE) deploy data-hub $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

down-data:
	$(OPERATE) stop data-hub $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

ps-data:
	$(OPERATE) status data-hub $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

up-research:
	$(OPERATE) deploy research $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

down-research:
	$(OPERATE) stop research $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

ps-research:
	$(OPERATE) status research $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

up-live:
	$(OPERATE) deploy live $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

down-live:
	$(OPERATE) stop live $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

ps-live:
	$(OPERATE) status live $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

backup-database:
	$(OPERATE) backup database $(if $(ENV_FILE),--env-file "$(ENV_FILE)")

test:
	@test -n "$$NORTHSTAR_TEST_DATABASE_URL" || (echo 'NORTHSTAR_TEST_DATABASE_URL must name disposable northstar_quant_test' >&2; exit 2)
	uv run --project backend pytest backend/tests

verify:
	uv sync --project backend --locked
	npm --prefix frontend ci
	uv run --project backend python scripts/protocol/generate_api.py --check
	npm --prefix frontend run check
	npm --prefix frontend run build
	npm --prefix frontend test
	uv lock --project backend --check
	uv run --project backend ruff check --config backend/pyproject.toml backend/src backend/tests backend/scripts scripts
	uv run --project backend ruff format --check --config backend/pyproject.toml backend/src backend/tests backend/scripts scripts
	uv run --project backend mypy --config-file backend/pyproject.toml backend/src
	$(MAKE) test
