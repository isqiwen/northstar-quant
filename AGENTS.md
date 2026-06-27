# Northstar Quant Agent Rules

This repository is a personal quantitative research and trading tool. When an AI agent works in this repo, follow these rules before creating, moving, renaming, or editing code.

## Project Intent

- Treat this as a real-money-adjacent system, even when the current broker mode is `paper`.
- Prefer boring, auditable engineering over clever abstractions.
- Keep research, backtesting, paper trading, and live trading behavior clearly separated.
- Never make a change that can silently increase live trading risk.

## Language

- Write user-facing documentation, log messages, CLI help text, and comments primarily in Chinese.
- Keep Python identifiers, module names, class names, and config keys in English.
- Use concise, practical explanations. Avoid marketing-style text.

## Repository Layout

Current main layout:

```text
alembic/                  Database migration scripts
configs/                  App, profile, data, strategy, and execution configs
docs/                     Architecture and operating notes
scripts/                  Development and demo scripts
src/northstar_quant/      Application package
templates/                Report templates
tests/                    Test suite
```

Core package boundaries:

- `config`: runtime settings and trading profile loading.
- `data`: market data download, validation, storage, and manifests.
- `strategies`: strategy implementations and canonical strategy pipeline.
- `portfolio`: portfolio construction and target combination.
- `risk`: global, strategy, and pre-trade risk checks.
- `backtest`: research-grade and event-style backtesting.
- `execution`: execution planning, order routing, broker adapters, reconciliation.
- `live`: live orchestration, scheduler, preflight, order polling, run health.
- `db`: SQLAlchemy models, sessions, repositories.
- `monitoring`: dashboard, health checks, alerts.
- `reporting`: report generation, PDF rendering, and email sending.

Keep new code in the narrowest existing module that owns the behavior. Do not create a new subsystem when an existing boundary fits.

## Safety Rules

- Never write passwords, API tokens, IBKR credentials, webhook secrets, SMTP passwords, private keys, or recovery codes into tracked files.
- Do not commit `.env`, local databases, downloaded market data, generated reports, cache directories, or broker state files.
- Keep `.env.example` safe by default. Example configs must not enable real-money trading.
- `NORTHSTAR_BROKER=paper` is the default safe mode.
- `NORTHSTAR_LIVE_TRADING_ENABLED` must default to `false`.
- `NORTHSTAR_KILL_SWITCH_ENABLED` must default to `false`, but code must honor it immediately when it is set to `true`.
- Do not run commands that can place real broker orders unless the user explicitly asks for live execution and confirms the target profile, broker, and account.
- Do not weaken preflight, kill-switch, order-routing, or pre-trade checks to make tests pass. Fix the model or tests instead.
- When adding a new live-trading pathway, ensure it passes through the same safety gates as `run_live_once`.

## Configuration Rules

- Runtime settings live in `src/northstar_quant/config/settings.py`.
- Environment variables use the `NORTHSTAR_` prefix.
- Trading profiles live in `configs/profiles/*.yaml`.
- Strategy configuration belongs in profile `strategies` blocks or `configs/strategy`, not hardcoded in execution code.
- Market-specific assumptions such as lot size, minimum trade value, calendar, trading currency, or data frequency should be explicit in profile/config/risk policy.
- If a config option affects live trading, document the safe default in `.env.example`.

## Trading Logic Rules

- Execution plans are not broker orders. Keep this distinction clear in naming and code.
- Pre-trade checks must be the last defense before broker submission.
- When adding order logic, account for:
  - minimum trade notional;
  - maximum order notional;
  - quantity limits and lot-size rules;
  - invalid or missing reference prices;
  - open orders and partial fills;
  - available cash or buying power when available;
  - profile lifecycle role and production eligibility.
- For China-market logic, be careful with board, lot, price-limit, suspension, and T+1 assumptions. Do not silently apply US-market behavior to CN profiles.
- Prefer fail-closed behavior for live trading. If required data is missing, block trading and emit an explicit reason.

## Database and Migrations

- Schema changes require an Alembic migration under `alembic/versions`.
- Keep ORM model changes, repository changes, migration scripts, and tests aligned.
- Repository helpers should not hide large behavioral changes. If a function commits internally, be aware that it affects transaction composition.
- Add repository tests for new persistence behavior.

## Testing and Quality Gates

Use `uv` for project commands.

Before finishing ordinary code changes, run:

```bash
uv run pytest
uv run ruff check .
```

For focused changes, run the relevant test files first, then the full suite before finalizing if the change touches shared execution, live trading, database, or config behavior.

`mypy` is useful but is not currently a clean mandatory gate for this repository. If working on type-heavy code, run:

```bash
uv run mypy src/northstar_quant
```

Report whether it passes or fails. Do not claim the codebase is type-clean unless this command passes.

## Development Discipline

- Inspect current git status before editing. The worktree may already contain user changes.
- Do not revert unrelated user changes.
- Keep changes focused and small.
- Add or update tests for changed behavior.
- Preserve existing public CLI commands unless the user asks for a breaking change.
- Prefer structured data models and explicit config over ad hoc dictionaries when the behavior is durable.
- Avoid broad `except Exception` in live, execution, broker, and persistence paths unless the exception is logged and the resulting behavior is intentionally safe.

## Command Discipline

Safe commands:

```bash
uv run pytest
uv run ruff check .
uv run northstar health
uv run northstar data profiles
uv run northstar live preflight --profile cn_etf_daily
uv run northstar live preview-rebalance --profile cn_etf_daily
```

Commands requiring explicit user confirmation when `NORTHSTAR_BROKER` is not `paper`:

```bash
uv run northstar live run
uv run northstar live scheduler
uv run northstar live cancel-stale
uv run northstar live poll
uv run northstar live sync
```

Do not start long-running schedulers or dashboards unless the user asks for them.

## Generated Files

Generated or local-runtime files should generally remain untracked:

- `.env`
- `.venv/`
- `.pytest_cache/`
- `.ruff_cache/`
- `.mypy_cache/`
- `storage/`
- `reports/`
- local SQLite databases
- downloaded market data
- broker state snapshots

If a generated artifact needs to be tracked, explain why before adding it.

## Final Response Expectations

When reporting work:

- Summarize what changed and why.
- List verification commands and results.
- Mention any tests or checks that were not run.
- Call out remaining live-trading risk plainly.

