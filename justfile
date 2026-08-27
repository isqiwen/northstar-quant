# Northstar Quant 的统一开发、部署与运维命令。
# Windows 使用 PowerShell，Linux 使用默认 shell；所有实质工作都委托给跨平台 Python
# 控制面或 Linux 目标端脚本，避免在 Just recipe 中复制业务与安全逻辑。

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

default:
    @{{quote(just_executable())}} --list

dev-check:
    python scripts/dev/check_env.py --require-config --require-postgres --require-just --require-git

# The only local entry point that may resolve or materialize Python packages.
# It first reuses a state-matched, offline-verified .venv; otherwise it builds a
# sibling fresh venv from reviewed lock artifacts and atomically promotes it.
# All other recipes fail closed if this stage has not completed.
env-bootstrap:
    python scripts/ci/check_dependency_policy.py
    python scripts/dev/run_uv.py lock --check --offline
    python scripts/ci/check_secrets.py
    python scripts/ci/bootstrap_pep517.py --profile development

# 强制重建已锁定的开发环境；仍只复用 .northstar 中经校验的缓存，不从旧 .venv 取包。
env-bootstrap-refresh:
    python scripts/ci/check_dependency_policy.py
    python scripts/dev/run_uv.py lock --check --offline
    python scripts/ci/check_secrets.py
    python scripts/ci/bootstrap_pep517.py --profile development --refresh

# 仅展示工具安装计划；不安装、不启动任何服务，也不接受任何许可。
# 缺少 just 时，直接使用 `python scripts/dev/setup.py --bootstrap-tools`。
dev-bootstrap:
    python scripts/dev/setup.py --bootstrap-tools

# 唯一的一键本地初始化：先由 Python 首次入口确认缺失工具；Ubuntu/Debian 会默认安装/启用本机 PostgreSQL，
# 再委托 just 初始化依赖、paper 安全配置与前向迁移。
setup:
    python scripts/dev/setup.py --initialize-workstation

dev-setup: env-bootstrap
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/dev/setup.py --initialize-config

# 显式复用本机 PostgreSQL；只升级至 Alembic head，绝不删除/清空数据库、表或 schema。
dev-postgres:
    python scripts/dev/check_env.py --require-postgres
    python scripts/dev/run_just.py env-bootstrap
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/dev/setup.py --initialize-config --with-postgres --migrate

db-up:
    python scripts/dev/check_env.py --require-postgres
    python scripts/dev/run_just.py env-bootstrap
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/dev/setup.py --initialize-config --with-postgres

db-migrate: dev-postgres

test-unit:
    python scripts/dev/run_uv.py run --offline --no-sync pytest tests/application/unit tests/data/unit tests/intelligence/unit tests/research/unit tests/portfolio_risk/unit tests/trading_execution/unit tests/foundation/unit -q

test-backtest:
    python scripts/dev/run_uv.py run --offline --no-sync pytest tests/research/unit -q

test-cli:
    python scripts/dev/run_uv.py run --offline --no-sync pytest tests/foundation/contract/test_cli_help.py -q

test:
    python scripts/dev/run_uv.py run --offline --no-sync pytest

lint:
    python scripts/dev/run_uv.py run --offline --no-sync ruff check .

typecheck:
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/ci/check_mypy_baseline.py check

check:
    python scripts/ci/check_dependency_policy.py
    python scripts/dev/run_uv.py lock --check --offline
    python scripts/ci/check_secrets.py
    python scripts/dev/run_uv.py run --offline --no-sync ruff check .
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/ci/check_mypy_baseline.py check

# P8 候选证据矩阵只重放固定的 offline / paper / ctp_sim 验收测试；
# 它拒绝 live、production 环境和任何真实 broker，且不执行部署、恢复或交易命令。
candidate-acceptance:
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/ci/check_integrated_candidate.py

# 发布预览始终是本地 dry-run；不连接 Linux 目标，也不执行部署或交易动作。
deploy-preview inventory='deploy.env':
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/deploy/deploy.py --inventory "{{inventory}}" --dry-run

# 默认部署命令进入 Python 控制面；目标配置的 SERVICE_MODE 默认必须是 health。
deploy-prod signing_key inventory='deploy.env':
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/deploy/deploy.py --inventory "{{inventory}}" --apply --signing-key "{{signing_key}}"

# 首次或明确更新远端唯一活动 .env 时使用；Python 和 Linux 后端都会执行 production 门禁。
deploy-prod-with-env signing_key inventory='deploy.env':
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/deploy/deploy.py --inventory "{{inventory}}" --apply --upload-env --signing-key "{{signing_key}}"

# 只有已通过画像、数据、preflight 与显式确认的生产调度器配置才能使用此命令。
deploy-prod-live signing_key inventory='deploy.env':
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/deploy/deploy.py --inventory "{{inventory}}" --apply --confirm-live-deploy YES --signing-key "{{signing_key}}"

ops-health inventory='deploy.env':
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/ops/health.py --inventory "{{inventory}}"

ops-logs inventory='deploy.env':
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/ops/logs.py --inventory "{{inventory}}"

ops-diagnose inventory='deploy.env':
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/ops/diagnose.py --inventory "{{inventory}}"

# 仅读取独立备份系统留下的就绪证据；不会创建备份或恢复数据库。
ops-backup inventory='deploy.env':
    python scripts/dev/run_uv.py run --offline --no-sync python scripts/ops/backup.py --inventory "{{inventory}}"
