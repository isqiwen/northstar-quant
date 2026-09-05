.DEFAULT_GOAL := help
.PHONY: help install up down verify test

help:
	@echo 'make up       Start the personal web application and PostgreSQL'
	@echo 'make down     Stop containers, keeping data'
	@echo 'make verify   Verify source and behavior (NORTHSTAR_TEST_DATABASE_URL required)'

install:
	uv sync --locked

up:
	docker compose up --build -d

down:
	docker compose down

test:
	@test -n "$$NORTHSTAR_TEST_DATABASE_URL" || (echo 'NORTHSTAR_TEST_DATABASE_URL must name disposable northstar_quant_test' >&2; exit 2)
	uv run pytest

verify:
	uv sync --locked
	uv lock --check
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts
	uv run mypy src
	$(MAKE) test
