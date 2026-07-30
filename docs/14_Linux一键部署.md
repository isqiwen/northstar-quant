# Linux 一键部署

## 适用范围

`scripts/deploy.sh` 用于从 macOS 或 Linux 开发机向 Ubuntu/Debian
服务器发布 Northstar Quant。它负责本地质量检查、构建源码制品、SSH 上传、运行时安装、
依赖同步、数据库迁移、systemd 配置、健康检查和应用版本回退。

它不负责创建生产 PostgreSQL、配置云防火墙、购买服务器或申请券商权限。生产数据库应
独立部署并具备自动备份，不能复用开发环境的 Docker 数据卷。

当前仓库没有 production 画像，也没有完整 CTP 报单适配器。部署配置必须保持
`SERVICE_MODE=health`、`NORTHSTAR_BROKER=paper` 和
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

生产密钥和数据库 URL 使用 `.env.production`：

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

必须替换 `NORTHSTAR_DATABASE_URL` 中的 `CHANGE_ME`，并根据服务器目录调整
`NORTHSTAR_STORAGE_DIR` 和 `NORTHSTAR_REPORTS_DIR`。这两个本地文件均被 Git 忽略。

## 部署命令

先做不连接服务器的完整构建演练：

```bash
DRY_RUN=1 scripts/deploy.sh
```

首次部署需要安装服务器运行时并上传生产环境文件：

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

默认目录结构：

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

每个版本拥有独立 `.venv`，`storage`、`reports` 和生产 `.env` 在版本间共享。
依赖通过远端 `uv sync --frozen --no-dev --no-editable` 从 `uv.lock` 安装。

## 服务模式

### health

默认模式。systemd 以 oneshot 服务运行：

```bash
northstar health
```

该模式会迁移和验证应用，但不会启动 Dashboard、调度器或任何交易执行流程。

### scheduler

长期运行：

```bash
northstar live scheduler
```

paper 调度器要求：

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
