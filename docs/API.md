# 前后端协议

三个应用使用独立 Next.js 前端与 Python API。接口的权威定义是 Protobuf，
前后端通过 HTTP 传输 `application/protobuf` 二进制消息；不使用 OpenAPI 生成链路，也不要求 gRPC 网关。

协议源码统一放在仓库根目录 `proto/`，前后端消费同一份定义，不在实现目录保留副本。
协议仍按应用划分所有权；业务规则和校验留在所属模块。

## 定义与生成

| 位置 | 内容 |
|---|---|
| `proto/data_hub.proto` | 数据来源、加工、快照接口 |
| `proto/research.proto` | 配置、因子、研究、Paper 与候选接口 |
| `proto/live.proto` | 运行观察、账户核对、接收控制与材料接口 |
| `proto/api_options.proto` | HTTP 方法与路径绑定、字段约束选项 |
| `proto/common.proto` | 通用 Empty、Error 消息 |
| 各应用的 `*_api.py` | Python HTTP 处理与业务调用、业务输入校验 |
| `backend/src/northstar_quant/apps/<应用>/api_pb2.py` / `.pyi` | protoc 生成的 Python 消息及类型 |
| `frontend/apps/<应用>/api/` | 生成的 TypeScript 类型、Protobuf 描述、静态编解码器与调用声明 |

协议文件名与实现目录分离，消息命名空间由各文件的 `package` 声明。
Python 生成步骤通过显式模块映射，将 protoc 生成的导入及模块名调整到所属实现包；公共消息与选项进入 `backend/src/northstar_quant/web/`。
生成脚本以 `proto/` 为输入根目录，将 Python 代码写入 `backend/src/`，将浏览器代码写入 `frontend/`。

安装依赖后，在仓库根目录执行：

```sh
uv sync --project backend --locked
npm --prefix frontend ci
npm --prefix frontend run api:generate
npm --prefix frontend run check
```

`api:check` 检查生成产物是否同步，`make verify` 与 CI 包含该检查。
生成过程只编译 `.proto`，不装配业务应用、不访问数据库或柜台。编解码器在构建时生成，浏览器不使用运行时动态代码生成，页面 CSP 不开放生产 `unsafe-eval`。

## 通信与职责

浏览器使用生成的请求函数，经 Next.js 的 `/api/...` 转发到本应用 Python API。
Next.js 不解释或修改业务消息，不访问数据库，也不持有柜台凭据。
Python 解码 Protobuf 后进入所属业务校验与操作；风险、账本、授权等判断仍由业务所有者执行。

Data Hub 的 `/api/sync/token` 是 token 的只写入口，`/api/sync/settings` 只接受设置版本和启停状态；
`/api/sync` 返回自动同步进度，任务和固定响应分别通过 `/api/sync/jobs/{id}`、`/api/sync/receipts/{id}` 查询。
没有手工导入、品种/周期选择或 Tick 协议。同步执行归独立 worker，API 不持有下载任务生命周期。

浏览器会话与 CSRF 仍由所属 API 校验，Live 命令还需要 `X-Live-Runtime-Id`。
请求保留固定身份；超时、断连或无法解码的回执显示结果未知，禁止自动重发。
健康检查使用 JSON；原始文件与候选下载保持原始文件格式，不伪装成业务消息。
Live 管理 API 与内核的内部认证 HTTP 是另一条接口，本次前后端协议不改变其执行权边界。

## 字段语义

- 金额、价格和比例使用十进制字符串，不能改用浮点数。
- 计数与序号使用整数；浏览器只处理 JavaScript 可精确表示的整数范围。
- 必需字段与可空字段通过协议选项明确区分；消息的 `null_fields` 标明显式为空的字段，未列出的缺省字段仍为未提供。该传输字段不进入业务对象，不能与实际值同时提供。缺少身份不能被默认值掩盖。
- 固定字段使用具名消息；原始 SDK 证据、加工参数及算法扩展参数使用明确的 `Struct` / `Value` 字段。
- 扩展证据不能覆盖具名字段。加工参数由数据业务在保存加工尝试后校验，以保留失败证据。
- Protobuf 的消息字节不作为数据、配置或账本的内容身份；原有业务身份规则保持不变。

当前只维护一份协议，未知请求字段会被拒绝，不保留旧 JSON 接口或版本兼容分支。
另一种语言可从同一 `.proto` 生成消息实现，但仍须实现相同 HTTP 绑定、认证、字段约束及业务语义。
更换实现语言可以不改变调用方；改变协议本身仍需协调双方。

## 跨主机发布接口

Data Hub 的 `/api/publications` 返回固定快照目录，`/api/publications/{snapshot_id}` 返回存储 UUID、相对路径、长度、SHA-256 和 Parquet 文件清单。
协议仍由 `proto/data_hub.proto` 定义；Research 后端使用独立 Bearer 凭据获取清单，不获取 core 数据库凭据。
Docker 仅通过 core 私网 `19083` 的只读网关开放这些 GET；`19082` 管理 API 保持本机绑定。
Research 按清单校验 发布目录中的文件，在本机 DuckDB 查询；不扫描目录拼接新旧版本。凭据、网络或文件校验失败明确报错。

## Data Hub 浏览接口

- `GET /api/explorer`：数据类型、交易所、品种与分片概况。
- `POST /api/explorer/contracts`：受限合约搜索。
- `POST /api/explorer/coverage`：自然日期窗口、当前任务、日历及校验依据。
- `POST /api/explorer/versions`：范围内不可变发布版本，按页返回。
- `POST /api/explorer/query`：首次选择固定分片，后续以 receipt_ids 复用相同数据；返回精确明细、字段统计和血缘。
- `POST /api/explorer/export`：固定版本的受控分页导出，来源未许可返回 403。

这些 POST 是带浏览器会话保护的只读查询，不更改下载范围或任务状态。源协议在 `proto/data_hub.proto`。

## Research 持久任务

`POST /api/tasks` 固定请求身份、快照和完整配置，返回 202 与任务身份；不在 HTTP 请求里计算。
`GET /api/tasks`、`GET /api/tasks/{task_id}` 查看进度、原因、尝试、固定证据与结果。
`POST /api/tasks/{task_id}/control` 的 action 为 cancel 或 retry；取消请求不代表已经停止。
`GET /api/runs` 与详情读取已保存结果，`POST /api/run-comparisons` 执行同条件比较。
所有消息由 `proto/research.proto` 定义。
