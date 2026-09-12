# Live 部署

Live 在自己的主机上运行，包含独立的 Next.js 前端、Python 管理 API、按实例隔离的交易内核和实例本地 SQLite。
运行不依赖 Data Hub、Research 或远程存储；容器不设置 CPU、内存、交换空间或进程数限制。

## 配置

在 `deploy/hosts.toml` 填写 Live 主机的 `host`、`user` 和 SSH `port`。
`user` 仅用于首次登录和提权，初始化后由脚本创建的 `northstar` 用户管理部署。
目标主机默认 SSH 登录和部署依赖须可用，deploy 自动准备 northstar 账号，见[部署前置要求](../README.md#部署与访问)。

应用配置统一放在 [deploy/live/.env](.env)：

| 参数 | 用途 |
|---|---|
| `NORTHSTAR_LIVE_INSTANCES` | 实例列表（`id:broker_profile`），例如 `sim:simnow_trading,dev:simnow_dev`；每个柜台 profile 仅一套账户实例；`ctp_production`（LIVE）尚未开放 |
| `NORTHSTAR_SIMNOW_USER_ID` | SimNow 账号 |
| `NORTHSTAR_SIMNOW_APP_ID` | SimNow 应用标识 |
| `NORTHSTAR_SIMNOW_AUTH_CODE` | SimNow 认证码 |
| `NORTHSTAR_SIMNOW_PASSWORD` | SimNow 密码 |

实盘 CTP 的 `NORTHSTAR_CTP_BROKER_ID`、`NORTHSTAR_CTP_TRADE_FRONT`、
`NORTHSTAR_CTP_MD_FRONT`、`NORTHSTAR_CTP_USER_ID`、`NORTHSTAR_CTP_PASSWORD`、
`NORTHSTAR_CTP_APP_ID`、`NORTHSTAR_CTP_AUTH_CODE` 已列入 `.env`，默认留空。
当前只保存配置，不注入模拟内核或管理端，不开放实盘连接。

凭据使用单引号包裹，避免密码中的 `$` 被 Compose 展开；实际凭据保留在本地，不提交 Git。
只有内核接收柜台凭据。镜像版本由部署脚本根据应用和 Git 提交自动选择。

## 部署与管理

在仓库根目录执行：

```sh
# 首次初始化主机

# 部署已提交代码，首次自动上传本地 deploy/live/.env
./scripts/northstarctl.py deploy live

# 已部署后，明确更新远程配置
./scripts/northstarctl.py deploy live --env-file deploy/live/.env

# 只操作 dev 实例，sim 继续运行
./scripts/northstarctl.py restart live --instance dev

# 日常整套管理
./scripts/northstarctl.py status live
./scripts/northstarctl.py logs live --follow
./scripts/northstarctl.py start live
./scripts/northstarctl.py restart live
./scripts/northstarctl.py stop live
```

默认保留已有远程配置；`start`、`restart` 使用已部署版本，不重新构建。
整套 `restart`、`stop` 会影响内核，`stop` 保留持久数据。
前端和管理 API 单独重启不负责停止内核。

每个实例另有 `live-monitor` 容器，使用单独的只读凭据，每 5 秒查询内核、本地存储与全量订单健康。
UNKNOWN 订单、事实冲突、未确认费用和存储异常均会报告。状态变化立即输出 JSON 告警，不变时每分钟输出心跳；通过上述 `logs live` 可见。
它不访问账户数据库、不持有柜台或控制凭据，也不启动、重启或授权内核。
`restart live --instance sim` 只重启交易内核，观察进程继续报告中断与恢复。
目前出口是容器日志，尚未配置外部告警接收方；整台主机失联必须由另一台机器上的观察者检测，不能由本机进程保证。

## 访问与目录

- 前端固定为 `http://<Live 主机>:18080`，不限制来源 IP；`live.wangqiwen.me` 需由 Caddy/FRP 转发到该端口。
- 管理 API 固定绑定 `127.0.0.1:19080`；内核不向宿主机发布端口，SQLite 没有数据库服务端口。
- 同源、浏览器会话和交易授权检查仍然生效。

目录由部署脚本创建并设置权限，固定在 `/opt/northstar`：

| 路径 | 内容 |
|---|---|
| `apps/live` | 程序版本与部署文件 |
| `config/live.env` | 私有运行配置 |
| `state/live/accounts` | 全部内核共享的本机账户独占锁（部署用户独占读写） |
| `state/live/instances/<id>/database/live.sqlite` | 本地 SQLite 及 WAL 文件 |
| `state/live/instances/<id>/sources` | 本地数据文件 |
| `credentials/live/instances/<id>` | 内核与管理 API 的访问凭据 |
| `logs/live` | 共享管理 API 日志 |
| `logs/live/instances/<id>` | 各实例内核日志 |

日志名为 `northstar-live-api-YYYY-MM-DD.log` 和 `northstar-live-kernel-YYYY-MM-DD.log`。
内核运行日志异步写入；日志不代替订单、成交和恢复记录。

## 当前边界与验收

启动应用不会自动连接柜台或授权下单；重启、恢复后也不会自动恢复发送权限。
网页顶部选择运行实例，只改变查看和控制目标，不切换账户或重启内核。
每个实例分别保存固定策略材料、查询、接收和账户证据；同一数据库禁止改绑账户或环境。
账户按“环境＋柜台标识＋账户号”确定身份。同一主机所有内核共享账户锁，
即使用不同实例名、数据库或容器，也不能同时占用同一账户；冲突时新内核启动失败，日志说明账户已被占用。
锁覆盖内核生命周期，先停止接收再释放；锁不会按超时失效。重启仍需重新核对，不继承交易授权。
不要删除或替换运行中的锁目录/文件，也不能为不同容器挂载不同的账户锁目录。
当前支持 hosts.toml 中唯一 Live 主机，不支持同账户跨主机部署、自动接管或主备切换；
本机文件锁不构成分布式防重。跨主机迁移须确认原主机及柜台进程停止、保全完整事实，并完成恢复核对。
同账户多策略应由一个实例协调，尚未实现的多策略调度不通过多内核绕开。

空凭据部署可启动检查，首次补齐账号后固定绑定；以后只可更换密码等认证信息。
实盘仍未开放；完整策略运行绑定、仿真报撤单和执行授权不由本次多实例部署替代。

`compose.yaml` 是部署模板，由 `scripts/operations/live_instances.py` 按配置展开；通过
`northstarctl` 或 `make up-live` 启动，避免直接运行未展开的模板。
Python 容器使用部署用户 UID/GID，认证文件保持 owner-only，SQLite 使用本机持久磁盘，不允许 NFS/SMB。
旧 PostgreSQL 持久目录不自动移动或删除，有旧目录准备记录时先保全数据并核对迁移，不能将新空库视为旧账户恢复。
网页可访问不代表账户已核对或可以交易，完整柜台仿真往返仍需单独验收。

使用已构建的 Linux amd64 镜像执行隔离部署验收：

```sh
uv run --project backend python scripts/acceptance/check_live_deployment.py \
  --image northstar-quant:local --frontend-image northstar-live-frontend:local
```

验收使用临时目录、独立容器和端口，不读取个人柜台凭据；检查 Web 与内核生命周期隔离及故障响应，结束后清理自己的测试资源。

SQLite 使用 WAL、FULL 同步和短写事务；实例进程锁不随时间过期。成交入账与补记进度共同提交。
备份使用 SQLite 快照 API，并复制该快照引用的原始文件；禁止单独复制正在运行的数据库文件。
在对应内核容器执行 `northstar maintenance backup /绝对备份目录`，备份目的地需另行挂载。
恢复需先停止对应内核，使用空 SQLite 和新的来源目录执行 `northstar maintenance restore /绝对备份目录`。
恢复保留实例、环境和账户绑定，不自动重新连接或授权发送。当前没有旧 PostgreSQL 数据自动转换工具。

运行环境固定为 `BACKTEST` / `SANDBOX` / `LIVE`。两个 SimNow profile 都属于 SANDBOX，仍使用外部 CTP 柜台；生产资金属于 LIVE。部署按 profile 自动注入 `NORTHSTAR_ENVIRONMENT` 和 `NORTHSTAR_BROKER_PROFILE`；独立启动也必须匹配这两个值。修改配置不能切换已有数据库的账户绑定。


## 固定策略材料

在 Research 发布候选 JSON，在 Live 的“固定策略材料”页面接收。核验通过后，
其配置会出现在“持续行情与影子策略”的本地配置列表；Research 停机不影响读取。
会话固定具体候选，后续接收不会改写旧绑定。接收材料不授权下单。

工作台首次访问创建唯一用户。普通重启保留账号；每次 deploy 清除账号，重新访问创建。
