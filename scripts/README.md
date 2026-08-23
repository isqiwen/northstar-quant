# Scripts

`justfile` 是 Windows 与 Linux 开发机统一的第一层命令面。Just recipe 只路由命令；可跨平台的
判断、配置读取、制品构建和 SSH 编排位于 Python，只有目标服务器操作位于 Linux shell。

```text
Windows / Linux developer workstation
        │
        ▼
      just
        │
        ▼
scripts/dev|deploy|ops/*.py        # 跨平台控制面
        │ SSH stdin（部署只传已签名的字节流）
        ▼
/usr/local/libexec/northstar-quant/release-gate
        │ 验签、索引验证、root-owned transaction
        ▼
scripts/deploy/gate_release.sh      # 仅从 root-owned 控制 bundle 执行
```

## 常用命令

| 命令 | 工作站 | 行为 |
| --- | --- | --- |
| `just dev-check` | Windows / Linux | 只读检查 Python、uv、just、Git、Docker/Compose/daemon 与部署工具状态。 |
| `just dev-bootstrap` | Windows / Linux | 仅预览 uv、just、Git 的系统安装计划；不执行安装。 |
| `just dev-bootstrap-docker` | Windows / Linux | 仅预览 Docker + Compose v2 安装计划；不启动 Docker。 |
| `just env-bootstrap` | Windows / Linux | 先在同级 fresh staging venv 中从受审计的锁定构建输入离线 materialize 依赖，完整验证后才切换 `.venv`；唯一获准 source-only 包逐字节验证后离线构建。 |
| `just dev-setup` | Windows / Linux | 先执行 `env-bootstrap`，再创建/迁移本地安全配置；不启动 Docker。 |
| `just dev-postgres` | Windows / Linux（有 Docker） | 显式启动并复用本地 PostgreSQL，创建隔离测试库且只升级至 Alembic head。 |
| `just test-unit` / `just test-backtest` / `just test-cli` | Windows / Linux | 跨平台开发验证。 |
| `just candidate-acceptance` | Linux CI / 已配置隔离 PostgreSQL 的工作站 | P8 固定候选证据矩阵已有三条独立真实 seam：P4→P1→P2 的 Intelligence→Research 静态 research-only 投影、PASS/具名 `CANDIDATE` Card 经独立人工 activation receipt 到 P3 `StrategyTarget` v2，以及 P8-WP05 opaque-authority `Portfolio/Risk→ctp_sim` 闭环。最后一条只使用原始 provenance request、隔离 PostgreSQL 一次性 consumption + durable intent、模拟 fill 和直接 simulator reconciliation；receipt 及 matrix eligibility 仍恒为 false。仅重放受控 offline / paper / `ctp_sim` 测试；任何 live、production 或真实 broker 配置都会失败关闭，且不部署、恢复或提交订单。 |
| `just deploy-prod <signing_key>` | Windows / Linux | 显式构建并签名发布到 Linux 目标；默认 `SERVICE_MODE=health`。 |
| `just ops-health` / `ops-logs` / `ops-diagnose` / `ops-backup` | Windows / Linux | 通过 SSH 读取 Linux 目标状态。 |

首次机器只有 Python 3.11+ 时，不依赖 `uv` 或 `just`，先使用 Python 入口预览工具计划：

```bash
python scripts/dev/setup.py --bootstrap-tools
python scripts/dev/setup.py --bootstrap-tools --install-docker
```

两条命令默认都不会安装、下载、启动 Docker 或接受许可。确认计划后，普通工具安装必须追加
`--apply --confirm-tool-install YES`；Docker 还必须追加 `--confirm-docker-install YES`。Windows 使用
`winget` 计划；Linux 仅正式支持 Ubuntu/Debian，其他发行版会失败关闭。bootstrap 不安装 Python、
不配置 WSL、不修改 Docker 用户组，也不自动安装部署所需的 `ssh`/`ssh-keygen`。

没有安装 `just` 时，先运行 `python scripts/ci/check_dependency_policy.py`、
`uv lock --check --offline`、`python scripts/ci/check_secrets.py` 与
`python scripts/ci/bootstrap_pep517.py --profile development`；随后项目初始化仍可使用同一层 Python
入口，例如 `uv run --offline --no-sync python scripts/dev/setup.py --initialize-config` 或
`uv run --offline --no-sync python scripts/deploy/deploy.py --inventory deploy.env`。

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `dev/check_env.py` | 无副作用的工作站检查，分别报告命令、Compose v2、daemon 与部署工具状态。 |
| `dev/setup.py` | 工具计划与项目初始化的显式入口；系统安装必须双重确认。 |
| `dev/tool_bootstrap.py` | Windows/Ubuntu/Debian 的可审阅安装计划；默认不执行。 |
| `dev/sync_env_schema.py` | 唯一 `.env` 结构迁移器；通过 stdin 写值且不回显机密。 |
| `db/01_create_test_database.sql` | 首次 Docker PostgreSQL 初始化隔离测试库。 |
| `ci/check_mypy_baseline.py` | 校验或显式更新版本化 mypy 类型债务基线。 |
| `ci/check_dependency_policy.py` | P9 的标准库离线依赖完整性/来源策略门禁：在任何 `uv` 解析、同步或构建前解析 `pyproject.toml` 与 `uv.lock`，拒绝 source/hash/metadata 或 root dependency 不一致；精确约束 PEP 517 builder group 及唯一 source-only artifact 的 URL/大小/SHA-256，输出稳定、无机密的 inventory digest，不联网且不宣称 CVE 扫描。 |
| `ci/bootstrap_pep517.py` | 唯一允许 materialize Python 包的标准库入口：创建非 seed fresh venv，先只安装锁定 wheels，再对唯一获准 source artifact 流式校验并在离线/no-index/no-isolation 边界构建它与首方项目。development、CI 与 Linux release 使用同一流程；其后命令必须 `uv run --offline --no-sync`。 |
| `ci/check_secrets.py` | 扫描全部 tracked 可解码文本（含测试）；有效的 binary magic 才会跳过解码，未知 binary、非 UTF-8、不可读文件与符号链接失败关闭。允许标记只能用于带理由的 canonical disposable test/CI fixture，业务、配置、部署和文档中一律失败关闭。 |
| `ci/check_integrated_candidate.py` | P8 的固定、无 shell 的候选验收测试编排器；`P8_INTELLIGENCE_TO_RESEARCH` 覆盖 P4 v3 immutable projection、same-store source receipt/raw artifact/document/content/span verifier、唯一 context normalized-artifact/full-row-commitment replay、application-owned P1 publishing、P2 hash-only PIT/lineage 与架构边界。`P8_RESEARCH_TO_PORTFOLIO_RISK` 覆盖已具 PASS 证据的具名 `CANDIDATE` Card、独立人工 activation、receipt 与 `StrategyTarget` v2。`P8_EXECUTION_PROVENANCE_PREFLIGHT` 覆盖 activation/portfolio/risk/PIT/account/quote/rule 的重放和纯 application 边界；receipt 永远 non-tradable。`P8_CTP_SIM_CANDIDATE_E2E` 覆盖不透明 final authority、一次性 PostgreSQL consumption、direct/raw bypass refusal、锁内 state/quote 重验、双订单 batch、真实模拟 fill 和只从 simulator 读取的 provenance-aware reconciliation；`Portfolio/Risk→CTP sim` 已由受控 candidate E2E VERIFIED。仅 Data PIT→Research 保持 BLOCKED。它只经 `pytest.main` 重放已列明的安全证据，并在进入 pytest 前拒绝 live/production/真实 broker 环境。 |
| `deploy/{inventory,preflight,package,control_bundle,release_manifest,release_signing,deploy}.py` | 跨平台的非机密清单、预检、制品、canonical manifest、签名与部署控制面。 |
| `deploy/release_gate_bootstrap.py` | 服务器管理员一次性、带外安装固定 root release gate 的工具；绝不是普通部署步骤。 |
| `deploy/gate_release.sh` 与 `deploy/remote/linux/` | 目标端受限控制代码；仅在 root gate 验证签名并解包到 root-owned transaction 后执行，不能通过 SSH 暂存目录直接调用。 |
| `ops/*.py` | 从任意开发工作站读取 Linux 目标的健康、日志、诊断和备份证据。 |
| `ops/remote/linux/` | Linux-only 只读运维动作；恢复入口明确失败关闭。 |
| `maintenance/backup_bundle.py` | Linux 上经双重显式确认创建/验证受限逻辑备份包；不属于远程只读 ops 面。 |
| `maintenance/restore_drill.py` | 仅对 loopback `northstar_test` 的真实 PostgreSQL 客户端恢复演练。 |

`build/`、`data/`、`release/`、`maintenance/` 和 `tools/` 留给相应的可审阅工作流。下载数据、真实
备份、账户状态、数据库导出、`.env` 和凭据一律留在仓库外。

数据库保全是所有脚本的共同边界：仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷。
数据库删除或清空只能由用户在仓库自动化之外手动执行；开发、部署和运维路径只允许创建、复用、
升级或读取已明确配置的数据库。

## 部署与运维边界

`deploy.py` 默认是 dry-run；只有 `--apply`（或 `just deploy-prod /安全路径/release-signing-key`）才会连接 Linux
目标。每次 `--apply` 都必须显式给出未跟踪的本机 OpenSSH 发布私钥 `--signing-key <path>`；它必须与服务器
管理员带外安装到 `/etc/northstar/release-allowed-signers` 的 `northstar-release` 公钥对应。部署 SSH 身份只负责
传输，不能代替 release 签名 authority。部署清单 `deploy.env` 只允许非机密目标参数；上传唯一活动 `.env`
需要 `--upload-env`，且 production、broker 与真实交易确认会在控制面和目标端重复校验。首次**运行时**安装
额外需要 `--setup-server`，但它不会 bootstrap 或更新 root release gate。

正常发布先读取固定 gate identity，再把已签名 manifest、runtime bundle、control bundle 和可选环境输入经 SSH
stdin 交给 gate。没有 `scp` 到可写远端目录后再 sudo 执行脚本的路径；gate 先验证签名、完整成员索引和固定入口，
然后在 root-owned transaction 中运行控制代码。若 migration 已开始或提交结果未知，自动化保留事务证据并要求
管理员人工恢复；不得重试提交、自动 downgrade 数据库或自动重启旧 release。

Linux systemd、服务、scheduler、worker、目标监控和未来 live trading 不是 Windows 职责。`health`、
`logs`、`diagnose` 和 `ops backup` 只读；后者只验证独立备份系统留下的无秘密恢复演练证据，不运行
`pg_dump` 或恢复。显式的 `maintenance/backup_bundle.py` 才能创建包，要求服务静止和双重确认；
`maintenance/restore_drill.py` 只允许隔离 `northstar_test`。`restore.sh` 一律失败关闭，生产恢复必须
使用经审批的独立 runbook。

私网 Dashboard 不使用 Docker、Caddy 或 `80`/`443`。仅当 `deploy.env` 明确设置
`DASHBOARD_DEPLOY_ENABLED=1` 时，Linux 发布才会管理独立的
`<SYSTEMD_SERVICE_NAME>-dashboard.service`，它固定监听 `127.0.0.1`。私有 ntfy 不属于签名 release：
正常发布必须保持 `NTFY_DEPLOY_ENABLED=0`，其 Docker、DNS、bootstrap 和认证只可走
[Linux 一键部署](../docs/07_Linux一键部署.md#私有-ntfy可选独立-root-operated-工作流)规定的独立 root-operated 工作流。
