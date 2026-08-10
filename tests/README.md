# 测试说明

测试按验证边界分层，而不是按文件数量简单平铺：

```text
tests/
├── unit/          快速、隔离的业务逻辑测试
├── integration/   PostgreSQL 与跨模块协作测试
├── contract/      架构、迁移、CLI、文档和部署脚本契约
├── e2e/           完整业务闭环
└── support/       数据库工厂、fixture 和测试数据构造器
```

## 一键运行

macOS/Linux：

```bash
scripts/setup_dev.sh
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
# 设置本地非空 POSTGRES_PASSWORD 后执行
.\scripts\setup_dev.ps1
```

入口会同步锁定依赖、启动本地 Docker PostgreSQL、迁移应用库和隔离测试库、运行健康检查、
`pytest` 与 Ruff。它强制 `paper` 和禁用 live，不会下载市场数据或提交订单。

## 手动命令

先确保 Docker 已启动，且 `.env` 中的 `POSTGRES_PASSWORD` 非空。数据库测试使用
`northstar_test`，每个测试还会创建隔离 schema。

```powershell
# 全量测试
uv run pytest

# 按层运行
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m contract
uv run pytest -m e2e

# 聚焦单文件或单个用例
uv run pytest tests/unit/backtest/test_futures_daily_backtest.py -q
uv run pytest tests/integration/reporting/test_backtest_run_workflow.py::test_actual_daily_backtest_run_writes_one_auditable_report_artifact -q

# 静态质量门禁
uv run ruff check .
uv run python scripts/check_mypy_baseline.py check
```

Windows 上的部署脚本契约测试需要可执行 Bash。安装 Git for Windows 后，测试会自动从
`git.exe` 的安装目录定位 Git Bash；不需要修改系统 PATH。Linux/macOS 直接使用系统 Bash。

## 编写测试

- 纯计算和配置校验放在 `unit/`；
- 涉及 PostgreSQL、文件制品或多个模块协作时放在 `integration/`；
- CLI、文档链接、迁移、依赖边界和部署安全门禁放在 `contract/`；
- 只有完整研究/执行闭环才放在 `e2e/`；
- 优先使用 `postgresql_engine` 或 `postgresql_session_factory` fixture；只有并发测试才直接
  使用 `tests.support.database` 工厂。

不要通过 SQLite、跳过 preflight、降低风险门槛或修改真实交易开关来让测试通过。新的配置、
数据制品、回测口径或执行行为必须同步增加覆盖。
