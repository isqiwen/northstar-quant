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
        │ SSH（仅部署/远程运维）
        ▼
scripts/*/remote/linux/*.sh         # Linux systemd / 权限 / 发布目标端
```

## 常用命令

| 命令 | 工作站 | 行为 |
| --- | --- | --- |
| `just dev-check` | Windows / Linux | 只读检查 Python、uv、just、Git、Docker/Compose/daemon 与部署工具状态。 |
| `just dev-bootstrap` | Windows / Linux | 仅预览 uv、just、Git 的系统安装计划；不执行安装。 |
| `just dev-bootstrap-docker` | Windows / Linux | 仅预览 Docker + Compose v2 安装计划；不启动 Docker。 |
| `just dev-setup` | Windows / Linux | 创建/迁移本地安全配置并同步锁定依赖；不启动 Docker。 |
| `just dev-postgres` | Windows / Linux（有 Docker） | 显式启动并复用本地 PostgreSQL，创建隔离测试库且只升级至 Alembic head。 |
| `just test-unit` / `just test-backtest` / `just test-cli` | Windows / Linux | 跨平台开发验证。 |
| `just deploy-prod` | Windows / Linux | 显式构建并发布到 Linux 目标；默认 `SERVICE_MODE=health`。 |
| `just ops-health` / `ops-logs` / `ops-diagnose` / `ops-backup` | Windows / Linux | 通过 SSH 读取 Linux 目标状态。 |

首次机器只有 Python 3.11+ 时，不依赖 `uv` 或 `just`，先使用 Python 入口预览工具计划：

```bash
python scripts/dev/setup.py --bootstrap-tools
python scripts/dev/setup.py --bootstrap-tools --install-docker
```

两条命令默认都不会安装、下载、启动 Docker 或接受许可。确认计划后，普通工具安装必须追加
`--apply --confirm-tool-install YES`；Docker 还必须追加 `--confirm-docker-install YES`。Windows 使用
`winget` 计划；Linux 仅正式支持 Ubuntu/Debian，其他发行版会失败关闭。bootstrap 不安装 Python、
不配置 WSL、不修改 Docker 用户组，也不自动安装部署所需的 `ssh`/`scp`。

没有安装 `just` 时，项目初始化仍可使用同一层 Python 入口，例如
`uv run python scripts/dev/setup.py --initialize-config` 或
`uv run python scripts/deploy/deploy.py --inventory deploy.env`。

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `dev/check_env.py` | 无副作用的工作站检查，分别报告命令、Compose v2、daemon 与部署工具状态。 |
| `dev/setup.py` | 工具计划与项目初始化的显式入口；系统安装必须双重确认。 |
| `dev/tool_bootstrap.py` | Windows/Ubuntu/Debian 的可审阅安装计划；默认不执行。 |
| `dev/sync_env_schema.py` | 唯一 `.env` 结构迁移器；通过 stdin 写值且不回显机密。 |
| `db/01_create_test_database.sql` | 首次 Docker PostgreSQL 初始化隔离测试库。 |
| `ci/check_mypy_baseline.py` | 校验或显式更新版本化 mypy 类型债务基线。 |
| `deploy/{inventory,preflight,package,deploy}.py` | 跨平台的非机密清单、预检、制品和部署控制面。 |
| `deploy/remote/linux/` | Linux 目标端的安装、升级、重启、回退和卸载受限包装器。 |
| `ops/*.py` | 从任意开发工作站读取 Linux 目标的健康、日志、诊断和备份证据。 |
| `ops/remote/linux/` | Linux-only 只读运维动作；恢复入口明确失败关闭。 |

`build/`、`data/`、`release/`、`maintenance/` 和 `tools/` 留给相应的可审阅工作流。下载数据、真实
备份、账户状态、数据库导出、`.env` 和凭据一律留在仓库外。

数据库保全是所有脚本的共同边界：仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷。
数据库删除或清空只能由用户在仓库自动化之外手动执行；开发、部署和运维路径只允许创建、复用、
升级或读取已明确配置的数据库。

## 部署与运维边界

`deploy.py` 默认是 dry-run；只有 `--apply`（或 `just deploy-prod`）才会连接 Linux 目标。部署清单
`deploy.env` 只允许非机密目标参数；上传唯一活动 `.env` 需要 `--upload-env`，且 production、broker 与
真实交易确认会在控制面和目标端重复校验。首次目标安装额外需要 `--setup-server`。

Linux systemd、服务、scheduler、worker、目标监控和未来 live trading 不是 Windows 职责。`health`、
`logs`、`diagnose` 和 `backup` 只读；`backup` 只验证独立备份系统留下的无秘密恢复演练证据，不运行
`pg_dump` 或恢复。`restore.sh` 一律失败关闭，生产恢复必须使用经审批的独立 runbook。

私网 Dashboard 不使用 Docker、Caddy 或 `80`/`443`。仅当 `deploy.env` 明确设置
`DASHBOARD_DEPLOY_ENABLED=1` 时，Linux 发布才会管理独立的
`<SYSTEMD_SERVICE_NAME>-dashboard.service`，它固定监听 `127.0.0.1`。可选私有 ntfy 的 Docker、DNS、
bootstrap 和认证要求见[Linux 一键部署](../docs/07_Linux一键部署.md#私有-ntfy可选)。
