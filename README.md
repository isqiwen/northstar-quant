# Northstar Quant

面向中国商品期货的量化研究、情报、组合、风险和交易平台。项目是 real-money-adjacent 系统：当前支持的正向证据只限
offline、paper 与本地 `ctp_sim`；没有真实 CTP 连接、真实账户或实盘交易能力。

默认安全设置：

```text
NORTHSTAR_BROKER=paper
NORTHSTAR_LIVE_TRADING_ENABLED=false
```

账户、持仓、订单、风险、市场数据、日历、合约、保证金、授权或 broker 状态未知时，系统必须 `NO NEW RISK`。

## 快速开始

```powershell
python scripts/dev/setup.py --initialize-workstation
python scripts/dev/run_just.py test
python scripts/dev/run_just.py check
```

首次入口会展示缺失的 `uv`、`just` 和 Git 安装计划，并在交互终端要求输入 `YES`。`uv`、`just`、其 bootstrap 依赖、缓存、状态和可执行文件
都会写入未跟踪的仓库目录 `.northstar/`；项目通过固定路径调用它们，不修改 `PATH`，也不需要重启终端。安装完成后同一次
调用会继续完成依赖同步、安全配置和 Alembic 前向迁移；仅当刚安装的宿主机工具在当前进程仍不可见时，才会提示重新打开终端后再次运行。

在 Ubuntu/Debian 上，该高层入口默认以 `sudo` 安装 `postgresql` 与 `postgresql-client`，并执行
`systemctl enable --now postgresql`，提供 `pg_isready`、`psql`、`createdb`、`pg_dump` 与 `pg_restore`。随后它只接受
`127.0.0.1:5432` 的本机服务；若 `northstar` 角色不存在，才创建最小的
本地开发角色并将随机密码写入未跟踪的 `.env`，绝不在终端输出密码。已有角色、密码、认证规则、服务配置或数据不会被改写。
`northstar` 只能创建/复用 `northstar` 与隔离的 `northstar_test`，并前向迁移；仓库自动化不会停止、重置或删除 PostgreSQL 服务或数据。

Windows、非 Ubuntu/Debian Linux、非默认端口，及低层 `dev-postgres` 命令仍只检查操作者已 provision 的本机 PostgreSQL。
若已有 `northstar` 角色，则在未跟踪的 `.env` 中填写与其匹配的 `POSTGRES_PASSWORD` 后再运行初始化；入口不会覆盖该密码。

后续运行会先将 `.venv` 的 bootstrap 状态与锁文件、项目声明、Python、uv 和 bootstrap 代码核对，并做离线健康检查；
完全匹配时直接复用环境。wheel 缓存位于 `.northstar/cache/uv`，唯一获准 source-only 包也会在校验 SHA-256 后缓存；
需要强制重建时使用 `python scripts/dev/run_just.py env-bootstrap-refresh`。构建在原子替换 `.venv` 前失败时会自动删除本次
staging venv；只有替换无法安全恢复时才保留该目录供诊断，VS Code 默认隐藏这类生成目录。

需要分步排查时仍可使用底层入口：`python scripts/dev/run_just.py env-bootstrap`、
`python scripts/dev/run_just.py dev-setup`、`python scripts/dev/run_just.py db-up`、
`python scripts/dev/run_just.py db-migrate`。

数据库自动化只前向迁移和复用已有数据。仓库自动化绝不删除或清空数据库、表、schema 或本机 PostgreSQL 数据目录。
数据库删除或清空只能由用户在仓库自动化之外手动执行。

存储职责：交易状态使用 PostgreSQL；大规模历史数据使用 Parquet；历史分析由 DuckDB 查询 Parquet；SQLite 只用于
本地工具集的独立数据库。详见[架构设计](docs/ARCHITECTURE.md#存储职责)。

当前仍处于开发期，`alembic/versions/` 只保留完整的
`0001_current_schema_baseline`。旧 revision 不受支持；若本地数据库记录的是旧 revision，必须由操作者在
仓库自动化之外手动重建后，再运行 `python scripts/dev/run_just.py setup` 或 `northstar init-db`。自动化不会 reset、stamp 或删除数据库。

## 文档

[文档导航](docs/README.md) 是唯一入口：

- [架构设计](docs/ARCHITECTURE.md)：领域边界、证据流、执行链和非升级边界；
- [开发与研究工作流](docs/DEVELOPMENT.md)：本地设置、画像、策略、回测和质量门禁；
- [运行、配置与部署手册](docs/OPERATIONS.md)：配置、运行模式、报告、部署、备份与故障处理；
- [数据、研究、AI 与安全治理](docs/GOVERNANCE.md)：数据授权、研究准入、AI 权限、审计和人工控制；
- [主实施计划](docs/planning/MASTER_IMPLEMENTATION_PLAN.md)：唯一实施进度事实来源；
- [P10 验收证据](docs/planning/P10_MATURE_V1_ACCEPTANCE_EVIDENCE.md) 与
  [交易故障矩阵](docs/planning/P10_TRADING_FAILURE_MATRIX.md)：已验证能力和外部阻塞的受控记录。

## 当前边界

当前实施状态、完成度与外部阻塞只由[主实施计划](docs/planning/MASTER_IMPLEMENTATION_PLAN.md)维护；本 README
不复制会随工作包变化的数字。生产灾备与权威数据 onboarding 仍需要外部授权、主机、数据许可和制品证据，它们不会因
本地 fixture 或 `ctp_sim` 成功而自动升级。

## License

仓库当前未附带单独许可证文件。若需开源发布，应先补充明确的 `LICENSE`。
