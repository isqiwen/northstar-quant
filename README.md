# Northstar Quant

个人国内期货研究与交易系统。一个仓库、一个 Python 包，包含 Data Hub、Research、Live 三个应用。
目标是逐步完成受控实盘闭环；当前尚未实现柜台发单、撤单与完整实盘执行。

## 当前功能

| 应用 | 已实现的主要功能 |
|---|---|
| Data Hub · 数据管理中心 | 原始文件留存、分钟数据导入、独立持久加工与失败记录、质量检查、不可变快照、查询与导出 |
| Research · 研究工作台 | 因子与策略目录、因子计算、固定配置、回测与结果比较、策略版本与候选发布、可恢复的文件回放 Paper 账户 |
| Live · 交易管理 | 独立运行内核、运行诊断、SimNow 只读查询、有时限的行情与账户回报接收、影子信号、账户与委托观察核对、归档与候选接收 |

研究、Paper、柜台模拟和实盘是不同的证据。Paper 使用历史文件模拟成交；Live 当前的影子信号不发单。
接收候选、查询成功或核对一致都不授予交易权限。启动、重启和恢复不会自动连接柜台或恢复接收。

持久研究任务调度、持续采集及完整交易恢复仍待实现，具体进度见 [开发路线](docs/ROADMAP.md)。

## 快速启动

部署位置：Data Hub 在 `core.local`，Research 在 `research.local`，PostgreSQL 与文件/备份在 QNAP `nas.local`。
三个应用的前端、API、worker/内核分别运行在独立容器中。

先根据 [跨主机部署说明](deploy/README.md) 填写各主机私有环境文件，确认 QNAP 实际 NFS 导出路径。
默认优先 NFSv4，各共享挂载在 `/mnt/northstar/` 下；Research 只读市场发布目录、读写自己的产物目录。
路径尚未确认时不猜测、不自动创建本地替代存储。

可使用统一远程入口（先填写仓库外的主机配置，详见[部署说明](deploy/README.md)）：

```sh
./scripts/northstarctl.py deploy database --config ~/.config/northstar/hosts.toml
./scripts/northstarctl.py deploy data-hub --config ~/.config/northstar/hosts.toml
./scripts/northstarctl.py deploy research --config ~/.config/northstar/hosts.toml
./scripts/northstarctl.py deploy live --config ~/.config/northstar/hosts.toml
```

脚本部署当前已提交版本，支持 `start`、`restart`、`stop`、`status`、`logs` 和 `--help`。
`start`/`restart` 使用已部署版本，不重新构建。Live 内核运行时拒绝整套启停；
使用 `restart live --management-only` 可单独重启前端/API。
也可以在对应主机的仓库根目录执行（需要 Git、uv、Make、Docker；Linux 客户端还需 NFS 客户端与 `findmnt`）：

```sh
# nas.local：先初始化 PostgreSQL 和共享来源目录
make up-database ENV_FILE=/absolute/private/nas.env
# core.local：NFS 已正确挂载后
make up-data ENV_FILE=/absolute/private/core.env
# research.local：市场只读、研究目录可写后
make up-research ENV_FILE=/absolute/private/research.env
# Live 所在主机，仍使用当前独立部署
make up-live
```

正式构建要求工作区干净；未提交修改时在对应命令前加 `NORTHSTAR_DEVELOPMENT_BUILD=1`。
Data/Research 启动只检查 NAS 网络数据库、NFS 挂载和存储身份，不启动 NAS 或另一个应用。
Data API 持久排队，独立 worker 加工；关闭 Data Web/API 不停止已接收任务。

当前工作台通过 SSH 隧道访问，保留本机 Host/Origin 保护：

```sh
ssh -N -L 18082:127.0.0.1:18082 core.local
ssh -N -L 18084:127.0.0.1:18084 research.local
```

| 应用 | 隧道建立后的浏览器地址 |
|---|---|
| Data Hub | <http://127.0.0.1:18082> |
| Research | <http://127.0.0.1:18084> |
| Live | <http://127.0.0.1:18080>（远程访问见独立 Live 部署说明） |

`make ps-data` / `ps-research` / `ps-live` 查看状态；`make down-data` / `down-research` / `down-live` 只停止对应应用并保留卷。
Data/Research 命令均需传相同的 `ENV_FILE`。NAS 单独使用 `make ps-database` / `down-database`，停止会影响依赖它的数据与研究操作。

修改代码后重新构建；`restart` 不会构建新代码。端口、NFS 参数、存储权限与备份见 [部署说明](deploy/README.md)。
当前个人容器与数据不会被新命令自动迁移或接管。

## 前后端如何运行

三个应用分别运行独立的 Next.js 前端和 Python API 后端，使用 HTTP 传输 Protobuf 二进制消息。
前端使用 React、TypeScript、Ant Design 和 ECharts；后端保留 FastAPI 及 Python 业务模块。

```text
浏览器 → Next.js 前端 → 所属 Python API → 业务模块
                            │
                            └─ Live API → 独立 Live 内核
```

前端仅负责页面、交互与转发所属 API，没有数据库或柜台凭据。Python 安装包不包含界面文件，
前后端分别构建镜像、启动和重启。前端退出不会停止后端；后端不可用时页面显示错误，不自动重发操作。

| 应用 | Next.js 前端 | Python API（本机调试） |
|---|---|---|
| Data Hub | `18082` | `19082` |
| Research | `18084` | `19084` |
| Live | `18080` | `19080` |

日常访问前端端口即可。Live 内核为独立的 `18081` 服务。
NAS PostgreSQL 分为 `northstar_data_hub`、`northstar_research`、`northstar_live`，账号按库隔离。
Research 消费只读固定发布文件，结果写入自己的库和产物共享，临时计算目录在本机。
Live 当前仍独立存储，不依赖家庭 NAS；最小恢复日志与异步归档是下一项改造。
独立持久研究 worker 与持续采集仍待实现。
进程隔离不代表已经完成任务检查点恢复，也不能隔离整台主机故障。

### 后端日志

三个 Python 后端自动写入独立 JSON 行日志。默认根目录是当前工作目录下的 `.northstar/logs/`：

| 应用 | 文件 |
|---|---|
| Data Hub | `data_hub/northstar-data-hub-api-YYYY-MM-DD.log`、`data_hub/northstar-data-hub-worker-YYYY-MM-DD.log` |
| Research | `research/northstar-research-api-YYYY-MM-DD.log` |
| Live | `live/northstar-live-api-YYYY-MM-DD.log`、`live/northstar-live-kernel-YYYY-MM-DD.log` |

Compose 把日志保存在持久日志卷，容器内路径为 `/var/log/northstar/`，重建容器仍保留。
例如 `docker compose -f deploy/live/compose.yaml exec live tail -n 50 /var/log/northstar/live/northstar-live-kernel-YYYY-MM-DD.log`。
将 `YYYY-MM-DD` 替换为日志日期。日期采用进程所在时区，跨日后的首次后台写入自动切换文件。
每个文件默认 10 MiB，超限后增加 `.1`～`.5` 后缀；每个程序除当前文件外，跨日期和大小轮转合计最多保留 5 份历史文件（按修改时间保留最新）。Python 运行日志写文件；
容器启动错误仍可通过 `docker compose --env-file /absolute/private/research.env -f deploy/research/compose.yaml logs --tail=100 research-api` 查看。

可在启动前设置 `NORTHSTAR_LOG_DIR`（日志根目录）、`NORTHSTAR_LOG_MAX_BYTES`（单文件上限）、
`NORTHSTAR_LOG_BACKUPS`（历史文件总份数）和 `NORTHSTAR_LOG_QUEUE`（队列容量，默认 1024 条）。
同一应用角色的日志文件只有一个写者；运行多个实例时分别设置日志根目录。
日志目录不可写或已有写者时拒绝启动，运行期间的磁盘故障不会转为业务线程同步写盘。

Live 调用线程只提交有界记录，格式化、写盘与轮转在后台完成；队列满时丢弃运行日志并计数。
内核后台每 50 ms 最多处理 8 条，避免日志洪峰持续占用 GIL；不逐笔记录行情或每步策略计算。
后台线程仍有 CPU/GIL 开销，容量与交易延迟须在部署机器实测；这不是零开销或硬实时保证。
正常退出最多等待 1 秒排空，强制退出或磁盘故障可能丢失末尾日志。成交、授权与订单事实仍以业务持久化记录为准。

各管理 API 的 `/health/logging` 提供队列、丢弃、写入错误和写线程状态；
Live 内核通过 `northstar check` 和现有运行诊断提供日志健康，不把日志状态当作交易授权或停止指令。
日志保留应用、组件、进程、启动身份、时间、级别和消息；不写请求体、查询字符串、认证头或异常参数。
日志实现位于 `backend/src/northstar_quant/logging_/`，与运行时日志目录分开。
业务代码使用标准 `logging.getLogger(__name__)`，传入固定模板和少量标量；不要先构造大字符串或传行情/账户对象。

## 本机开发

需要 Python 3.12、uv 和 Node.js 22.12+。在仓库根目录安装依赖：

```sh
uv sync --project backend --locked
npm --prefix frontend ci
```

在所属主机先用对应的启动命令和环境文件启动应用，再停止要调试的前端容器，释放端口。例如开发 Research：

```sh
make up-research ENV_FILE=/absolute/private/research.env
docker compose --env-file /absolute/private/research.env -f deploy/research/compose.yaml stop research
NORTHSTAR_API_URL=http://127.0.0.1:19084 npm --prefix frontend run dev:research
```

Data Hub 对应 `dev:data`、后端 `19082`；Live 对应 `dev:live`、后端 `19080`。
开发与日常访问使用相同端口。Next.js 开发服务支持热更新，修改 Python 代码仍需更新对应 API 服务。

本机 Python 入口为 `uv run --project backend northstar serve data-api`、`uv run --project backend northstar serve research-api`、
`uv run --project backend northstar serve live-api`（Live 管理 API）和 `uv run --project backend northstar serve live-kernel`（内核）。
`uv run --project backend northstar serve data-worker` 启动独立数据执行器，需要与 Data API 使用相同数据库、来源目录和代码版本。
前三个默认监听表中的 `190xx` 端口；不要与同端口容器同时启动。
各后端需配置所属 `NORTHSTAR_DATABASE_URL`；Data Hub/Research 另需对应市场、研究目录及存储 UUID（见部署模板）。
Data Hub 与当前 Live 内核使用 `NORTHSTAR_DATA_DIR`；Research 不访问来源目录。
Live 管理 API 使用 `NORTHSTAR_LIVE_URL` 与 `NORTHSTAR_LIVE_AUTH` 访问内核。

前端生产构建使用 `npm --prefix frontend run build`，随后运行对应 `start:data`、`start:research`、`start:live`，
同样需要 `NORTHSTAR_API_URL`。Python 包单独用 `uv build --project backend` 构建；未提交代码需加 `NORTHSTAR_DEVELOPMENT_BUILD=1`。

## 代码与接口

| 位置 | 职责 |
|---|---|
| `proto/` | 前后端共同消费的 Protobuf 协议源码 |
| `backend/` | Python 源码、测试、依赖、构建钩子和 Dockerfile |
| `frontend/apps/{data_hub,research,live}/` | 各应用的页面、路由与交互 |
| `frontend/tests/` | 前端行为测试与接口类型检查 |
| `frontend/shared/` | 复用控件、图表、请求与浏览器状态处理 |
| `backend/src/northstar_quant/apps/` | 应用装配、启动及各功能的 `*_api.py` 接口 |
| `backend/src/northstar_quant/{data_management,research,live}/` | 数据业务、研究流程与生产会话 |
| `backend/src/northstar_quant/{factors,strategies,risk,execution,broker,accounting,simulation}/` | 因子、策略、风险、执行、柜台、账户与模拟能力 |
| `backend/src/northstar_quant/{web,cli}/` | 共享 Web 接入机制与薄命令行入口 |
| `deploy/`、`backend/tests/` | 部署配置与行为验证 |

接口以根目录 `proto/` 下的 `data_hub.proto`、`research.proto`、`live.proto` 为准，生成 Python 消息、TypeScript 类型与浏览器调用声明。
变更协议后执行，不手工编辑生成文件：

```sh
npm --prefix frontend run api:generate
npm --prefix frontend run check
```

详细定义位置与变更流程见 [接口说明](docs/API.md)；新增算法见 [因子与策略开发](docs/FACTOR_STRATEGY_DEVELOPMENT.md)。
CLI 按 `serve`、`status`、`check`、`data`、`research`、`maintenance`、`advanced` 分组。
使用 `northstar --help` 查看入口，具体命令与示例见 [命令行说明](docs/CLI.md)。
Web 与 CLI 调用同一套业务规则，CLI 不另行实现采集、回测或交易逻辑。

## 验证

准备专用、可清空的 PostgreSQL 数据库 `northstar_quant_test` 后执行：

```sh
export NORTHSTAR_TEST_DATABASE_URL='postgresql+psycopg://northstar:northstar_local@127.0.0.1:15432/northstar_quant_test'
make verify
```

`make verify` 检查接口生成同步、前端类型与构建、前端测试、Python 格式与类型及业务测试。
测试会重置测试库，不能指向应用数据库。安装验收与浏览器验收另由
`scripts/check_install.py`、`scripts/check_browser.py` 执行；独立容器验收使用
`scripts/check_application_deployment.py` 和 `scripts/check_live_deployment.py`。CI 配置见 [.github/workflows/ci.yml](.github/workflows/ci.yml)。

## 数据与运行维护

各应用日志和 Live 认证保存在所属 Docker 卷；Data Hub/Research 的数据库、来源、市场发布、研究产物和备份位于 NAS，Live 内核拥有独立本地数据卷。重建容器不会清空这些数据；不要用 `docker compose down -v` 停止日常应用。
PostgreSQL 运行目录保留在 NAS 自己的本地卷，不放在 NFS 客户端挂载中。数据库与其引用的来源文件必须一起备份，市场数据、备份和私密凭据不提交到 Git。

```sh
# 目标目录必须尚不存在；每次备份换一个名称
docker compose --env-file /absolute/private/core.env -f deploy/data_hub/compose.yaml exec data-api northstar maintenance backup /var/lib/northstar/backups/manual-001
```

恢复时准备空数据库和新的独立来源目录，设置 `NORTHSTAR_DATABASE_URL`、`NORTHSTAR_DATA_DIR`，
再运行 `northstar maintenance restore /absolute/backup-directory`，不要预先执行 `maintenance init-db`。
恢复不会覆盖已有数据库，也不会自动恢复行情连接或交易权限。
`maintenance init-db` 只接受当前存储结构，不自动重置已有数据；存储结构不匹配时应先保全所需证据，再明确处理。

当前回测与 Paper 的模拟范围是单合约、显式日内时段和分钟数据，费用、滑点、保证金为显式假设；
没有完整模拟部分成交、市场冲击或交易所结算，也不会在数据结束时自动平仓。
金额使用十进制值，结果绑定固定数据、配置及 Git 身份；`-dirty` 结果无法仅凭提交号还原未提交源码。

Live 默认不发送订单。未知结果不能盲目重发；实时执行最终必须由用户在完成核对与验证后明确授权。
当前柜台适配依赖 Linux amd64，默认启动不加载 SimNow 凭据；具体接入与已有证据见下列文档。

## 进一步阅读

- [架构与职责边界](docs/ARCHITECTURE.md)
- [前后端协议](docs/API.md)与 [命令行使用](docs/CLI.md)
- [因子与策略开发](docs/FACTOR_STRATEGY_DEVELOPMENT.md)
- [开发路线](docs/ROADMAP.md)与 [GitHub Project](https://github.com/users/isqiwen/projects/1)
- [数据、柜台来源与验收边界](docs/SOURCES.md)
- [独立 Live 部署](deploy/live/README.md)
