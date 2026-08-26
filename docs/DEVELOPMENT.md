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

在仓库根目录执行：

```powershell
just setup
just check
uv run --offline --no-sync pytest
```

`setup` 是默认的一键本地初始化入口：它先通过 `env-bootstrap` materialize 已审计依赖，再创建或迁移本地
paper 安全配置，且不启动 Docker。`env-bootstrap` 仍是唯一显式的本地依赖同步边界；之后所有 `uv run` 命令必须使用
`--offline --no-sync`，避免命令隐式下载或 materialize 依赖：

```powershell
uv run --offline --no-sync python scripts/dev/check_env.py
uv run --offline --no-sync pytest tests/research
uv run --offline --no-sync ruff check .
uv run --offline --no-sync python scripts/ci/check_mypy_baseline.py check
```

只在确实需要本地 PostgreSQL 或 integration 测试时才启动 Docker：

```powershell
just setup-postgres
```

需要定位具体阶段时，可改用 `just db-up` 与 `just db-migrate` 分步执行。

Linux 上的完整 `pytest` 包含真实 PostgreSQL restore drill；在运行前，`PATH` 必须能找到与本机 PostgreSQL
服务端 major 兼容的 `pg_dump`、`pg_restore` 和 `psql`。`just dev-postgres` 只启动 Docker PostgreSQL，不会安装这些
宿主机客户端；缺少它们时完整套件会明确失败，不能把 restore drill 静默跳过。Windows 工作站不执行该 Linux-only drill。

数据库自动化只复用既有数据并执行前向迁移，绝不会清空数据库、表、schema 或 Docker volume。测试数据库必须是隔离的
`northstar_test`；具体连接与备份边界见[运行手册](OPERATIONS.md)。

存储职责固定为：交易状态使用 PostgreSQL，大规模历史数据使用受治理的 Parquet，历史研究/回测分析使用 DuckDB 查询
Parquet，本地工具集才可使用独立 SQLite。DuckDB 或 SQLite 不得替代 PostgreSQL integration test、交易前事实或风险状态；
所有 Parquet 输入仍须通过版本、hash、lineage 和 PIT 校验。

### 历史 Parquet Lake 与 DuckDB

历史 Lake 与当前可覆盖的 `storage/market` 投影刻意分离。先通过受控 Source → ArtifactStore → `DatasetVersion`
链完成授权、质量和血缘验证；只有与该 artifact canonical payload 完全一致的 Parquet，才可以物化到 Lake：

```powershell
uv run --offline --no-sync northstar data lake materialize --input <verified-artifact.parquet> --dataset-version <dataset-version-sha256> --artifact-snapshot <snapshot-sha256> --kind bars --event-time-column date
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
uv run --offline --no-sync northstar local-tools lake-index rebuild
uv run --offline --no-sync northstar local-tools lake-index list --kind bars
```

`rebuild` 先逐份调用 Lake verify，再以并发安全的 SQLite transaction 追加新的 index generation；SQLite 损坏或 schema
不兼容时，只会隔离固定的 tool-owned 文件并在显式 rebuild 中重建。`list` 不是验证，也不会被 DuckDB 或 PostgreSQL
自动读取。

当前开发期只有一个完整的 Alembic 基线 `0001_current_schema_baseline`，不支持从历史 revision 升级。若本地开发库的
`alembic_version` 不是该基线，操作者必须在仓库自动化之外手动重建它，然后才可运行 `just setup-postgres` 或
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
uv run --offline --no-sync northstar data profiles
uv run --offline --no-sync northstar data download --profile cn_futures_daily_trend_offline
uv run --offline --no-sync northstar data validate --profile cn_futures_daily_trend_offline
uv run --offline --no-sync northstar backtest run portfolio --profile cn_futures_daily_trend_offline
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
- 改动共享模型、配置、数据库、execution 或 risk 时，完成前运行完整 `uv run --offline --no-sync pytest`；
- 修改架构或运行边界时，同步更新相应的规范文档和 [主实施计划](planning/MASTER_IMPLEMENTATION_PLAN.md)。

## 7. 排查顺序

先检查配置与画像，再检查数据授权、artifact hash、quality、PIT、Contract Master 与 calendar；最后才检查策略逻辑。
不要通过放宽 gate、伪造现时数据或使用未来规则让回测“跑通”。

如遇订单、账户、日历、报价、保证金、对账或 broker 状态未知，停在 `NO NEW RISK`，收集只读诊断，然后按
[运行手册](OPERATIONS.md) 的恢复和人工审批流程处理。
