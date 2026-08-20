# Linux 一键部署

## 适用范围

`just deploy-prod`（或 `scripts/deploy/deploy.py`）可从 Windows 或 Linux 开发机向 Ubuntu/Debian
服务器发布 Northstar Quant。Just 只路由命令，Python 控制面负责本地预检、构建源码制品与 SSH 编排；
Linux 目标端才负责运行时安装、依赖同步、数据库迁移、systemd 配置、健康检查和应用版本回退。Windows
不需要也不应运行 systemd、服务、scheduler 或未来 live trading。

systemd 模板位于仓库级的 `infra/systemd/`；发布流程会将它们与 `scripts/deploy/` 部署模块一并临时上传，
运行时状态、真实数据和备份不会进入部署制品。

当 `NTFY_DEPLOY_ENABLED=1` 时，它还会部署项目专用的私有 ntfy 与 Caddy TLS 反向代理，
并在明确请求时执行一次身份初始化。ntfy 是可选的即时告警基础设施，不参与下单、撤单、
风控判断或 kill switch；告警服务不可用不能改变交易或风控的既有结果。

它不负责创建生产 PostgreSQL、配置云防火墙、购买服务器或申请券商权限。生产数据库应
独立部署并具备自动备份，不能复用开发环境的 Docker 数据卷。它也**不会安装 Docker**；
启用私有 ntfy 前，远程服务器必须由运维人员预装 Docker Engine 与 Docker Compose。

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
- 如启用私有 ntfy：远程服务器已安装并可由 `sudo` 调用 Docker 与 Docker Compose；部署脚本不安装它们。
- 如启用私有 ntfy：`NTFY_PUBLIC_HOST` 的 A/AAAA 记录已解析到该服务器，TCP `80`/`443` 对公网开放，
  且没有被其他 Web 服务占用；Caddy 需要它们申请和续期 ACME TLS 证书。

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

首次发布使用 Python 控制面的 `--upload-env` 将部署工作树中的 `.env` 安装为服务器
`/srv/northstar/northstar-quant/shared/.env`；每个 release 只读这个同名活动文件。后续如需更新
环境变量，仍显式使用 `--upload-env`。自动化场景可用 `--env-file /安全路径/.env` 指定上传来源，
但文件名与字段结构仍应与 `.env.example` 一致，且必须通过 `NORTHSTAR_ENV=production` 门禁。

无需为 Paper broker 设置固定状态文件路径：默认状态按账户写入
`<runtime.storage_dir>/brokers/paper/<NORTHSTAR_PAPER_ACCOUNT>/state.json`。这保证部署
切换到独立数据盘时，Paper 状态仍与数据目录一起迁移；只有明确的迁移或恢复场景才应在
活动 `.env` 中取消注释 `NORTHSTAR_PAPER_STATE_PATH`。

## 私有 ntfy（可选）

私有 ntfy 是本项目唯一的外部即时告警服务。它与 Northstar 应用、报告邮件和本地日志职责不同：
应用 `.env` 只保存向 ntfy 发布所需的地址、主题和发布令牌；`deploy.env` 只保存非敏感基础设施参数；
一次性用户口令只保存在未跟踪的 bootstrap 文件。即时告警的内容、审计边界和应用侧变量见
[报告、PDF 与通知](05_报告_PDF与通知.md)，本节仅说明 Linux 服务部署。

在 `deploy.env` 中配置以下字段；默认 `0` 表示完全不部署也不修改 ntfy：

| 字段 | 示例/默认值 | 含义 |
| --- | --- | --- |
| `NTFY_DEPLOY_ENABLED` | `0` | 设为 `1` 才启用私有 ntfy 部署。 |
| `NTFY_PUBLIC_HOST` | `ntfy.example.com` | 启用时必填的公网 HTTPS 域名；只能填写主机名，不含协议、端口或路径。 |
| `NTFY_ACME_EMAIL` | `ops@example.com` | 启用时必填，供 Caddy/ACME 证书通知使用。 |
| `NTFY_IMAGE` | `binwiederhier/ntfy:v2.27.0` | ntfy 固定镜像标签；升级前先在测试环境验证，禁止改成 `latest`。 |
| `NTFY_CADDY_IMAGE` | `caddy:2.10.2-alpine` | Caddy 固定镜像标签；负责 TLS 终止与反向代理。 |
| `NTFY_CONFIG_DIR` | `/etc/northstar-ntfy` | 受控服务端配置目录。 |
| `NTFY_DATA_DIR` | `/var/lib/northstar-ntfy` | 持久化认证、消息缓存与证书数据的目录；不属于 release。 |
| `NTFY_CACHE_DURATION` | `24h` | 告警缓存保留期；应按最小必要原则设置。 |

示例：

```bash
cp deploy.env.example deploy.env
# 编辑 deploy.env：填写部署目标，并设置以下非敏感参数
# NTFY_DEPLOY_ENABLED=1
# NTFY_PUBLIC_HOST=ntfy.example.com
# NTFY_ACME_EMAIL=ops@example.com
```

`NTFY_CONFIG_DIR` 和 `NTFY_DATA_DIR` 是服务器路径，必须避免指向发布版本目录。不要手工删除
`NTFY_DATA_DIR`、容器卷或认证数据库；这会使现有 topic、用户和令牌失效。部署脚本以严格私有模式
运行 ntfy：匿名访问默认拒绝，Caddy 提供 HTTPS，ntfy 仅在反向代理之后接受请求。

### 首次身份初始化

复制受版本控制的模板后填写真实值：

```bash
cp ntfy.bootstrap.env.example ntfy.bootstrap.env
chmod 600 ntfy.bootstrap.env
```

`ntfy.bootstrap.env` 仅含以下四个字段，均必须使用单独的高强度值：

```text
NTFY_ADMIN_USERNAME=
NTFY_ADMIN_PASSWORD=
NTFY_READER_USERNAME=
NTFY_READER_PASSWORD=
```

该文件被 Git 忽略，不能放入制品、工单、命令行历史、CI 日志或聊天记录。默认文件位置是项目根目录的
`ntfy.bootstrap.env`；若使用受控密钥路径，使用 `NTFY_BOOTSTRAP_FILE=/安全路径/ntfy.bootstrap.env`
覆盖。bootstrap 不保存发布 topic 或 token；它们必须先由操作者填写在唯一活动应用 `.env` 中：

```dotenv
NORTHSTAR_ALERT_MODE=ntfy
NORTHSTAR_NTFY_BASE_URL=https://ntfy.example.com
NORTHSTAR_NTFY_TOPIC=
NORTHSTAR_NTFY_TOKEN=
```

主题应使用随机且不可猜测的安全名称；发布 token 必须是 `tk_` 加 29 位字母数字，且只属于
`northstar-publisher` 非管理员身份。可在受控本机终端生成一次后，立即复制到 `.env`：

```bash
uv run python -c "import secrets; print('nq_' + secrets.token_hex(16))"
uv run python -c "import secrets, string; print('tk_' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(29)))"
```

第二条命令的输出是秘密；不要把输出写入命令参数、Shell 历史、截图、CI 日志或聊天记录。
`NORTHSTAR_NTFY_BASE_URL` 必须与 `NTFY_PUBLIC_HOST` 对应。部署过程会拒绝缺失或格式不安全的
topic/token，不会生成、打印、回写或替换本地 `.env` 中的发布令牌。
首次启动需要同时上传应用 `.env` 与 bootstrap 文件。跨平台 Python 控制面要求三个显式开关：
`--upload-env`、`--upload-ntfy-bootstrap` 与 `--confirm-ntfy-bootstrap YES`；不要在 Windows PowerShell
或 CI 日志中保留 bootstrap 凭据。

```bash
uv run python scripts/deploy/deploy.py \
  --inventory deploy.env --apply --setup-server --upload-env \
  --upload-ntfy-bootstrap --confirm-ntfy-bootstrap YES
```

`--setup-server` 只安装 Northstar 的 Python/systemd 运行时，**不安装 Docker**。首次命令之前须确认
Docker、Docker Compose、DNS 和 `80`/`443` 前置条件均已满足。bootstrap 只可用于首次身份初始化或
经过审批的身份维护；普通版本发布不得设置它。

普通发布不会重置 ntfy 认证数据、管理员/订阅者口令或发布令牌。需要轮换身份时，先备份
`NTFY_DATA_DIR`，制定恢复方案，并显式使用受控的 bootstrap/维护流程；不要通过删除数据目录或修改
release 文件“重置”。

身份和权限必须至少分为三类：

- 管理员：只用于人工维护 ntfy 用户、ACL 和令牌，绝不提供给 Northstar 或手机。
- 订阅者：手机使用的独立只读身份，只能读取指定告警 topic，不能伪造告警。
- 发布者：Northstar 专用的非管理员身份，只能向指定告警 topic 写入。应用只使用其发布令牌，令牌只保存在
  生产 `.env` 的 `NORTHSTAR_NTFY_TOKEN` 中。

ntfy token 的权限继承其所属用户账户；因此“发布令牌”不是管理员令牌的缩小版本。发布用户本身必须
仅拥有指定 topic 的写权限，不能读取消息、管理其他用户或访问其他 topic。

### 手机可达性与验收

服务部署成功不等于手机一定及时收到告警。Android 自建 ntfy 的即时送达依赖客户端保持前台订阅服务；
iOS 在不使用上游推送服务的严格私有配置下可能延迟数分钟甚至更久。默认部署不把消息转发到公共
`ntfy.sh`，也不承诺移动网络、厂商省电策略或推送系统的送达时效。不得把 ntfy 消息视为交易控制、
人工确认或唯一审计证据。

首次验收至少确认：Caddy 的 HTTPS 证书有效、`https://<NTFY_PUBLIC_HOST>/v1/health` 返回健康状态、
发布者可以发布、匿名访问被拒绝、订阅者不能发布，并在目标手机上完成实际订阅测试。健康接口只说明
服务状态，不证明 topic ACL、令牌或手机送达正常。

## 部署命令

先做不连接服务器的完整构建演练（这是默认行为）：

```bash
uv run python scripts/deploy/deploy.py --inventory deploy.env
```

首次部署需要安装服务器运行时并上传活动 `.env`：

```bash
uv run python scripts/deploy/deploy.py \
  --inventory deploy.env --apply --setup-server --upload-env
```

后续普通发布只需要：

```bash
just deploy-prod
```

更新服务器环境变量：

```bash
just deploy-prod-with-env
```

正常发布会拒绝未提交工作区，并在上传前执行：

```bash
uv run ruff check .
uv run pytest
```

`--allow-dirty`、`--skip-ruff` 和 `--skip-tests` 仅用于明确的诊断场景，
不应作为日常发布配置。`--apply` 之前的 Python 控制面不会建立 SSH 连接；目标 Linux 后端在每次
真正发布时会再次校验同等安全门禁。

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

默认配置下，该模式会迁移和验证应用，但不会启动 Dashboard、调度器或任何交易执行流程。

### 私网 Dashboard（可选）

Dashboard 是只读观察界面，不是交易许可、鉴权门户或公网 Web 服务。默认
`DASHBOARD_DEPLOY_ENABLED=0`；只有在 `deploy.env` 显式设为 `1` 时，发布流程才会
管理独立的 `<SYSTEMD_SERVICE_NAME>-dashboard.service`。它不会改变
`SERVICE_MODE=health` 或 `SERVICE_MODE=scheduler` 的含义，也不会替换这两个主服务。

该服务固定监听 `127.0.0.1`，应用也会拒绝任何其他
`NORTHSTAR_DASHBOARD_HOST` 值；不配置 Caddy、Docker、反向代理或 `80`/`443` 端口。远程查看使用
SSH 隧道或受控 VPN，例如默认端口：

```bash
ssh -N -L 8501:127.0.0.1:8501 deploy@example.com
```

然后在本机浏览器打开 `http://127.0.0.1:8501`。如修改
`NORTHSTAR_DASHBOARD_PORT`，隧道两侧端口必须同步调整。验收时至少确认服务器监听地址仅为
loopback、Dashboard systemd 服务正常，以及公网防火墙没有放行该端口。

发布主服务成功后才会更新 Dashboard。若 Dashboard 自身启动失败，部署会强制关闭并移除该可选服务，
主服务仍保持本次已验证的发布版本，远端输出会明确为
`dashboard=disabled_after_failure`；若连“已关闭”都无法确认，部署会失败并要求立即人工检查。
当前 Dashboard 会显示账户、订单、成交、报告路径与行情摘要，且没有登录、角色控制或独立只读数据库
身份，因此绝不能面向公网或共享给不受信任的用户。

### scheduler

这是**未来** production 阶段的 `infra/systemd/scheduler.service.in` 模板，不是当前可启动服务。当前没有 production
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
just deploy-prod-live
```

这个确认只表示允许部署脚本启动非 paper 调度器，不替代交易系统自身的 preflight、
kill switch、账户核验和风控。

## 回退与数据库迁移

新版本会先在临时版本目录中安装依赖、执行 Alembic 迁移和健康检查，然后原子切换
`current`。systemd 启动失败时，脚本会恢复上一版本并重新启动服务。

数据库迁移只允许前向、加法式升级；`downgrade()` 会失败关闭，部署绝不自动回退数据库 schema、
删除或清空数据。即使仍处于开发阶段，模型、迁移、测试与文档也必须作为同一变更整体更新，不能以
重建、清空或重新初始化本地数据库替代兼容性设计。正式发布前必须备份生产数据库，并定期演练恢复。
仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷；生产数据库的删除或清空只能由用户
在仓库自动化之外手动执行。仓库内的 `infra/backup/northstar-quant/` 只保存策略和演练说明，绝不保存
真实备份。

默认保留最近五个版本，且至少保留两个版本。

### PostgreSQL 备份与恢复证据

本部署脚本**不会**创建 PostgreSQL 备份、安装对象存储客户端、配置 WAL 归档，也不会把数据库凭据
交给应用以外的服务。生产 PostgreSQL 必须由独立、最小权限的备份系统负责；开发 Docker 数据卷、
`pg_dump` 文件留在同一台服务器或“任务执行成功”都不能视为可恢复的备份。

第一阶段只提供诚实的观测门禁。`configs/maintenance/database_backup_readiness.yaml` 默认关闭；启用后，
独立运维流程须在以下位置写入**无秘密**证据：

```text
<runtime.storage_dir>/operations/database-backup/readiness.json
```

证据必须记录一个逻辑备份制品的 UUID、SHA-256、大小、完成 UTC 时间，以及对**同一制品**执行隔离
PostgreSQL 恢复演练的完成 UTC 时间、`passed` 状态和方法名。它不能包含 DSN、主机、路径、对象存储
桶、账户、令牌或密码。不要为了消除告警手工伪造该文件：应用只能验证证据结构和时效，无法独立验证
外部介质或恢复目标，所以完整证据仍只显示 `warn`，永远不会显示 `pass`。

```bash
# 仅查看证据，不会备份、恢复或修改任何文件。
sudo -u northstar /srv/northstar/northstar-quant/current/.venv/bin/northstar \
  ops backup status

# 发布预检和 health systemd 服务均使用此门禁：只有 blocked 才返回退出码 2。
sudo -u northstar /srv/northstar/northstar-quant/current/.venv/bin/northstar \
  health --fail-on-blocked
```

启用策略后，证据缺失、损坏、过期、指向不同制品或恢复演练失败会使 health 为 `blocked`，从而阻断
后续发布。策略关闭时结果为 `skipped`，明确表示“未检查”，而不是“备份正常”。这项能力只覆盖
PostgreSQL 备份/恢复就绪；Paper/`ctp_sim` 状态、正式报告与依法允许复制的行情数据仍需分别纳入
经授权的备份范围。

在进入真实 CTP 或实盘前，必须升级为独立故障域的加密备份、PITR（基础备份加 WAL 归档）、书面
RPO/RTO、备份新鲜度告警和至少定期的隔离恢复演练。不能仅依赖本项目的证据检查作为灾难恢复方案。

## 运维命令

从 Windows 或 Linux 工作站读取状态：

```bash
just ops-health
```

读取日志（默认最后 200 行）：

```bash
just ops-logs
```

收集只读诊断或备份/恢复演练证据：

```bash
just ops-diagnose
just ops-backup
```

这些命令通过受限 SSH 调用 Linux 目标脚本；它们不上传 `.env`、不启动服务，也不创建数据库备份。
`backup` 只读取独立备份系统留下的无秘密就绪证据。服务重启需要目标端
`CONFIRM_SERVICE_RESTART=YES`，手动回退和卸载目前明确失败关闭；生产恢复必须走独立、已审批的
PostgreSQL runbook。
