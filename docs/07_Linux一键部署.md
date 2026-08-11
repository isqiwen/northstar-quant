# Linux 一键部署

## 适用范围

`scripts/deploy.sh` 用于从 macOS 或 Linux 开发机向 Ubuntu/Debian
服务器发布 Northstar Quant。它负责本地质量检查、构建源码制品、SSH 上传、运行时安装、
依赖同步、数据库迁移、systemd 配置、健康检查和应用版本回退。

它不负责创建生产 PostgreSQL、配置云防火墙、购买服务器或申请券商权限。生产数据库应
独立部署并具备自动备份，不能复用开发环境的 Docker 数据卷。

当前仓库没有 production 画像，也没有完整 CTP 报单适配器。因此当前唯一可用的部署模式是
`SERVICE_MODE=health`，并且必须保持 `NORTHSTAR_BROKER=paper` 和
`NORTHSTAR_LIVE_TRADING_ENABLED=false`。

## 服务器要求

- Ubuntu 或 Debian 64 位服务器。
- 可通过 SSH 密钥登录。
- SSH 用户可无交互执行 `sudo -n true`。
- 服务器可以访问 Python 包源、Astral uv 下载地址和生产 PostgreSQL。
- PostgreSQL 已创建 `northstar` 数据库和最小权限应用用户。
- 系统启用 NTP 时间同步。

部署前检查：

```bash
ssh deploy@example.com 'uname -s && sudo -n true'
```

## 本地配置

部署目标使用不含密钥的 `deploy.env`：

```bash
cp deploy.env.example deploy.env
```

至少修改：

```text
DEPLOY_HOST=deploy@example.com
SERVICE_MODE=health
```

`deploy.env` 还是运行时可写目录的唯一部署配置来源。默认所有目录位于
`/srv/northstar/northstar-quant/shared/`；若要使用独立数据盘，显式设置下列绝对路径，
而不是修改发布版本目录：

```text
RUNTIME_STORAGE_DIR=/mnt/northstar-quant/storage
RUNTIME_DOWNLOADS_DIR=/mnt/northstar-quant/storage/downloads
RUNTIME_REPORTS_DIR=/mnt/northstar-quant/reports
RUNTIME_LOG_DIR=/var/log/northstar-quant
RUNTIME_CACHE_DIR=/var/cache/northstar-quant
RUNTIME_MATPLOTLIB_DIR=/var/cache/northstar-quant/matplotlib
```

这些字段在示例配置中默认留空：脚本会从 `SERVICE_HOME` 和 `APP_NAME` 派生原有目录；只设置
`RUNTIME_STORAGE_DIR` 并继续留空 `RUNTIME_DOWNLOADS_DIR` 时，下载目录自动派生为
`<RUNTIME_STORAGE_DIR>/downloads`。其余非空字段均按指定路径生效。

部署脚本只接受 `/srv`、`/var/lib`、`/var/cache`、`/var/log`、`/mnt` 或 `/data` 下的路径，
拒绝相对路径和含 `.`、`..` 路径段的值。首次安装和后续发布都会以服务用户创建这些目录，
权限为 `0750`。部署脚本会以制品内的 `configs/app.example.yaml` 为模板，将已校验的四个业务输出
路径原子写入**待发布版本**完整的 `configs/app.yaml`；发布前迁移和健康检查使用这份配置，失败时
随 stage 清理，旧版本的配置不会变化。应用通过此 YAML 读取 `storage`、`downloads`、`reports` 和 `logs`，而 systemd
仅用 `RUNTIME_*_DIR` 设置可写白名单和缓存目录。切换 `current` 前脚本会先停止旧服务，避免
旧进程在重载配置时读取新版本路径。不要通过手工软链接或在活动 `.env` 重复设置这四个
`NORTHSTAR_*_DIR` 绕过该边界。

生成的活动文件始终与示例具有完全相同的字段。当前部署只从 `deploy.env` 注入四个运行目录；
其余非敏感应用规则（例如日志轮转）随已审阅的 `configs/app.example.yaml` 版本冻结。不要在服务器
上事后手改 release 内的 `app.yaml`；需要变更通用规则时，应修改模板、测试并重新发布。

生产密钥和数据库 URL 使用唯一活动文件 `.env`。请在**独立的部署工作树**中创建它，不要把开发
工作树中 `NORTHSTAR_ENV=dev` 的 `.env` 拿来上传：

```bash
cp .env.example .env
chmod 600 .env
```

必须把 `NORTHSTAR_ENV` 设为 `production`，并替换 `NORTHSTAR_DATABASE_URL` 中的 `CHANGE_ME`。
部署脚本会在上传前先校验 `.env.example` 的完整字段结构，再验证这两个条件；若缺字段、仍是开发值或
不安全的 broker/live 组合，都会失败关闭。`.env` 只保存数据库 URL、令牌等敏感运行时变量；业务输出目录以 `deploy.env` 为唯一部署来源，并由发布过程
在每个新版本生成完整的 `configs/app.yaml`。`configs/app.yaml`、`.env` 与 `deploy.env` 均被 Git
忽略；不要把密码、令牌或数据库 URL 写入 `deploy.env`。

首次发布使用 `UPLOAD_ENV=1` 将部署工作树中的 `.env` 安装为服务器
`/srv/northstar/northstar-quant/shared/.env`；每个 release 只读这个同名活动文件。后续如需更新
环境变量，仍显式设置 `UPLOAD_ENV=1`。自动化场景可用 `ENV_FILE=/安全路径/.env` 指定上传来源，
但文件名与字段结构仍应与 `.env.example` 一致，且必须通过 `NORTHSTAR_ENV=production` 门禁。

无需为 Paper broker 设置固定状态文件路径：默认状态按账户写入
`<runtime.storage_dir>/brokers/paper/<NORTHSTAR_PAPER_ACCOUNT>/state.json`。这保证部署
切换到独立数据盘时，Paper 状态仍与数据目录一起迁移；只有明确的迁移或恢复场景才应在
活动 `.env` 中取消注释 `NORTHSTAR_PAPER_STATE_PATH`。

## 部署命令

先做不连接服务器的完整构建演练：

```bash
DRY_RUN=1 scripts/deploy.sh
```

首次部署需要安装服务器运行时并上传活动 `.env`：

```bash
UPLOAD_ENV=1 SETUP_SERVER=1 scripts/deploy.sh
```

后续普通发布只需要：

```bash
scripts/deploy.sh
```

更新服务器环境变量：

```bash
UPLOAD_ENV=1 scripts/deploy.sh
```

正常发布会拒绝未提交工作区，并在上传前执行：

```bash
uv run ruff check .
uv run pytest
```

`ALLOW_DIRTY=1`、`SKIP_RUFF=1` 和 `SKIP_TESTS=1` 仅用于明确的诊断场景，
不应作为日常发布配置。

## 服务器目录

默认目录结构如下；若设置了 `RUNTIME_*_DIR`，可写的 `storage`、`reports`、`logs`、
`cache` 和 `matplotlib` 可位于独立路径，不必在 `shared/` 下。

```text
/srv/northstar/northstar-quant/
├── current -> releases/<revision-timestamp>
├── releases/
│   ├── <old-release>/
│   └── <current-release>/
└── shared/
    ├── .env
    ├── cache/
    ├── logs/
    ├── matplotlib/
    ├── python/
    ├── reports/
    ├── storage/
    └── uv-cache/
```

每个版本拥有独立 `.venv`，并在安装时生成自身完整的 `configs/app.yaml`；生产 `.env` 与
`python`、`uv-cache` 保存在 `shared/`。`storage`、`reports`、`logs` 均通过版本目录软链接指向
配置的运行时目录。依赖通过远端 `uv sync --frozen --no-dev --no-editable` 从 `uv.lock` 安装。

## 服务模式

### health

默认模式。systemd 以 oneshot 服务运行：

```bash
northstar health
```

该模式会迁移和验证应用，但不会启动 Dashboard、调度器或任何交易执行流程。

### scheduler

这是**未来** production 阶段的 systemd 模板，不是当前可启动服务。当前没有 production
画像，执行下列命令会在画像/preflight 阶段失败关闭：

```bash
northstar live scheduler
```

只有在生产画像、真实 CTP adapter、经授权数据、完整 preflight 和阶段审批全部完成后，
才可评估 scheduler 模式。届时 paper 调度器仍要求：

```text
NORTHSTAR_BROKER=paper
NORTHSTAR_LIVE_TRADING_ENABLED=false
```

非 paper 调度器除了生产画像、券商适配器和应用 preflight，还要求每次发布显式执行：

```bash
CONFIRM_LIVE_DEPLOY=YES scripts/deploy.sh
```

这个确认只表示允许部署脚本启动非 paper 调度器，不替代交易系统自身的 preflight、
kill switch、账户核验和风控。

## 回退与数据库迁移

新版本会先在临时版本目录中安装依赖、执行 Alembic 迁移和健康检查，然后原子切换
`current`。systemd 启动失败时，脚本会恢复上一版本并重新启动服务。

数据库迁移不会自动降级。当前项目尚未建立生产基线，也没有需要保留的生产业务数据，
因此开发阶段不承诺旧 schema 兼容；模型、迁移、测试和本地数据库应作为同一变更整体更新。
首次正式发布后，才切换为先扩展、后清理的兼容迁移策略。正式发布前必须备份生产数据库，
并定期演练恢复。

默认保留最近五个版本，且至少保留两个版本。

## 运维命令

查看状态：

```bash
ssh deploy@example.com 'sudo systemctl status northstar-quant --no-pager'
```

查看日志：

```bash
ssh deploy@example.com \
  'sudo journalctl -u northstar-quant -n 100 --no-pager'
```

停止或启动：

```bash
ssh deploy@example.com 'sudo systemctl stop northstar-quant'
ssh deploy@example.com 'sudo systemctl start northstar-quant'
```

查看当前版本：

```bash
ssh deploy@example.com \
  'readlink -f /srv/northstar/northstar-quant/current'
```

Dashboard 不属于默认 systemd 服务。确需远程查看时，应监听 `127.0.0.1` 并通过
SSH 隧道或 VPN 访问，不直接暴露公网。
