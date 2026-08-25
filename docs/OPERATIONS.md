# 运行、配置与部署手册

本文是 Northstar Quant 的运行、配置、报告、部署和数据保全操作权威。它不授予真实交易权限；架构与执行边界见
[架构设计](ARCHITECTURE.md)，数据/AI/安全治理见[治理与安全](GOVERNANCE.md)。

## 1. 配置事实来源

运行时配置是显式、typed、validated 的。按职责使用下列文件，而不是用环境变量或一个临时 YAML 隐式覆盖全部行为：

| 类别 | 事实来源 | 用途 |
|---|---|---|
| 应用设置 | `.env`、`configs/app.yaml`、`configs/app.local.yaml` | 本机私有运行参数与活动配置 |
| 可跟踪模板 | `.env.example`、`configs/app.example.yaml` | 安全默认值，不能当作运行时配置 |
| 画像 | `configs/profiles/` | offline、simulated、future live 的明确生命周期 |
| 数据与准入 | `configs/data/sources.yaml`、`configs/research/admission/` | source、授权和研究资格 |
| 合约/日历/规则 | `configs/instruments/`、`configs/calendars/` | Contract Master 与订单前事实 |
| 运维策略 | `configs/maintenance/` | 输出保留和备份就绪证据 |

首次本机设置使用：

```powershell
just env-bootstrap
uv run --offline --no-sync python scripts/dev/setup.py --initialize-config
```

只有明确传入 `--with-postgres --migrate` 才会将 Docker PostgreSQL 纳入本机设置。初始化不会下载市场数据、启动 scheduler
或调用真实交易；它不会覆盖已有疑似生产、非-paper、live、kill-switch 或外部数据库配置。需要重置本地开发配置时，
操作者必须显式使用 `--confirm-reset-local-dev-config YES`。

安全默认值必须保持：

```text
NORTHSTAR_BROKER=paper
NORTHSTAR_LIVE_TRADING_ENABLED=false
```

`NORTHSTAR_` 是唯一允许的环境变量前缀。`configs/app.yaml`、`.env` 和所有 secret 均不得提交；不要把生产 token、
账户号、CTP credential、数据库 URL 或对象存储凭据写进 tracked 文件。

## 2. 运行目录与数据保全

运行目录由 settings 中的 `runtime.*` 字段决定，包括 `runtime.downloads_dir`、`runtime.log_dir`、storage、reports
和临时 staging 目录。它们是生成物，不应版本控制。输出保留策略在
`configs/maintenance/output_retention.yaml` 中：

```powershell
# 只列出候选，不删除。
uv run --offline --no-sync northstar data cleanup

# 只有 YAML 策略已启用且操作者显式确认时才执行受限清理。
uv run --offline --no-sync northstar data cleanup --apply
```

清理只可处理策略 allowlist 中已过期的下载缓存和临时文件；不能触及 reports、release、运行状态、数据库、备份或
Docker volume。未知路径、符号链接、范围不清或未显式确认时应失败关闭。

Northstar 的核心运行数据库是 PostgreSQL：

```powershell
just dev-postgres
```

本地 PostgreSQL 使用独立数据卷；自动化不会使用 `down -v`、drop、truncate、delete 或 migration downgrade。
仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷；数据库删除或清空只能由用户在仓库自动化之外手动执行。
测试数据库仅可为隔离的 `northstar_test`。`init-db`/`dev-postgres` 只能前进到 Alembic head。

SQLite 仅允许由 Local tools 作为独立的本地缓存、索引或 scratch storage 使用；它不配置为
`NORTHSTAR_DATABASE_URL`，不参与 Alembic、`init-db`、核心 integration test，也不能保存订单、持仓、
风险、审批、对账或审计的权威事实。

大规模历史数据以受治理的 Parquet 制品保存：tick、bars、factors、features 和可复现的 research/backtest
输入或结果必须保留 manifest、内容 hash、lineage、PIT、license 与保留期语义。DuckDB 只查询这些 Parquet 制品
进行历史分析；它不保存当前交易状态，不直写 PostgreSQL 权威记录，也不绕过 Research、Risk 或 Execution 门禁。
当前代码已使用 Parquet 发布受治理的市场制品，但尚未安装 DuckDB 或提供 DuckDB runtime adapter、CLI 和查询契约；
具体 DuckDB adapter 或 Local tools SQLite schema 尚须在对应工具/分析 Work Package 中单独实现与验收。

`paper` / `ctp_sim` 的可变模拟 broker state 已保存到 PostgreSQL 的账户隔离快照与不可变 transition 审计链；不会写入
`state.json` 或 Local-tools SQLite。该 adapter-private 状态机不替代 PostgreSQL 中的 durable order、fill、position
snapshot、risk、approval、reconciliation 与 audit 账本。当前 Contract Master / CTP mapping 仍为版本受控 YAML 配置，迁入
PostgreSQL 的时间版本化合约权威库需要单独实现。

当前开发期的 head 是唯一完整基线 `0001_current_schema_baseline`，历史 revision 不提供升级路径。若本地
`alembic_version` 记录其他值，必须由操作者在仓库自动化之外手动重建本地数据库或数据卷，然后再执行
`just dev-postgres` 或 `northstar init-db`。仓库自动化不会 drop、truncate、stamp、downgrade 或替你重建数据库。

## 3. 数据、日历与运行模式

### 数据与日历

`configs/data/sources.yaml` 描述 source 事实，但 source 能否用于研究或生产还取决于授权、artifact、质量和准入状态。
市场数据、Contract Master、规则和 Calendar 都必须作为可追溯的 `ArtifactSnapshot` 使用。

真正可执行的品种画像必须将经授权 normalized calendar payload 的 hash 显式写入
`futures.calendar_artifact_snapshot_hashes`。当前项目日历配置没有 runtime Calendar Artifact，不能以工作日、`XSHG`
或测试 fixture 替代商品期货夜盘/休市事实；因此提交路径会以 `TRADING_CALENDAR_ARTIFACT_REQUIRED` 拒绝。

### Offline

`configs/profiles/offline/` 仅用于数据下载、特征研究、回测和本地撮合。连续合约只能支持研究，
`data.live_trading_eligible: false`。见[开发与研究工作流](DEVELOPMENT.md)。

### Simulated

`configs/profiles/simulated/` 配合 `NORTHSTAR_BROKER=ctp_sim` 使用。`ctp_sim` 是本地持久化 CTP 语义模拟，
不连接期货公司，也不代表真实 CTP adapter 已实现。账户、日历、规则、报价、持仓、订单或风险状态未知时不能增加风险。

可用的只读/预演命令示例：

```powershell
uv run --offline --no-sync northstar live signal --profile cn_futures_daily_trend_simulated
uv run --offline --no-sync northstar live sync
uv run --offline --no-sync northstar live risk-check --profile cn_futures_daily_trend_simulated
uv run --offline --no-sync northstar live preflight --profile cn_futures_daily_trend_simulated
uv run --offline --no-sync northstar live preview-rebalance --profile cn_futures_daily_trend_simulated
```

### Real account / CTP

仓库没有 production YAML，也没有真实 CTP 报单能力。禁止新增占位画像来启动 `live scheduler`。真实账户需要数据授权、
真实合约/日历/保证金/规则、账户和未完成订单状态、preflight、全局安全开关、经认证人工审批，以及用户对 profile、
broker、account、environment 与 action 的明确确认。少一项即 `NO NEW RISK`。

## 4. 报告、通知与调度

回测/研究报告写入 `reports/`，包含可读报告、machine-readable JSON 和不可变 `manifest.json`。PDF 渲染和邮件投递是
报告分发能力，不是交易控制面；如果导出内容含 secret 或脱敏无法确认，投递必须失败关闭。

私有 ntfy 可以作为即时告警通道，但必须使用独立、root-operated 的运维工作流和专用凭据；它不能成为 broker credential、
交易开关或恢复 HALT 的替代路径。邮件、ntfy 和 scheduler 都只消费受控通知，不能制造订单。

Scheduler 使用 typed job registry。任何 live 相关 job 都会先过画像、生命周期、风险和运行门禁；当前无满足条件的
production profile 时它必须拒绝启动。

## 5. Linux 部署边界

生产运行目标是 Linux x86_64，控制端可为 Windows 或 Linux：

```text
workstation
→ just / Python controller
→ strict SSH stdin
→ root-owned signed release gate
→ package → upload → install/upgrade → migrate → restart → health → promote
```

常用入口：

```powershell
just deploy-prod
just prod-health
```

部署控制器、脚本分工和环境变量参见[`scripts/README.md`](../scripts/README.md)，部署声明见
[`infra/README.md`](../infra/README.md)。release gate 先验证签名 authority、manifest、control/runtime bundle、
SHA-256、archive 索引和固定入口，再在 root-owned transaction 中执行。迁移开始后失败只能人工恢复；不得自动 downgrade、
重试迁移、绕过 health 或切换到真实交易。

systemd 使用 root-owned release/env snapshot、最小可写路径、`ProtectSystem=strict` 和 loopback-only dashboard。
health、logs、diagnose 和 backup status 是只读操作；服务默认不会因部署而开启非-paper 交易。

## 6. 备份、恢复与灾备

`configs/maintenance/database_backup_readiness.yaml` 只保存无 secret 的就绪证据路径和时效要求；它不会创建、上传、恢复或
删除备份。查看证据：

```powershell
uv run --offline --no-sync northstar ops backup status
```

受控维护脚本包括 `backup_bundle.py` 与 `restore_drill.py`。备份包必须有完整文件清单和 SHA-256，外部目录必须预先挂载、
私有且不在 release、reports 或 storage 之内。采集前及最终发布前均需确认服务 inactive；脚本不能自动停止服务。
备份包不复制 `paper` 或 `ctp_sim` 的 `state.json`：它们的当前快照与 transition 审计均属于 PostgreSQL，
仅由 PostgreSQL 逻辑转储恢复。

恢复演练只允许 loopback 隔离的 `northstar_test`，以 schema transaction rollback 证明恢复过程；它不是生产 restore。
当前仍缺生产 DR policy、加密异地副本、WAL/PITR、RPO/RTO 目标和受控恢复演练，不能把本地 Docker 或 loopback evidence
升级为 production DR 结论。

## 7. 故障与人工恢复

订单状态需要覆盖 accepted、rejected、partial fill、fill、cancel pending、cancelled 和 unknown。回调可以重复或乱序；
reconnect、timeout、network partition、DB 不可用、身份不一致、过期事实、保证金/价格限制和 rollover 都必须失败关闭。

unknown order、对账差异或未解释 fill 会进入 sticky `HALT`。自动化不得恢复 HALT、不得取消真实订单、不得增加仓位。
收集只读诊断后，由获得权限的人按照 [P10 Trading Failure Matrix](planning/P10_TRADING_FAILURE_MATRIX.md) 和外部操作流程
人工处置。
