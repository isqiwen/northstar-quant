# 开发与研究工作流

本文是开发环境、代码约定、第一条研究路径和回测工作流的唯一操作说明。架构理由见
[架构设计](ARCHITECTURE.md)，运行与部署见[运行手册](OPERATIONS.md)，数据准入和 AI 边界见
[治理与安全](GOVERNANCE.md)。

## 1. 支持范围与安全起点

Windows x86_64 和 Linux x86_64 均为 Tier 1 开发平台。生产目标仅为 Linux x86_64；不要把本地 Windows
会话当作生产运行环境。

开发默认是 paper、offline 或本地 `ctp_sim` 语义演练。不要写入真实 CTP 凭据、不要创建 production profile、
不要通过测试 fixture 或连续合约绕过账户、日历、规则和 pre-trade gate。

## 2. 初始化与质量门禁

Linux 开发机需要 Python 3.11+。在 Ubuntu/Debian 上，高层 `--initialize-workstation` 默认安装发行版
`postgresql`/`postgresql-client`，并启用默认 `postgresql` systemd 服务，因此会提供与服务端匹配的
`pg_isready`、`psql`、`createdb`、`pg_dump` 与 `pg_restore`。它只管理默认的 `127.0.0.1:5432` 本机服务，
不修改 PostgreSQL 配置、认证规则或数据目录。

新服务上若 `northstar` 角色不存在，初始化才创建最小的 `LOGIN CREATEDB` 本地开发角色；空 `POSTGRES_PASSWORD` 会生成随机值并仅写入
未跟踪、权限受限的 `.env`，绝不输出。已有角色、密码或认证规则不会被覆盖：若角色存在，请先在 `.env` 填入匹配的密码。Windows、
非 Ubuntu/Debian Linux、非 5432 端口和所有低层分步命令仍要求操作者预先准备服务和凭据。
首次尚未安装 `uv`、`just` 或 Git 时，直接运行统一 Python 初始化入口：

```powershell
python scripts/dev/setup.py --initialize-workstation
```

入口会展示缺失工具的安装计划，并仅在交互终端输入 `YES` 后执行。`uv`、`just`、其 pipx 环境、缓存、状态和可执行文件
只会写入未跟踪的 `.northstar/`；项目通过固定路径调用它们，不修改 `PATH`，也不需要重启终端。安装完成后的同一次调用会转交
仓库本地 `just dev-postgres`：它 materialize 已审计依赖、验证本地 paper 配置、用 `pg_isready`/`psql` 验证服务、通过 `createdb`
创建或复用 `northstar` 和隔离的 `northstar_test`，并执行前向迁移。仅当刚安装的宿主机工具在当前进程仍不可见时，才会提示重新打开终端后再次运行。
工具就绪后，所有 recipe 都经由 `python scripts/dev/run_just.py` 调用。

无交互自动化只能显式传入确认值：

```powershell
python scripts/dev/setup.py --initialize-workstation --confirm-tool-install YES
```

该脚本可安装仓库本地 `uv`、`just` 和宿主机 Git；在 Ubuntu/Debian 的高层入口中也会默认安装并启用本机 PostgreSQL。Python 仍是宿主机前置条件。在 Ubuntu/Debian 上，`uv` 通过仓库本地的 `pipx`
安装：bootstrap 模块、pipx venv、缓存、状态和可执行文件均位于 `.northstar/`，使用 `pip --target`，绝不绕过 PEP 668
系统 Python 保护；`just` 下载固定版本的官方发布包并校验 SHA-256 后写入 `.northstar/bin`。服务未就绪、客户端工具缺失、URL 非 loopback
或凭据无法验证时，入口在迁移前失败关闭；它不会重置服务、覆盖角色密码、编辑认证规则或删除任何数据库数据。
工具就绪后，在仓库根目录执行：

```powershell
python scripts/dev/run_just.py setup
python scripts/dev/run_just.py test
python scripts/dev/run_just.py check
```

`setup` 是唯一的一键本地初始化入口：它先通过 `env-bootstrap` 核对 `.venv` 的 bootstrap 状态，并在锁文件、项目声明、Python、uv
或 bootstrap 代码变化时才从受审计输入重建依赖；然后创建或迁移本地 paper 安全配置，默认安装/启动符合条件的本机 PostgreSQL，
仅创建缺失的开发角色/数据库，并只前进到 Alembic head。
开发与仓库本地质量门禁的 wheel 缓存固定在 `.northstar/cache/uv`，唯一 source-only 制品每次复用前都校验大小和 SHA-256。`env-bootstrap` 仍是唯一显式的
本地依赖同步边界；之后所有命令通过 `python scripts/dev/run_uv.py` 固定解析 `.northstar/bin/uv`，并使用 `--offline --no-sync`，避免隐式下载或 materialize 依赖：

只有高层 `--initialize-workstation` 具备上述 Ubuntu/Debian 默认安装/启动权限。`pg_isready`、客户端工具、loopback URL、
认证、数据库创建权限或当前 Alembic 状态任一未知时，`setup` 会失败关闭；它不会下载市场数据、提交订单、停止/重置服务、
覆盖既有角色密码或删除数据库。

需要忽略有效状态并重建环境时，使用 `python scripts/dev/run_just.py env-bootstrap-refresh`；它优先复用仓库内经验证的缓存，
但锁定制品尚未缓存时仍会下载并验证，且不会信任旧 `.venv` 的包。普通失败会删除未提升的 sibling staging venv；只有
原子提升无法安全恢复时才保留它用于诊断，且 VS Code Explorer 与文件监听默认忽略这些目录。

```powershell
python scripts/dev/run_uv.py run --offline --no-sync python scripts/dev/check_env.py
python scripts/dev/run_uv.py run --offline --no-sync pytest tests/research
python scripts/dev/run_uv.py run --offline --no-sync ruff check .
python scripts/dev/run_uv.py run --offline --no-sync python scripts/ci/check_mypy_baseline.py check
```

需要定位具体阶段时，可改用 `python scripts/dev/run_just.py db-up` 与 `python scripts/dev/run_just.py db-migrate` 分步执行。

### VS Code 日常任务

工作区任务刻意只保留四个高频入口：开发初始化、完整测试、质量检查和环境诊断。初始化使用
Python 首次入口以便在仓库本地 `just` 尚未安装时也能引导工具，并始终准备 PostgreSQL；其余任务通过 `run_just.py` 委托给 `.northstar/bin/just`。默认 Test Task
运行完整 `test` recipe，默认 Build Task 运行 `check` recipe。

局部 `test-unit`、`test-backtest`、`test-cli` 适合在终端或 Python Test Explorer 中按改动范围执行；工具
bootstrap 与 Linux 发布预览也只保留显式终端入口。首次机器的 bootstrap 见
[`scripts/dev/README.md`](../scripts/dev/README.md)，发布预览使用 `python scripts/dev/run_just.py deploy-preview`，它强制 dry-run，
不建立 SSH 连接。

Linux 上的完整 `pytest` 包含真实 PostgreSQL restore drill；在运行前，`PATH` 必须能找到与本机 PostgreSQL
服务端 major 兼容的 `pg_isready`、`psql`、`createdb`、`pg_dump` 与 `pg_restore`。`dev-postgres` recipe 只验证和复用
已经运行的本机服务，不会安装或启动它；缺少工具或服务不可达时完整套件会明确失败，不能把 restore drill 静默跳过。Windows 工作站不执行该 Linux-only drill。

数据库自动化只复用既有数据并执行前向迁移。仓库自动化绝不删除或清空数据库、表、schema 或本机 PostgreSQL 数据目录。
测试数据库必须是隔离的
`northstar_test`；具体连接与备份边界见[运行手册](OPERATIONS.md)。

存储职责固定为：交易状态使用 PostgreSQL，大规模历史数据使用受治理的 Parquet，历史研究/回测分析使用 DuckDB 查询
Parquet，本地工具集才可使用独立 SQLite。DuckDB 或 SQLite 不得替代 PostgreSQL integration test、交易前事实或风险状态；
所有 Parquet 输入仍须通过版本、hash、lineage 和 PIT 校验。

### 历史 Parquet Lake 与 DuckDB

历史 Lake 与当前可覆盖的 `storage/market` 投影刻意分离。先通过受控 Source → ArtifactStore → `DatasetVersion`
链完成授权、质量和血缘验证；只有与该 artifact canonical payload 完全一致的 Parquet，才可以物化到 Lake：

```powershell
python scripts/dev/run_uv.py run --offline --no-sync northstar data lake materialize --input <verified-artifact.parquet> --dataset-version <dataset-version-sha256> --artifact-snapshot <snapshot-sha256> --kind bars --event-time-column date
```

物化结果会返回 immutable Lake version。使用 `northstar data lake verify` 重新计算 manifest、文件 hash、schema、分区和
`available_at`；再用 `northstar research lake-query --as-of <ISO-8601-with-timezone> --sql-file <query.sql>` 执行分析。
DuckDB 在内存中运行、只暴露已经通过 `available_at <= as_of` 过滤的 `lake_data` relation；它会重新验证 Parquet 字节后
创建本次查询专用 snapshot，并检查物理计划没有读取其他 relation。查询必须是单条 SELECT/WITH，不能使用外部 I/O、写入、
随机/时间/顺序敏感函数或用户自定义 `LIMIT`/`OFFSET`；系统统一稳定排序并限制结果行数。每次输出都有可回放 receipt，查询
或 receipt 本身不构成策略、风险批准或订单。

### SQLite Local-tools Lake 索引

SQLite 不是核心数据库的 fallback。唯一已实现的本地工具库位于 `<storage_dir>/local-tools/`，保存可从 Lake 重新构建的
manifest discovery metadata；它没有订单、成交、持仓、策略状态、风险、审批、对账或审计事实。显式操作如下：

```powershell
python scripts/dev/run_uv.py run --offline --no-sync northstar local-tools lake-index rebuild
python scripts/dev/run_uv.py run --offline --no-sync northstar local-tools lake-index list --kind bars
```

`rebuild` 先逐份调用 Lake verify，再以并发安全的 SQLite transaction 追加新的 index generation；SQLite 损坏或 schema
不兼容时，只会隔离固定的 tool-owned 文件并在显式 rebuild 中重建。`list` 不是验证，也不会被 DuckDB 或 PostgreSQL
自动读取。

当前开发期只有一个完整的 Alembic 基线 `0001_current_schema_baseline`，不支持从历史 revision 升级。若本地开发库的
`alembic_version` 不是该基线，操作者必须在仓库自动化之外手动重建它，然后才可运行 `python scripts/dev/run_just.py setup` 或
`northstar init-db`；不要添加 `stamp`、drop、truncate 或自动重建脚本来绕过这个边界。

## 3. 创建研究画像

从 `configs/profiles/offline/` 复制一个 `_offline` 画像，并同步检查：

- `profile_id` 与文件名一致，且以 `_offline` 结尾；
- 数据集、`data.source_id`、`universe_id` 和 `research_admission` 相互一致；
- 连续合约只标记为研究输入，不能作为实际可交易合约；
- 策略、backtest、risk、时间口径和版本字段是显式的；
- 任何未来 `ctp_sim` / live 画像都另建在各自目录，不能通过修改 offline 画像绕过状态门禁。

离线研究的最短安全路径：

```powershell
python scripts/dev/run_uv.py run --offline --no-sync northstar data profiles
python scripts/dev/run_uv.py run --offline --no-sync northstar data download --profile cn_futures_daily_trend_offline
python scripts/dev/run_uv.py run --offline --no-sync northstar data validate --profile cn_futures_daily_trend_offline
python scripts/dev/run_uv.py run --offline --no-sync northstar backtest run portfolio --profile cn_futures_daily_trend_offline
```

下载、校验和回测产物写入受控运行目录；每份报告都应保留不可变 `manifest.json`，并明确数据版本、配置、代码 revision、
成本与滑点模型。公开数据适合探索和工程验收，不代表已获生产数据授权。

## 4. 第一条策略的最小形状

策略应输出显式目标权重，不应直接调用执行层。平仓也应写成明确的零目标，而不是依赖旧持仓或隐式默认值。

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    instrument_id: str
    target_weight: float


def breakout_target(*, instrument_id: str, close: float, high_20: float) -> Target:
    target_weight = 0.10 if close > high_20 else 0.0
    return Target(instrument_id=instrument_id, target_weight=target_weight)
```

实现时把领域逻辑放入 `research/`，并为以下行为写 focused test：

- 正常信号和明确 `target_weight: 0.0` 的平仓路径；
- 无数据、PIT 不成立、契约映射未知和质量失败；
- 成本、滑点、换月、保证金/费用规则的输入版本；
- 同一 dataset/config/code 输入的可重放结果。

不要把 Feature、Strategy、PortfolioTarget、ExecutionPlan 或 BrokerOrder 合并成一个对象；完整语义见
[架构设计](ARCHITECTURE.md#3-六个领域)。

## 5. 回测与研究准入

推荐顺序是：连续合约探索 → 实际合约日线验证 → 仅在执行细节改变结论时进行分钟回放。每一步都需要遵守
`available_time <= simulation_time`，并记录 DatasetVersion、FeatureVersion、StrategyVersion、OOS 区间、配置、
代码 revision、成本与滑点模型。

策略候选的规范链为：

```text
Feature → Experiment → Backtest → Validation → OOS / Stress → Research Decision
```

研究结论不等于策略升级，更不等于订单权限。研究准入、供应商状态、数据授权和人工激活约束以
[治理与安全](GOVERNANCE.md) 为准。

## 6. 代码、配置与文档约定

- 新的稳定概念使用 typed structured model，不使用跨层 ad-hoc `dict`；
- 配置使用显式、typed、validated 的 schema，环境变量前缀只能是 `NORTHSTAR_`；
- 注释解释量化假设、时间语义、单位、失败关闭条件和不可变性，而不重复代码语法；
- 不新增 compatibility alias、legacy adapter、deprecated fallback 或旧 CLI 参数。项目未发布，调用方、测试、配置、
  文档与 migration 应在同一变更中迁移；
- 改动共享模型、配置、数据库、execution 或 risk 时，完成前运行完整 `python scripts/dev/run_uv.py run --offline --no-sync pytest`；
- 修改架构或运行边界时，同步更新相应的规范文档和 [主实施计划](planning/MASTER_IMPLEMENTATION_PLAN.md)。

## 7. 排查顺序

先检查配置与画像，再检查数据授权、artifact hash、quality、PIT、Contract Master 与 calendar；最后才检查策略逻辑。
不要通过放宽 gate、伪造现时数据或使用未来规则让回测“跑通”。

如遇订单、账户、日历、报价、保证金、对账或 broker 状态未知，停在 `NO NEW RISK`，收集只读诊断，然后按
[运行手册](OPERATIONS.md) 的恢复和人工审批流程处理。
