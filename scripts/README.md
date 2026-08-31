# Scripts

`justfile` 是 Linux x86_64 工作站统一的第一层命令面，必须经 `scripts/dev/run_just.py` 调用仓库 `.northstar/bin/just`。Just recipe 只路由命令；
判断、配置读取、制品构建和 SSH 编排位于 Python，目标服务器操作位于 Linux shell。

```text
Linux x86_64 workstation / control host
        │
        ▼
scripts/dev/run_just.py
        │
        ▼
.northstar/bin/just
        │
        ▼
scripts/dev|deploy|ops/*.py        # Linux x86_64 控制面
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
| `python scripts/dev/run_just.py dev-check` | Linux x86_64 | 只读验证完整开发前提：Python、uv、just、Git、本机 PostgreSQL 客户端与本地配置；部署工具仍只报告状态。 |
| `python scripts/dev/run_just.py dev-bootstrap` | Linux x86_64 | 仅预览 uv、just、Git 的安装计划；不执行安装。 |
| `python scripts/dev/run_just.py env-bootstrap` | Linux x86_64 | 先离线验证状态匹配的 `.venv`，仅在输入或健康检查变化时才在同级 fresh staging venv 中从受审计锁定输入重建；普通失败会清理未提升 staging，wheel 与逐字节验证的唯一 source-only 包复用 `.northstar/cache/`。 |
| `python scripts/dev/run_just.py env-bootstrap-refresh` | Linux x86_64 | 显式忽略当前 `.venv` 状态并创建 fresh staging venv；优先复用 `.northstar/cache/` 中经验证的下载物，缓存未命中时仍按 lock 下载并校验。 |
| `python scripts/dev/setup.py --initialize-workstation` | Linux x86_64 | 唯一一键本地初始化：缺少基础工具时展示计划并要求 `YES`；Ubuntu/Debian 默认安装/启用本机 PostgreSQL，随后执行 `env-bootstrap`、安全配置、数据库创建与 Alembic 前向迁移。 |
| `python scripts/dev/run_just.py dev-setup` / `dev-postgres` | Linux x86_64 | 底层分步入口，分别对应配置初始化和已运行 PostgreSQL 的验证、数据库创建/复用及迁移。 |
| `python scripts/dev/run_just.py db-up` / `db-migrate` | 已配置本机 PostgreSQL 的工作站 | 数据库排障用分步入口：先验证/创建或复用本地数据库，再单独前向迁移。 |
| `python scripts/dev/run_just.py test-unit` / `test-backtest` / `test-cli` / `test` | Linux x86_64 | 本地开发验证；`test` 为完整 pytest。 |
| `python scripts/dev/run_just.py lint` / `typecheck` / `check` | Linux x86_64 | 静态质量门禁；`check` 同时执行依赖策略、lock、secret、Ruff 和 mypy baseline。 |
| `python scripts/dev/run_just.py candidate-acceptance` | 已配置隔离 PostgreSQL 的 Linux 工作站 | P8 固定候选证据矩阵已有三条独立真实 seam：P4→P1→P2 的 Intelligence→Research 静态 research-only 投影、PASS/具名 `CANDIDATE` Card 经独立人工 activation receipt 到 P3 `StrategyTarget` v2，以及 P8-WP05 opaque-authority `Portfolio/Risk→ctp_sim` 闭环。最后一条只使用原始 provenance request、隔离 PostgreSQL 一次性 consumption + durable intent、模拟 fill 和直接 simulator reconciliation；receipt 及 matrix eligibility 仍恒为 false。仅重放受控 offline / paper / `ctp_sim` 测试；任何 live、production 或真实 broker 配置都会失败关闭，且不部署、恢复或提交订单。 |
| `python scripts/dev/run_just.py deploy-preview` | Linux x86_64 | 明确的本地 dry-run；可构建和验证制品，但不会建立 SSH 连接或执行 Linux 目标操作。 |
| `python scripts/dev/run_just.py deploy-prod <signing_key>` | Linux x86_64 | 显式构建并签名发布到 Linux 目标；默认 `SERVICE_MODE=health`。 |
| `python scripts/dev/run_just.py ops-health` / `ops-logs` / `ops-diagnose` / `ops-backup` | Linux x86_64 | 通过 SSH 读取 Linux 目标状态。 |

首次机器只有 Python 3.11+ 时，不依赖 `uv` 或 `just`，直接使用统一 Python 入口：

```bash
python scripts/dev/setup.py --initialize-workstation
```

它会展示仓库本地 `uv`、`just` 与宿主机 Git 的安装计划，并仅在交互终端输入 `YES` 后执行。`uv`、`just`、其 pipx bootstrap 模块、缓存、状态和
虚拟环境均写入仓库未跟踪的 `.northstar/`，项目命令以固定路径调用它们，不修改 `PATH`，也不要求重启终端。安装完成后同一入口会立即继续；
仅当刚安装的宿主机 Git 在当前进程仍不可见时，才提示重新打开终端后再次运行。
Ubuntu/Debian 的高层初始化默认安装 `postgresql`/`postgresql-client` 并启用默认 `postgresql` 服务，使
`pg_isready`、`psql`、`createdb`、`pg_dump` 与 `pg_restore` 可用。项目只使用 `.env` 中的 loopback URL；新服务的
`northstar` 角色不存在时才创建它，空 `POSTGRES_PASSWORD` 会生成并仅写入 `.env`。既有角色、密码、认证规则或服务配置绝不覆盖。
Ubuntu/Debian 以外的 Linux x86_64、非 5432 端口与低层 `dev-postgres` 仍只检查操作者 provision 的服务。
非交互自动化必须显式传入 `--confirm-tool-install YES`。
工作站和部署控制面仅支持 Linux x86_64；受控工具安装仅支持 Ubuntu/Debian。`uv` 使用 `.northstar/` 内的独立 pipx 环境，先用
`pip --target` 安装 pipx，再以该模块创建 `.northstar/bin/uv`；`just` 下载固定官方发布包并校验 SHA-256 后写入 `.northstar/bin`，绝不绕过 PEP 668 的系统 Python 保护；其他发行版会失败关闭。
低层 `--bootstrap-tools` 不安装 Python、不管理 PostgreSQL 服务，也不自动安装部署所需的 `ssh`/`ssh-keygen`。
只想审阅或手工执行工具安装计划时，仍可使用 `python scripts/dev/run_just.py dev-bootstrap` 或 `--bootstrap-tools` 低层入口。

不要在缺少 `uv` 时手工运行 lock 检查或 PEP 517 bootstrap；统一首次入口会在工具就绪后委托本地 `just dev-postgres`，
按既定顺序执行 dependency policy、离线 lock 校验、secret scan 与开发依赖 materialization。需要排障时，仍可显式运行
`python scripts/dev/run_just.py env-bootstrap`、`python scripts/dev/run_just.py dev-setup`，或在工具就绪后执行
`python scripts/dev/run_uv.py run --offline --no-sync python scripts/dev/setup.py --initialize-config`。

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `dev/check_env.py` | 无副作用的工作站检查，报告命令、本机 PostgreSQL 客户端/服务状态与部署工具状态。 |
| `dev/setup.py` | 工具计划、首次工作站引导与项目初始化的显式入口；基础工具需确认，高层 Ubuntu/Debian PostgreSQL 默认安装无需额外参数。 |
| `dev/tool_bootstrap.py` | Ubuntu/Debian 的可审阅安装计划；高层入口可执行受限的原生 PostgreSQL 计划。 |
| `dev/bootstrap_just.py` / `dev/project_tools.py` / `dev/run_uv.py` / `dev/run_just.py` | 下载并校验固定 just 发布包，或仅解析并启动仓库 `.northstar/bin/uv`、`.northstar/bin/just`；不从 `PATH` 回退。 |
| `dev/sync_env_schema.py` | 唯一 `.env` 结构迁移器；通过 stdin 写值且不回显机密。 |
| `ci/check_mypy_baseline.py` | 校验或显式更新版本化 mypy 类型债务基线。 |
| `ci/check_dependency_policy.py` | P9 的标准库离线依赖完整性/来源策略门禁：在任何 `uv` 解析、同步或构建前解析 `pyproject.toml` 与 `uv.lock`，拒绝 source/hash/metadata 或 root dependency 不一致；精确约束 PEP 517 builder group 及唯一 source-only artifact 的 URL/大小/SHA-256，输出稳定、无机密的 inventory digest，不联网且不宣称 CVE 扫描。 |
| `ci/bootstrap_pep517.py` | 唯一允许 materialize Python 包的标准库入口：development 仅在完整输入状态不匹配时创建 non-seed fresh venv，release 始终 fresh；先安装锁定 wheels，再对唯一获准 source artifact 流式校验并在离线/no-index/no-isolation 边界构建它与首方项目。开发与本地质量门禁固定使用 `.northstar/bin/uv` 和仓库缓存，Linux release 使用受控的系统 uv；其后开发命令必须经 `run_uv.py run --offline --no-sync`。 |
| `ci/check_secrets.py` | 扫描全部 tracked 可解码文本（含测试）；有效的 binary magic 才会跳过解码，未知 binary、非 UTF-8、不可读文件与符号链接失败关闭。允许标记只能用于带理由的 canonical disposable test fixture，业务、配置、部署和文档中一律失败关闭。 |
| `ci/check_integrated_candidate.py` | P8 的固定、无 shell 的候选验收测试编排器；`P8_INTELLIGENCE_TO_RESEARCH` 覆盖 P4 v3 immutable projection、same-store source receipt/raw artifact/document/content/span verifier、唯一 context normalized-artifact/full-row-commitment replay、application-owned P1 publishing、P2 hash-only PIT/lineage 与架构边界。`P8_RESEARCH_TO_PORTFOLIO_RISK` 覆盖已具 PASS 证据的具名 `CANDIDATE` Card、独立人工 activation、receipt 与 `StrategyTarget` v2。`P8_EXECUTION_PROVENANCE_PREFLIGHT` 覆盖 activation/portfolio/risk/PIT/account/quote/rule 的重放和纯 application 边界；receipt 永远 non-tradable。`P8_CTP_SIM_CANDIDATE_E2E` 覆盖不透明 final authority、一次性 PostgreSQL consumption、direct/raw bypass refusal、锁内 state/quote 重验、双订单 batch、真实模拟 fill 和只从 simulator 读取的 provenance-aware reconciliation；`Portfolio/Risk→CTP sim` 已由受控 candidate E2E VERIFIED。仅 Data PIT→Research 保持 BLOCKED。它只经 `pytest.main` 重放已列明的安全证据，并在进入 pytest 前拒绝 live/production/真实 broker 环境。 |
| `deploy/{inventory,preflight,package,control_bundle,release_manifest,release_signing,deploy}.py` | Linux x86_64 的非机密清单、预检、制品、canonical manifest、签名与部署控制面。 |
| `deploy/release_gate_bootstrap.py` | 服务器管理员一次性、带外安装固定 root release gate 的工具；绝不是普通部署步骤。 |
| `deploy/gate_release.sh` 与 `deploy/remote/linux/` | 目标端受限控制代码；仅在 root gate 验证签名并解包到 root-owned transaction 后执行，不能通过 SSH 暂存目录直接调用。 |
| `ops/*.py` | 从 Linux x86_64 工作站读取 Linux 目标的健康、日志、诊断和备份证据。 |
| `ops/remote/linux/` | Linux-only 只读运维动作；恢复入口明确失败关闭。 |
| `maintenance/backup_bundle.py` | Linux 上经双重显式确认创建/验证受限逻辑备份包；不属于远程只读 ops 面。 |
| `maintenance/restore_drill.py` | 仅对 loopback `northstar_test` 的真实 PostgreSQL 客户端恢复演练。 |

`build/`、`data/`、`release/`、`maintenance/` 和 `tools/` 留给相应的可审阅工作流。下载数据、真实
备份、账户状态、数据库导出、`.env` 和凭据一律留在仓库外。

数据库保全是所有脚本的共同边界：仓库自动化绝不删除或清空数据库、表、schema 或本机 PostgreSQL 数据目录。
数据库删除或清空只能由用户在仓库自动化之外手动执行；开发、部署和运维路径只允许创建、复用、
升级或读取已明确配置的数据库。

## 部署与运维边界

`python scripts/dev/run_just.py deploy-preview` 与 `deploy.py` 默认都是 dry-run；只有 `--apply`（或 `python scripts/dev/run_just.py deploy-prod /安全路径/release-signing-key`）
才会连接 Linux 目标。每次 `--apply` 都必须显式给出未跟踪的本机 OpenSSH 发布私钥 `--signing-key <path>`；它必须与服务器
管理员带外安装到 `/etc/northstar/release-allowed-signers` 的 `northstar-release` 公钥对应。部署 SSH 身份只负责
传输，不能代替 release 签名 authority。部署清单 `deploy.env` 只允许非机密目标参数；上传唯一活动 `.env`
需要 `--upload-env`，且 production、broker 与真实交易确认会在控制面和目标端重复校验。首次**运行时**安装
额外需要 `--setup-server`，但它不会 bootstrap 或更新 root release gate。

正常发布先读取固定 gate identity，再把已签名 manifest、runtime bundle、control bundle 和可选环境输入经 SSH
stdin 交给 gate。没有 `scp` 到可写远端目录后再 sudo 执行脚本的路径；gate 先验证签名、完整成员索引和固定入口，
然后在 root-owned transaction 中运行控制代码。若 migration 已开始或提交结果未知，自动化保留事务证据并要求
管理员人工恢复；不得重试提交、自动 downgrade 数据库或自动重启旧 release。

Linux systemd、服务、scheduler、worker、目标监控和未来 live trading 都在 Linux x86_64 支持边界内。`health`、
`logs`、`diagnose` 和 `ops backup` 只读；后者只验证独立备份系统留下的无秘密恢复演练证据，不运行
`pg_dump` 或恢复。显式的 `maintenance/backup_bundle.py` 才能创建包，要求服务静止和双重确认；
`maintenance/restore_drill.py` 只允许隔离 `northstar_test`。`restore.sh` 一律失败关闭，生产恢复必须
使用经审批的独立 runbook。

私网 Dashboard 不使用 Caddy 或 `80`/`443`。仅当 `deploy.env` 明确设置
`DASHBOARD_DEPLOY_ENABLED=1` 时，Linux 发布才会管理独立的
`<SYSTEMD_SERVICE_NAME>-dashboard.service`，它固定监听 `127.0.0.1`。私有 ntfy 不属于签名 release：
正常发布必须保持 `NTFY_DEPLOY_ENABLED=0`，其 Docker、DNS、bootstrap 和认证只可走
[运行、配置与部署手册](../docs/OPERATIONS.md)规定的独立 root-operated 工作流。
