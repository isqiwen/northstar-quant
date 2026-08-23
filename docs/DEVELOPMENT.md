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
just env-bootstrap
just dev-setup
just check
just test
```

`env-bootstrap` 是唯一显式的本地依赖同步边界。之后所有 `uv run` 命令必须使用
`--offline --no-sync`，避免命令隐式下载或 materialize 依赖：

```powershell
uv run --offline --no-sync python scripts/dev/check_env.py
uv run --offline --no-sync pytest tests/research
uv run --offline --no-sync ruff check .
uv run --offline --no-sync python scripts/ci/check_mypy_baseline.py check
```

只在确实需要本地 PostgreSQL 或 integration 测试时才启动 Docker：

```powershell
just db-up
just db-migrate
```

数据库自动化只复用既有数据并执行前向迁移，绝不会清空数据库、表、schema 或 Docker volume。测试数据库必须是隔离的
`northstar_test`；具体连接与备份边界见[运行手册](OPERATIONS.md)。

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
[架构设计](ARCHITECTURE.md#3-不可合并的领域语义)。

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
- 改动共享模型、配置、数据库、execution 或 risk 时，完成前运行完整 `just test`；
- 修改架构或运行边界时，同步更新相应的规范文档和 [主实施计划](planning/MASTER_IMPLEMENTATION_PLAN.md)。

## 7. 排查顺序

先检查配置与画像，再检查数据授权、artifact hash、quality、PIT、Contract Master 与 calendar；最后才检查策略逻辑。
不要通过放宽 gate、伪造现时数据或使用未来规则让回测“跑通”。

如遇订单、账户、日历、报价、保证金、对账或 broker 状态未知，停在 `NO NEW RISK`，收集只读诊断，然后按
[运行手册](OPERATIONS.md) 的恢复和人工审批流程处理。
