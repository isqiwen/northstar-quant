# Northstar Quant

个人国内期货研究与交易系统。一个仓库、一个 Python 包，包含 Data Hub、Research、Live 三个应用。
目标是逐步完成受控实盘闭环；当前尚未实现柜台发单、撤单与完整实盘执行。

## 当前功能

| 应用 | 已实现的主要功能 |
|---|---|
| Data Hub · 数据管理中心 | Tushare 原始响应留存、全量历史持久同步、独立加工、全量队列积压与失败统计、质量检查、不可变快照、查询与导出 |
| Research · 研究工作台 | 因子与策略目录、因子计算、固定配置、回测与结果比较、策略版本与候选发布、可恢复的文件回放 Paper 账户 |
| Live · 交易管理 | 独立运行内核、运行诊断、SimNow 只读查询、有时限的行情与账户回报接收、影子信号、账户与委托观察核对、归档与候选接收 |

研究、Paper、柜台模拟和实盘是不同的证据。Paper 使用历史文件模拟成交；Live 当前的影子信号不发单。
接收候选、查询成功或核对一致都不授予交易权限。启动、重启和恢复不会自动连接柜台或恢复接收。

已有 Tushare 定时增量/补缺与独立研究 worker；跨日研究输入、统一期货账本、受约束成交及完整柜台执行仍待交付，见 [架构设计](docs/ARCHITECTURE.md) 和 [开发路线](docs/ROADMAP.md)。

## 快速启动

部署位置：Data Hub 和独立 PostgreSQL 在 `core.local`，Research 在 `research.local`。
三个应用的前端、API、worker/内核分别运行在独立容器中。
应用持久目录固定在 `/opt/northstar/`；本机磁盘或主机预先挂载的共享使用同一套配置。
配置文件在 `deploy/{database,data_hub,research,live}/.env`，数据库密码默认为 `123456`，发布接口无需 token，实际凭据放私有运行副本。
部署脚本自动准备 NFS 客户端、Docker、目录和权限；外部 NFS 服务须提前导出 `/quant`，Data Hub 读写挂载、Research 只读挂载。存储 UUID 自动生成并持久保存，详见[部署说明](deploy/README.md)。

可使用统一远程入口（填写 `deploy/hosts.toml` 后提交代码，首次部署自动上传所属 `.env`）：

```sh
./scripts/northstarctl.py init-host
./scripts/northstarctl.py deploy database
./scripts/northstarctl.py deploy data-hub
./scripts/northstarctl.py deploy research
./scripts/northstarctl.py deploy live
```

远程 `deploy` 自动安装目标 Ubuntu/Debian 主机缺失的 Git、uv、Docker/Compose/Buildx。
同时将仓库 `scripts/operations/docker_configuration.py` 中的四个镜像源合并到 `/etc/docker/daemon.json`，
保留其他 Docker 设置，校验后热加载并确认生效，不重启容器。
主机上的存储检查和版本读取直接使用 Python，不安装后端业务依赖；业务依赖在镜像中安装。
中断部署或 SSH 连接关闭后，远程部署会清理本次子进程并释放锁；已启动容器保留，重试 `deploy` 继续部署。
管理对象为 `database`、`data-hub`、`research`、`live`；数据库固定与 Data Hub 同机，仍独立部署。项目不管理 NFS 服务端。

`hosts.toml` 的应用主机填写 host/user/port；`[nfs]` 只填写 host，不配置 `[database]`。NFS 固定挂载到 `/opt/northstar/files/market`；user 仅供 `init-host` 登录和提权，创建 northstar 用户、配置 SSH 公钥及免密码 sudo。
Research 部署会在主机上解析发布接口的 `.local` 地址，并为容器生成主机映射；部署主机必须能解析该地址，IP 变化后执行 `restart research` 刷新映射。
之后部署固定使用 northstar，首次运行配置自动上传，默认保留已有配置；目标先具备 SSH、Python 3.11+。
脚本部署当前已提交版本；可用 `deploy data-hub --env-file /本地路径/data-hub.env` 指定应用配置并更新远程运行副本。
不指定时默认首次上传仓库中的 `.env`，后续保留远程配置；支持 `start`、`restart`、`stop`、`status`、`logs` 和 `--help`。
部署前会校验当前应用允许的参数：未知/废弃、重复、缺失或无效配置拒绝部署；保留的远程配置也必须通过当前版本校验。
`.env` 使用单行 `KEY=value`；需要字面 `$变量` 的密码使用单引号，不允许依赖主机环境展开变量。
显式 `--env-file` 替换配置（未提供新工作台密码摘要时保留已安装摘要）；配置与源码在同一次受部署锁保护的传输中处理，校验通过后才激活。

`status` 显示 `apps/<应用>/deployment.json`：目标 SHA、配置 SHA256、部署阶段、最后成功组合和实际容器镜像身份。
部署失败保留新配置及失败记录，不宣称容器或数据库已原子回滚；请修正后重新 `deploy`。
`start/restart` 拒绝未验证或手工修改的配置。成功后只清理该应用无引用的旧版本和旧镜像，配置、业务数据、凭据及构建缓存保留。
清理失败单独标记 `cleanup_failed`，与应用启动失败区分。修改数据库密码配置不会自动更改已有数据库账号密码。

`start`/`restart` 使用已部署版本，不重新构建。所有应用统一操作整个部署对象；`restart live` 会重启前端、API和内核；SQLite 文件保留。
也可以在对应主机的仓库根目录执行（需要 Git、uv、Make、Docker）：

```sh
# core.local：先初始化独立 PostgreSQL 和已准备的存储目录
make up-database
# core.local：存储目录已准备后
make up-data
# research.local：市场只读、研究目录可写后
make up-research
# Live 所在主机，仍使用当前独立部署
make up-live
```

三个工作台需要分别登录。首次 `deploy` 自动生成并显示该应用的随机密码，后续部署保留密码。
自定义或忘记密码时运行 `uv run --project backend northstar maintenance password-hash`，
把输出的 `NORTHSTAR_WORKSPACE_PASSWORD_HASH='...'` 加入该应用私有 `.env`，再用 `deploy <应用> --env-file <文件>` 更新。
API 重启后重新登录；登录不会连接柜台或开启交易。手动 Make/Compose 启动前也须在运行配置中填写摘要。

本机 Make 命令读取 `/opt/northstar/config/<应用>.env`，不提供路径覆盖。
正式构建要求工作区干净；未提交修改时在对应命令前加 `NORTHSTAR_DEVELOPMENT_BUILD=1`。
Data Hub 连接 core 独立数据库；Research 使用本机 SQLite。启动检查目录和存储身份，不操作底层存储或另一个应用。
Data API 持久排队，独立 worker 下载与加工；关闭 Data Web/API 不停止已提交任务。
Data Hub 只通过 Tushare 自动同步全部期货历史数据，不提供文件导入、Tick、品种或周期选择。
打开 Data Hub → **历史同步**，保存 token 后点击 **开始同步全部数据**；可查看分片进度、等待原因与固定数据。
分片 **记录** 展示质量问题的原文行号、字段和原因；**版本与来源** 可选择同一分片的两个固定版本，比较新增、删除及字段变化。
在分片的 **记录** 中可点击 **重处理已留存响应**，使用当前规则重新校验最新原文，不重新下载；同步暂停时保留排队，失败不覆盖旧版本。
凭据保存在 core 本地 `/opt/northstar/credentials/data-hub/`，API 不回显 token；前端/API 停止不影响独立 worker。
本地开发需为 API 和 worker 设置同一个绝对路径 `NORTHSTAR_DATA_SECRET_DIR`（私有目录权限 0700）。
同步范围、备份与当前研究语义限制见 [架构](docs/ARCHITECTURE.md#4-数据与时间)。

三个应用部署后可通过所属主机直接访问；也可使用所属主机的局域网 IP（例如 `http://192.168.50.10:18082`）。
部署自动开放三个前端端口，不限制来源 IP，也不需要登记访问 IP；保留同源和会话校验。

| 应用 | 公网地址（Caddy/FRP） | 直接访问 |
|---|---|---|
| Data Hub | <https://datahub.wangqiwen.me> | <http://core.local:18082> |
| Research | <https://research.wangqiwen.me> | <http://research.local:18084> |
| Live | <https://live.wangqiwen.me> | Live 主机 IP:18080 |

三个应用仍支持本机 `127.0.0.1` 和 SSH 隧道；Python API 端口不向局域网开放。

`make ps-data` / `ps-research` / `ps-live` 查看状态；`make down-data` / `down-research` / `down-live` 只停止对应应用并保留主机数据目录。
core 上的数据库单独使用 `make ps-database` / `down-database`；停止会影响 Data Hub，不会停止 Research 或 Live 的本地存储。

修改代码后重新构建；`restart` 不会构建新代码。端口、目录、存储权限与备份见 [部署说明](deploy/README.md)。
当前个人容器与数据不会被新命令自动迁移或接管。

本地启动 Research 前，创建专用本机目录，设置 `NORTHSTAR_RESEARCH_DATABASE=/绝对路径/research.sqlite3`，
并运行 `NORTHSTAR_DATABASE_OWNER=research northstar maintenance init-db`。Research API 使用同一 SQLite 文件环境变量；CLI `research` 命令自动选择此本地库。

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
core PostgreSQL 仅保存 Data Hub 元数据；Research 的任务与结果保存在 research 本机 SQLite。
Research 用 Data Hub API 获取固定清单，通过只读市场目录和本机 DuckDB 读取 Parquet。
配置 `NORTHSTAR_DATA_HUB_URL` 即可访问只读发布接口，无需 token，也不向 Research 分发 core 数据库口令。
Live 使用实例本地 SQLite 与来源文件；启动和联合恢复检查已保存事实，恢复不会自动连接柜台或重新授权。
每实例独立 `live-monitor` 输出内核、存储和订单健康 JSON 告警；外部通知与整机失联检测尚未验收。
Research 回测由独立 `research-worker` 执行；前端和 API 重启不结束已接收的任务。
进程隔离不能隔离整台主机故障；实际外部柜台闭环仍需单独验收。

### 后端日志

三个 Python 后端自动写入独立 JSON 行日志。默认根目录是当前工作目录下的 `.northstar/logs/`：

| 应用 | 文件 |
|---|---|
| Data Hub | `data_hub/northstar-data-hub-api-YYYY-MM-DD.log`、`data_hub/northstar-data-hub-worker-YYYY-MM-DD.log` |
| Research | `research/northstar-research-api-YYYY-MM-DD.log`、`research/northstar-research-worker-YYYY-MM-DD.log` |
| Live | `live/northstar-live-api-YYYY-MM-DD.log`、`live/northstar-live-kernel-YYYY-MM-DD.log` |

Compose 把日志保存在 `/opt/northstar/logs/<应用>/`，容器内路径为 `/var/log/northstar/`，重建容器仍保留。
例如 `docker compose --env-file /opt/northstar/config/live.env -f deploy/live/compose.yaml exec live tail -n 50 /var/log/northstar/live/northstar-live-kernel-YYYY-MM-DD.log`。
将 `YYYY-MM-DD` 替换为日志日期。日期采用进程所在时区，跨日后的首次后台写入自动切换文件。
每个文件默认 10 MiB，超限后增加 `.1`～`.5` 后缀；每个程序除当前文件外，跨日期和大小轮转合计最多保留 5 份历史文件（按修改时间保留最新）。Python 运行日志写文件；
容器启动错误仍可通过 `docker compose --env-file /opt/northstar/config/research.env -f deploy/research/compose.yaml logs --tail=100 research-api` 查看。

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
make up-research
docker compose --env-file /opt/northstar/config/research.env -f deploy/research/compose.yaml stop research
NORTHSTAR_API_URL=http://127.0.0.1:19084 npm --prefix frontend run dev:research
```

Data Hub 对应 `dev:data`、后端 `19082`；Live 对应 `dev:live`、后端 `19080`。
开发与日常访问使用相同端口。Next.js 开发服务支持热更新，修改 Python 代码仍需更新对应 API 服务。

本机 Python 入口为 `uv run --project backend northstar serve data-api`、`uv run --project backend northstar serve research-api`、
`uv run --project backend northstar serve live-api`（Live 管理 API）和 `uv run --project backend northstar serve live-kernel`（内核）。
`uv run --project backend northstar serve data-worker` 启动独立数据执行器，需要与 Data API 使用相同数据库、来源目录和代码版本。
`uv run --project backend northstar serve live-monitor` 启动独立只读健康观察，使用初始化生成的 `monitor/read.toml` 和所属实例的内核地址。
前三个默认监听表中的 `190xx` 端口；不要与同端口容器同时启动。
Data Hub 配置 `NORTHSTAR_DATABASE_URL`；Research 使用 `NORTHSTAR_RESEARCH_DATABASE` 本机 SQLite，Live 使用实例本地状态目录（见部署模板）。
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
| `backend/src/northstar_quant/market_data/` | 无数据库依赖的行情值与时间/数值校验 |
| `backend/src/northstar_quant/{data_management,research,live}/` | 数据业务、研究流程与生产会话 |
| `backend/src/northstar_quant/{factors,strategies,risk,execution,broker,accounting,simulation}/` | 因子、策略、风险、执行、柜台、账户与模拟能力 |
| `backend/src/northstar_quant/{web,cli}/` | 共享 Web 接入机制与薄命令行入口 |
| `deploy/`、`backend/tests/` | 部署配置与行为验证 |
| `scripts/operations/` | 本机 Compose 执行、远程部署和挂载预检 |
| `scripts/protocol/` | Protobuf 与浏览器代码生成 |
| `scripts/acceptance/` | 安装、浏览器、容器和恢复验收 |

接口以根目录 `proto/` 下的 `data_hub.proto`、`research.proto`、`live.proto` 为准，生成 Python 消息、TypeScript 类型与浏览器调用声明。
变更协议后执行，不手工编辑生成文件：

```sh
npm --prefix frontend run api:generate
npm --prefix frontend run check
```

详细定义位置与变更流程见 [接口说明](docs/API.md)；新增算法见 [因子与策略开发](docs/FACTOR_STRATEGY_DEVELOPMENT.md)。
架构长期对照 NautilusTrader；具体取舍与参考链接见[架构设计](docs/ARCHITECTURE.md#0-设计依据与运行模型)。
Research/Paper 与 Live 共用策略运行时，分别使用历史模拟成交和柜台事实；不依赖 NautilusTrader 运行库。
CLI 按 `serve`、`status`、`check`、`data`、`research`、`maintenance`、`advanced` 分组。
使用 `northstar --help` 查看入口，具体命令与示例见 [命令行说明](docs/CLI.md)。
Web 与 CLI 调用同一套业务规则，CLI 不另行实现采集、回测或交易逻辑。

## Data Hub 数据浏览

首页按数据类型显示分片概况；`/browse` 按交易所、品种、合约、周期和自然日期查看固定数据，
`/quality` 查看日期覆盖与异常并跳转到同步任务，`/versions` 查看与重开历史发布分片。
浏览筛选不改变全量同步范围，不增加手工导入或 Tick。

图表显示当前分页的原始 K 线、成交量和可用持仓量，明细保留精确文本；不补价格、不推断夜盘归属。
分钟“响应已校验”不等于分钟完整，日线覆盖验证与非交易日单独标注。
查询首次固定分片版本，翻页不跟随后台修订；版本冲突与文件损坏明确拒绝。
范围导出也检查来源权限，当前自动留存的 Tushare 原文未开放导出，按钮会说明原因。
这些供应商历史版本尚不等于完成跨日语义的 Research 快照。

## Research 研究流程

首页查看近期运行；`/experiments/new` 选择固定快照和策略配置提交回测，`/tasks/<任务身份>` 查看进度、原因、尝试和结果。
`/experiments` 筛选任务与比较同条件结果；报告显示权益、回撤、资金持仓、模拟成交和策略/Risk 决定。
`/candidates` 登记固定策略版本并关联研究证据，发布候选不授予交易权限。

Compose 自动启动独立 `research-worker`；本机可用 `uv run --project backend northstar serve research-worker` 启动，
与 API 使用同一份 Research 本地 SQLite 和市场/研究存储配置。没有 worker 时任务排队，浏览器关闭不丢任务。
取消先记录请求，计算确认后才显示已取消；保存结果阶段取消窗口关闭。中断保留尝试，可显式重试相同输入；代码变化须新建任务。
已有 CLI `research run` 是明确的前台有界运行，仍调用同一研究计算。
支持固定快照及显式结算事实驱动的跨日计算；真实有效条款、完整保证金、样本外评价和参数优化仍待交付。

## 验证

准备专用、可清空的 PostgreSQL 数据库 `northstar_quant_test` 后执行：

```sh
export NORTHSTAR_TEST_DATABASE_URL='postgresql+psycopg://northstar:northstar_local@127.0.0.1:15432/northstar_quant_test'
make verify
```

`make verify` 检查接口生成同步、前端类型与构建、前端测试、Python 格式与类型及业务测试。
测试会重置测试库，不能指向应用数据库。安装验收与浏览器验收另由
`scripts/acceptance/check_install.py`、`scripts/acceptance/check_browser.py` 执行；独立容器验收使用
`scripts/acceptance/check_application_deployment.py` 和 `scripts/acceptance/check_live_deployment.py`。CI 配置见 [.github/workflows/ci.yml](.github/workflows/ci.yml)。

SimNow 凭据填写在 `deploy/live/.env` 的四个 `NORTHSTAR_SIMNOW_*` 配置项中，
仅传给 Live 内核，不传给前端/API；不再使用独立凭据文件或配置向导。
值用单引号包裹，避免 `$` 被 Compose 当作变量；真实凭据不要提交 Git。
已有部署更新配置时使用 `northstarctl.py deploy live --env-file deploy/live/.env`。
`NORTHSTAR_LIVE_INSTANCES=sim:simnow_trading,dev:simnow_dev` 定义两个独立实例：第一套模拟与开发环境。
网页顶部选择实例，各实例的内核、本地 SQLite、认证和日志独立；例如 `northstarctl.py restart live --instance dev`
只重启 dev。实例环境/账户固定，不能改配置复用另一账户的数据库；实盘仍未开放。详见 [Live 部署](deploy/live/README.md)。

## 数据与运行维护

三个应用的文件、日志、凭据与状态固定保存在 `/opt/northstar/` 下的所属目录，Live 内核拥有独立本地状态目录。重建容器不会清空这些数据；不要用 `docker compose down -v` 停止日常应用。
PostgreSQL 活跃目录保留在 core 本机，Research SQLite 保留在 research 本机，使用各自主机的本地持久磁盘。数据库与其引用的来源文件必须一起备份，市场数据、备份和私密凭据不提交到 Git。

```sh
# 自动创建新的备份子目录
make backup-database
```

恢复时准备空数据库和新的独立来源目录，设置 `NORTHSTAR_DATABASE_URL`、`NORTHSTAR_DATA_DIR`，
再运行 `northstar maintenance restore /absolute/backup-directory`，不要预先执行 `maintenance init-db`。
恢复不会覆盖已有数据库，也不会自动恢复行情连接或交易权限。
`maintenance init-db` 只接受当前存储结构，不自动重置已有数据；存储结构不匹配时应先保全所需证据，再明确处理。

Data Hub 的固定查询结果可提交后台合并，旧版本和逐行来源保持可读。
清理先运行 `northstar maintenance prune-data` 查看无引用对象，再用
`northstar maintenance prune-data --apply <plan_id>` 执行该清单；引用变化时拒绝。
一次最多 500 个对象，保留所有来源/发布/备份引用，不处理临时 staging，也不因合并删除旧版本。

当前回测与 Paper 支持单合约、固定多日时段、跨日结算、有效费用/保证金条款和受量约束的部分成交；
仍不模拟真实排队优先级或市场冲击，也不会在数据结束时自动平仓。真实历史条款与 Tushare 研究清单联合验收尚未完成。
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


### 验收证据

安装态验收可设置 `NORTHSTAR_ACCEPTANCE_ARTIFACTS=/绝对路径/证据目录`。
输出实际安装实现身份、包/Python/PostgreSQL/SQLite 版本、存储 UUID、检查结果及脱敏进程日志；恢复阶段单独记录存储绑定。
CI 无论成功或失败均保留 `acceptance-<SHA>-<attempt>` 产物 14 天，避免临时目录清理后丢失失败诊断。
源码 SHA、主机当前版本、镜像 ID 和验收结论应分别记录，旧部署健康不代表新提交已验收。
三个工作台的身份认证现状见 [API 边界](docs/API.md#工作台身份边界)。
