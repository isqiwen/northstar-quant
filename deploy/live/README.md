# Live 部署

Live 在自己的主机上运行，包含独立的 Next.js 前端、Python 管理 API、交易内核和本地 PostgreSQL。
运行不依赖 Data Hub、Research 或远程存储；容器不设置 CPU、内存、交换空间或进程数限制。

## 配置

在 `deploy/hosts.toml` 填写 Live 主机的 `host`、`user` 和 SSH `port`。
`user` 仅用于首次登录和提权，初始化后由脚本创建的 `northstar` 用户管理部署。
目标主机需具备 SSH 和 Python 3.11+；部署脚本自动安装缺失的部署依赖。

应用配置统一放在 [deploy/live/.env](.env)：

| 参数 | 用途 |
|---|---|
| `NORTHSTAR_LIVE_ENVIRONMENT` | `simnow_trading`：第一套模拟环境（默认）；`simnow_dev`：接口联调环境，不提供结算；`production` 当前尚未开放，启动会报错 |
| `NORTHSTAR_LIVE_DATABASE_PASSWORD` | Live 本地数据库密码，默认 `123456`；修改配置不会自动更改已有数据库密码 |
| `NORTHSTAR_SIMNOW_USER_ID` | SimNow 账号 |
| `NORTHSTAR_SIMNOW_APP_ID` | SimNow 应用标识 |
| `NORTHSTAR_SIMNOW_AUTH_CODE` | SimNow 认证码 |
| `NORTHSTAR_SIMNOW_PASSWORD` | SimNow 密码 |

凭据使用单引号包裹，避免密码中的 `$` 被 Compose 展开；实际凭据保留在本地，不提交 Git。
只有内核接收柜台凭据。镜像版本由部署脚本根据应用和 Git 提交自动选择。

## 部署与管理

在仓库根目录执行：

```sh
# 首次初始化主机
./scripts/northstarctl.py init-host live

# 部署已提交代码，首次自动上传本地 deploy/live/.env
./scripts/northstarctl.py deploy live

# 已部署后，明确更新远程配置
./scripts/northstarctl.py deploy live --env-file deploy/live/.env

# 日常管理
./scripts/northstarctl.py status live
./scripts/northstarctl.py logs live --follow
./scripts/northstarctl.py start live
./scripts/northstarctl.py restart live
./scripts/northstarctl.py stop live
```

默认保留已有远程配置；`start`、`restart` 使用已部署版本，不重新构建。
整套 `restart`、`stop` 会影响内核和数据库，`stop` 保留持久数据。
前端和管理 API 单独重启不负责停止内核。

## 访问与目录

- 前端固定为 `http://<Live 主机>:18080`，不限制来源 IP；`live.wangqiwen.me` 需由 Caddy/FRP 转发到该端口。
- 管理 API 固定绑定 `127.0.0.1:19080`；内核和 PostgreSQL 不向宿主机发布端口。
- 同源、浏览器会话和交易授权检查仍然生效。

目录由部署脚本创建并设置权限，固定在 `/opt/northstar`：

| 路径 | 内容 |
|---|---|
| `apps/live` | 程序版本与部署文件 |
| `config/live.env` | 私有运行配置 |
| `state/live/postgresql` | 本地数据库 |
| `state/live/sources` | 本地数据文件 |
| `credentials/live` | 内核与管理 API 的访问凭据 |
| `logs/live` | 按日期分别保存 API、内核日志 |

日志名为 `northstar-live-api-YYYY-MM-DD.log` 和 `northstar-live-kernel-YYYY-MM-DD.log`。
内核运行日志异步写入；日志不代替订单、成交和恢复记录。

## 当前边界与验收

启动应用不会自动连接柜台或授权下单；重启、恢复后也不会自动恢复发送权限。
模拟与未来实盘使用同一应用，通过运行环境区分；切换环境不能复用另一环境的接收记录。
网页可访问不代表账户已核对或可以交易，完整柜台仿真往返仍需单独验收。

使用已构建的 Linux amd64 镜像执行隔离部署验收：

```sh
uv run --project backend python scripts/acceptance/check_live_deployment.py \
  --image northstar-quant:local --frontend-image northstar-live-frontend:local
```

验收使用临时目录、独立容器和端口，不读取个人柜台凭据；检查 Web 与内核生命周期隔离及故障响应，结束后清理自己的测试资源。
