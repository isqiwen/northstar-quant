# Scripts

私网 Dashboard 不使用 Docker、Caddy 或 `80`/`443`。只有在 `deploy.env` 明确设置
`DASHBOARD_DEPLOY_ENABLED=1` 时，发布流程才会管理独立的
`<SYSTEMD_SERVICE_NAME>-dashboard.service`；它固定监听 `127.0.0.1`，不会替换主
health/scheduler 服务。访问步骤与不公开暴露的边界见[Linux 一键部署](../docs/07_Linux一键部署.md#私网-dashboard可选)。

脚本按职责组织，日常只需要记住三个入口。

| 脚本 | 是否直接运行 | 用途 |
| --- | --- | --- |
| `setup_dev.sh` | 是 | 在 macOS/Linux 初始化 Docker PostgreSQL 开发环境。 |
| `setup_dev.ps1` | 是 | 在 Windows PowerShell 初始化 Docker PostgreSQL 开发环境。 |
| `deploy.sh` | 是 | 检查、构建、上传并部署到 Linux 服务器。 |

Windows 在项目根目录直接执行：

```powershell
.\scripts\setup_dev.ps1
```

PowerShell 与 Bash 入口都会在缺失时从 `.env.example` 创建完整的本地 `.env`；旧结构会在
保留已有值的前提下迁移，并先保留本地忽略的备份。两者随后确保 `northstar` 与
`northstar_test` 两个开发数据库存在，运行迁移、健康检查、测试和 Ruff。它们在当前进程和活动
`.env` 中都强制使用 `paper` 并禁用实盘，不会下载市场数据或调用 live 命令。
两个开发入口都会在缺失时从 `configs/app.example.yaml` 创建完整的活动配置
`configs/app.yaml`；示例文件不会被程序读取，后续仅编辑活动文件。

开发环境内部模块：

| 路径 | 用途 |
| --- | --- |
| `dev/common.sh` | 日志、错误处理与系统检查。 |
| `dev/docker.sh` | Docker 与 Compose 检查。 |
| `dev/env.sh` | 本地 `.env` 和数据库密码管理。 |
| `dev/postgres.sh` | 开发 PostgreSQL 启动和初始化。 |

Linux 部署内部模块：

| 脚本 | 用途 |
| --- | --- |
| `deploy/build-artifact.sh` | 构建不含密钥、数据和虚拟环境的源码制品。 |
| `deploy/provision.sh` | 远程部署总控。 |
| `deploy/install-runtime.sh` | 安装 Ubuntu/Debian 运行时、uv、Python 和服务用户。 |
| `deploy/install-release.sh` | 安装锁定依赖、迁移、健康检查、原子切换和失败回退。 |
| `deploy/lib/*.sh` | 公共函数、SSH 连接复用与非敏感部署配置读取。 |
| `deploy/systemd/*.service.in` | health、scheduler 与默认关闭的私网 Dashboard systemd 安全模板。 |

可选的私有 ntfy 服务由 Linux 部署流程管理，但 Docker 与 Docker Compose 必须预先安装在远程服务器。
在 `deploy.env` 中设置 `NTFY_DEPLOY_ENABLED=1` 并填写域名、ACME 邮箱及数据目录后，首次部署还必须创建
受 Git 忽略的 `ntfy.bootstrap.env`，再显式执行：

```bash
UPLOAD_ENV=1 UPLOAD_NTFY_BOOTSTRAP=1 SETUP_SERVER=1 scripts/deploy.sh
```

此 bootstrap 只用于首次身份初始化；日常发布保持 `UPLOAD_NTFY_BOOTSTRAP` 未设置，以保留 ntfy 的认证
数据。完整的 DNS、端口、Caddy TLS、权限与手机送达边界见
[Linux 一键部署](../docs/07_Linux一键部署.md#私有-ntfy可选)。

首次部署：

```bash
cp deploy.env.example deploy.env
# 在独立的部署工作树中创建活动环境文件；不要复用开发工作树的 .env。
cp .env.example .env
# 编辑两个本地文件，并将 .env 的 NORTHSTAR_ENV 设为 production 后执行
UPLOAD_ENV=1 SETUP_SERVER=1 scripts/deploy.sh
```

后续版本发布：

```bash
scripts/deploy.sh
```

完整说明见 `docs/07_Linux一键部署.md`。
