# 跨平台开发脚本

`setup.py` 和 `check_env.py` 是 Windows 与 Linux 共用的开发工作站入口。请优先通过
仓库根目录的 `just` 调用它们：

```text
just dev-check
just dev-bootstrap
just dev-bootstrap-docker
just dev-setup
just dev-postgres
```

## 工具 bootstrap

Python 3.11+ 是唯一不会由本脚本安装的前置条件：它只会被检查并给出提示。`uv`、`just`、Git
可由 `--bootstrap-tools` 纳入计划；Docker Desktop（Windows）或 Docker Engine + Compose v2
（Ubuntu/Debian Linux）只在本地 PostgreSQL 和 integration 测试需要时才纳入。

```bash
# 默认只显示命令，不安装、不联网、不启动 Docker。
python scripts/dev/setup.py --bootstrap-tools

# Docker 必须额外显式加入计划，默认仍不执行。
python scripts/dev/setup.py --bootstrap-tools --install-docker
```

执行系统安装必须由操作者审阅计划后手动追加精确的 `YES`：

```bash
python scripts/dev/setup.py --bootstrap-tools --apply --confirm-tool-install YES
python scripts/dev/setup.py --bootstrap-tools --install-docker --apply --confirm-tool-install YES --confirm-docker-install YES
```

Windows 计划使用 `winget`；Linux 仅正式支持 Ubuntu/Debian，其他发行版会失败关闭而不执行命令。
bootstrap 不自动安装 Python、接受 Docker Desktop 许可、配置 WSL、启动服务或执行 `usermod`。`ssh`/`ssh-keygen`
只供部署控制面使用，环境检查会报告它们，但不会自动安装。

## 幂等性与中断恢复

相同命令可以安全重复执行。已在 PATH 中的工具不会再次纳入安装计划；Docker Linux 源与 keyring
若和本工具的预期状态完全一致，会复用并只补齐缺失软件包。source 已写入、keyring 尚未写入的中断状态
可以恢复；未知 keyring、符号链接或不同的系统源一律失败关闭，不会覆盖。

`just dev-setup` 在 `.env` 与 `configs/app.yaml` 已符合目标状态时不改写它们、不生成新密码，也不会额外
创建 `.env` 备份。`just dev-postgres` 始终使用固定 Compose 项目 `northstar-quant`，重复执行只复用本地
容器、卷、数据库和 Alembic head。若检测到保留的数据卷但 `.env` 缺少 `POSTGRES_PASSWORD`，流程会停止；
请恢复原密码。如确需删除或清空，只有用户可在仓库自动化之外手动操作数据库或卷；初始化器绝不会
删除卷或猜测密码。

仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷。数据库删除或清空只能由用户在仓库
自动化之外手动执行；`dev-postgres` 仅启动/复用已有本地服务并执行非破坏性的 Alembic 升级。

开发期迁移已压缩为唯一完整基线 `0001_current_schema_baseline`，不支持旧 revision 的就地升级。若已保留的本地卷记录了
其他 `alembic_version`，`dev-postgres` 会失败关闭；操作者必须在仓库自动化之外手动重建该本地开发数据库或卷，随后再运行
`just dev-postgres`。脚本绝不会自动 reset、stamp、drop、truncate 或删除卷。

## 项目初始化

先运行 `just env-bootstrap`，它在全新 `.venv` 中仅 materialize 已审计的锁定构建输入；后续
`dev-setup` 显式创建本地活动配置、生成不回显的本地数据库密码并固定安全交易开关；
`dev-postgres` 才会启动 Docker PostgreSQL 并运行迁移。两者都会保持 `paper`、禁用实盘和 kill switch，绝不会下载
市场数据、启动 scheduler 或提交订单。已有疑似生产、非 paper、live、kill-switch 或外部数据库 `.env`
会被拒绝覆盖；确认它只是本地开发文件后，才可手动追加
`--confirm-reset-local-dev-config YES`。

`sync_env_schema.py` 是内部的无秘密 `.env` schema 迁移工具；它通过 stdin 接收待写入值，避免把
密码或数据库 URL 放入进程参数或终端输出；CLI 与函数入口都会拒绝活动 `.env` 符号链接。
