# 跨平台开发脚本

`setup.py` 和 `check_env.py` 是 Windows 与 Linux 共用的开发工作站入口。工具已就绪后，请通过
仓库根目录的 `run_just.py` 调用仓库本地 `just`：

```text
python scripts/dev/run_just.py dev-check
python scripts/dev/run_just.py dev-bootstrap
python scripts/dev/run_just.py setup
python scripts/dev/run_just.py dev-setup
python scripts/dev/run_just.py dev-postgres
```

首次机器尚未安装 `uv` 或 `just` 时，直接运行：

```bash
python scripts/dev/setup.py --initialize-workstation
```

它会先展示缺失工具的计划，只有在交互终端输入 `YES` 后才安装。`uv`、`just` 及其 bootstrap 依赖只写入仓库未跟踪的
`.northstar/`，项目通过固定路径调用它们，不修改 `PATH`，也不需要重启终端。安装完成后同一次入口会立即继续；
仅当刚安装的宿主机 Git 在当前进程仍不可见时，才提示重新打开终端后再次运行。`setup` recipe 使用相同的首次
引导逻辑。低层 `--bootstrap-tools` 命令保留给只预览或人工排障的场景。

## 工具 bootstrap

Python 3.11+ 是不会由本脚本安装的前置条件。Ubuntu/Debian 的高层
`python scripts/dev/setup.py --initialize-workstation` 会默认安装 `postgresql`/`postgresql-client`，并启用默认
`postgresql` 服务，从而提供与服务端 major 对应的 `pg_isready`、`psql`、`createdb`、`pg_dump` 与 `pg_restore`。
它只管理 `127.0.0.1:5432` 的默认服务，不编辑 PostgreSQL 配置、认证规则或数据目录。

```bash
# 默认只显示命令，不安装、不联网、不管理 PostgreSQL 服务。
python scripts/dev/setup.py --bootstrap-tools
```

执行开发工具安装必须由操作者审阅计划后手动追加精确的 `YES`：

```bash
python scripts/dev/setup.py --bootstrap-tools --apply --confirm-tool-install YES
```

Windows 计划只使用 `winget` 安装 Git；Linux 仅正式支持 Ubuntu/Debian。`uv` 使用仓库 `.northstar/` 内由当前 Python 以
`pip --target` 安装的 pipx：pipx 模块、虚拟环境、缓存、状态和 `bin/uv` 均留在该目录。项目通过
`python scripts/dev/run_uv.py` 固定解析该路径；`just` 下载固定官方发布包、校验 SHA-256 并写入 `bin/just` 或 `bin/just.exe`，由
`python scripts/dev/run_just.py` 固定解析。两者不修改当前用户 `PATH`，也不会绕过 PEP 668 系统 Python 保护。其他发行版会失败关闭而不执行命令。
低层 `--bootstrap-tools` 不自动安装 Python、配置 WSL、创建数据库角色、启动服务或执行 `usermod`。`ssh`/`ssh-keygen` 只供部署控制面使用，
环境检查会报告它们，但不会自动安装。

标准初始化只接受 `.env` 中的 loopback PostgreSQL URL。新服务上若本机 `northstar` 角色不存在，首次入口才创建最小的
`LOGIN CREATEDB` 角色；空 `POSTGRES_PASSWORD` 会生成随机值并仅写入未跟踪的 `.env`。已有角色、密码或认证规则不会被修改；
若已有角色存在而密码缺失或无效，初始化会失败关闭，请填写匹配凭据。服务不可达、客户端工具缺失、认证/数据库创建权限不足或连接地址不安全时，
初始化会在迁移前失败关闭；它不会重置服务、覆盖既有凭据或回退到文件/SQLite 数据库。

## 幂等性与中断恢复

相同命令可以安全重复执行。已存在且通过 `.northstar/bin/uv` 或 `.northstar/bin/just` 验证的工具不会再次纳入安装计划；Git 仍按宿主机
命令检查。高层初始化只在客户端缺失或默认服务未就绪时运行受限 PostgreSQL 安装/启用计划；服务状态、认证或可达性未知时，初始化一律失败关闭，
不会覆盖系统配置、角色、密码或数据库数据。

`env-bootstrap` 只在 `.venv` 缺失、状态文件缺失/损坏、锁文件、项目声明、Python、uv 或 bootstrap 输入变化，或离线健康检查失败时才创建
fresh staging venv。正常重复运行会复用 `.venv`；wheel 缓存固定在 `.northstar/cache/uv`，唯一 source-only 制品缓存于
`.northstar/cache/source-artifacts`，每次使用前都重新校验大小与 SHA-256。需要显式重建时使用
`python scripts/dev/run_just.py env-bootstrap-refresh`，它不会复用旧 `.venv`。构建在原子提升 `.venv` 前失败时会删除本次
staging venv；只有提升本身无法安全恢复时才保留目录供诊断。`.vscode/settings.json` 会隐藏 `.venv`、`.northstar` 和这些
staging/previous 目录，并排除它们的文件监听。

`setup` 和底层 `dev-setup` recipe 在 `.env` 与 `configs/app.yaml` 已符合目标状态时不改写它们。高层 `setup` 只在缺失
`northstar` 角色且 `.env` 密码为空时生成一次本地密码；既有角色/密码永不覆盖，也不会额外创建 `.env` 备份。高层 `setup` 和底层
`dev-postgres` recipe 用 `pg_isready` 与 `psql` 验证本机服务，再以 `createdb` 创建或复用 `northstar` 与 `northstar_test`，
重复执行只复用数据库和 Alembic head。若 `.env` 密码无法认证，流程会停止；如确需删除或清空，只有用户可在仓库自动化之外手动操作数据库；
初始化器绝不会删除数据库或猜测/覆盖既有密码。

仓库自动化绝不删除或清空数据库、表、schema 或本机 PostgreSQL 数据目录。数据库删除或清空只能由用户在仓库
自动化之外手动执行；`dev-postgres` recipe 仅验证/复用已有本地服务并执行非破坏性的 Alembic 升级。

开发期迁移已压缩为唯一完整基线 `0001_current_schema_baseline`，不支持旧 revision 的就地升级。若已保留的本地数据库记录了
其他 `alembic_version`，或 revision 名称相同但 schema 早于当前完整基线，`dev-postgres` 会失败关闭；操作者必须在仓库自动化之外
手动重建该本地开发数据库，随后再运行 `python scripts/dev/run_just.py dev-postgres`。脚本绝不会自动 reset、stamp、drop、truncate 或删除数据库。

## 项目初始化

日常本机初始化使用 `python scripts/dev/setup.py --initialize-workstation`。它会先确认基础工具；缺少可 bootstrap 工具时展示计划并要求输入 `YES`，
随后重新检查并在可定位时继续同一次初始化。Ubuntu/Debian 上缺少 PostgreSQL 客户端或默认服务未就绪时，它会默认安装/启用该本机服务；
工具就绪后运行 `env-bootstrap`，复用状态匹配的 `.venv` 或在必要时从已审计的锁定构建输入创建全新环境，再验证本地活动配置、固定安全交易开关、
创建仅缺失的开发角色/数据库并运行前向迁移。它始终保持 `paper`、禁用实盘和 kill switch，绝不会下载市场数据、启动 scheduler 或提交订单。
已有疑似生产、非 paper、live、kill-switch 或外部数据库 `.env`
会被拒绝覆盖；确认它只是本地开发文件后，才可手动追加
`--confirm-reset-local-dev-config YES`。

`sync_env_schema.py` 是内部的无秘密 `.env` schema 迁移工具；它通过 stdin 接收待写入值，避免把
密码或数据库 URL 放入进程参数或终端输出；CLI 与函数入口都会拒绝活动 `.env` 符号链接。
