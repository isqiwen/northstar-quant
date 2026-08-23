# Northstar Quant 的统一开发、部署与运维命令。
# Windows 使用 PowerShell，Linux 使用默认 shell；所有实质工作都委托给跨平台 Python
# 控制面或 Linux 目标端脚本，避免在 Just recipe 中复制业务与安全逻辑。

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

default:
    @just --list

dev-check:
    python scripts/dev/check_env.py

# The only local entry point that may resolve or materialize Python packages.
# It builds a sibling fresh venv from reviewed lock artifacts and only then
# atomically promotes it to the generated repository .venv; all other recipes
# fail closed if this stage has not completed.
env-bootstrap:
    python scripts/ci/check_dependency_policy.py
    uv lock --check --offline
    python scripts/ci/check_secrets.py
    python scripts/ci/bootstrap_pep517.py --profile development

# 仅展示工具安装计划；不安装、不启动 Docker，也不接受任何许可。
# 缺少 just 时，直接使用 `python scripts/dev/setup.py --bootstrap-tools`。
dev-bootstrap:
    python scripts/dev/setup.py --bootstrap-tools

# 仅将 Docker Desktop/Engine 与 Compose v2 加入预览计划；执行必须手工追加双重 YES 确认。
dev-bootstrap-docker:
    python scripts/dev/setup.py --bootstrap-tools --install-docker

dev-setup: env-bootstrap
    uv run --offline --no-sync python scripts/dev/setup.py --initialize-config

# 显式启动并复用本地 PostgreSQL；只升级至 Alembic head，绝不删除/清空数据库、表、schema 或 Docker 卷。
dev-postgres: env-bootstrap
    uv run --offline --no-sync python scripts/dev/setup.py --initialize-config --with-postgres --migrate

test-unit:
    uv run --offline --no-sync pytest tests/data/unit tests/intelligence/unit tests/research/unit tests/portfolio_risk/unit tests/trading_execution/unit tests/foundation/unit -q

test-backtest:
    uv run --offline --no-sync pytest tests/research/unit -q

test-cli:
    uv run --offline --no-sync pytest tests/foundation/contract/test_cli_help.py -q

check:
    python scripts/ci/check_dependency_policy.py
    uv lock --check --offline
    python scripts/ci/check_secrets.py
    uv run --offline --no-sync ruff check .
    uv run --offline --no-sync python scripts/ci/check_mypy_baseline.py check

# P8 候选证据矩阵只重放固定的 offline / paper / ctp_sim 验收测试；
# 它拒绝 live、production 环境和任何真实 broker，且不执行部署、恢复或交易命令。
candidate-acceptance:
    uv run --offline --no-sync python scripts/ci/check_integrated_candidate.py

# 默认部署命令进入 Python 控制面；目标配置的 SERVICE_MODE 默认必须是 health。
deploy-prod signing_key inventory='deploy.env':
    uv run --offline --no-sync python scripts/deploy/deploy.py --inventory "{{inventory}}" --apply --signing-key "{{signing_key}}"

# 首次或明确更新远端唯一活动 .env 时使用；Python 和 Linux 后端都会执行 production 门禁。
deploy-prod-with-env signing_key inventory='deploy.env':
    uv run --offline --no-sync python scripts/deploy/deploy.py --inventory "{{inventory}}" --apply --upload-env --signing-key "{{signing_key}}"

# 只有已通过画像、数据、preflight 与显式确认的生产调度器配置才能使用此命令。
deploy-prod-live signing_key inventory='deploy.env':
    uv run --offline --no-sync python scripts/deploy/deploy.py --inventory "{{inventory}}" --apply --confirm-live-deploy YES --signing-key "{{signing_key}}"

ops-health inventory='deploy.env':
    uv run --offline --no-sync python scripts/ops/health.py --inventory "{{inventory}}"

ops-logs inventory='deploy.env':
    uv run --offline --no-sync python scripts/ops/logs.py --inventory "{{inventory}}"

ops-diagnose inventory='deploy.env':
    uv run --offline --no-sync python scripts/ops/diagnose.py --inventory "{{inventory}}"

# 仅读取独立备份系统留下的就绪证据；不会创建备份或恢复数据库。
ops-backup inventory='deploy.env':
    uv run --offline --no-sync python scripts/ops/backup.py --inventory "{{inventory}}"
