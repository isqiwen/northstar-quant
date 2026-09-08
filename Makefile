.DEFAULT_GOAL := help
.PHONY: help install up down verify test

help:
	@echo 'make up       Start independent Live, three application Webs and PostgreSQL'
	@echo 'make down     Stop containers, keeping data'
	@echo 'make verify   Verify source and behavior (NORTHSTAR_TEST_DATABASE_URL required)'

install:
	uv sync --project backend --locked

up:
	NORTHSTAR_GIT_REVISION="$$(uv run --project backend python -c 'from northstar_quant import code_revision; print(code_revision())')" docker compose up --build -d

down:
	docker compose down

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
