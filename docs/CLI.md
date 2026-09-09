# 命令行使用

CLI 用于启动、检查、维护和自动化。日常数据管理、研究与 Live 控制以网页为主。
以下示例中的 `northstar` 是安装后的命令；源码环境在仓库根目录加上 `uv run --project backend`。

## 常用入口

| 命令 | 用途 |
|---|---|
| `northstar serve data-worker` | 独立执行已接收的数据加工任务，无监听端口 |
| `northstar serve data-api` | 启动 Data Hub API，默认端口 19082 |
| `northstar serve research-api` | 启动 Research API，默认端口 19084 |
| `northstar serve live-api` | 启动 Live 管理 API，默认端口 19080 |
| `northstar serve live-kernel` | 启动独立 Live 内核，默认端口 18081 |
| `northstar status` | 查看 Live 内核身份、状态和能力 |
| `northstar check` | 检查 Live 内核与存储，异常时返回非零退出码 |

API 和内核的 `serve` 命令支持 `--port`，仅监听本机；`data-worker` 不接收端口参数。Next.js 前端使用 Compose 或前端 npm 命令启动；
应用分别使用根目录的 `make up-data`、`make up-research`、`make up-live` 启动，环境配置见 [README](../README.md)。
`status`、`check` 使用 LiveClient；内核不可用时明确失败，不会替你启动内核。

## 数据与研究自动化

```sh
northstar data import examples/intraday.toml
northstar data datasets
northstar data dataset <snapshot-id>
northstar research run <snapshot-id> --study examples/intraday.toml
northstar research list
northstar research show <run-id>
northstar research replay <run-id>
```

`data import <study.toml>` 在 Data Hub 发布固定快照；`research run <snapshot_id>` 在 Research 消费发布产物，两者使用各自数据库账号。
CLI 导入和重新加工调用数据业务的持久接收、认领和加工，等待结果退出；网页仅排队，由 `data-worker` 执行。
两者复用来源及失败证据，不另建采集或回测实现。
`research configure` / `research configurations` 管理固定配置；
`research paper` 下的 `create`、`list`、`show`、`next` 操作文件回放模拟账户。

## 维护

```sh
northstar maintenance init-db
northstar maintenance init-auth <directory>
northstar maintenance backup <new-directory>
northstar maintenance restore <backup-directory>
northstar maintenance audit-data
```

备份需要维护窗口，数据库及引用文件一起保存。恢复只接受空数据库和新来源目录，
不要提前初始化恢复目标。`init-auth` 生成的是 Live 内部认证文件，不是柜台凭据。

## 高级运维

`northstar advanced --help` 查看高级操作：

- `advanced broker`：明确的 SimNow 查询，以及基线、资金、持仓、委托和预算证据处理。
- `advanced stream`：有时限的行情/账户回报接收、控制、观察、账户处理及归档。
- `advanced receipt <request-id>`：查询已提交命令的结果，不重新提交。

高级命令包含会创建记录或发起接收的操作，并非全部只读诊断。
查看帮助、状态或历史证据不会自动连接柜台；`query` 和 `start` 是显式接入操作。
CLI 把 Live 操作交给所属运行时，最终权限、状态和业务约束仍在执行处检查。
预算计算、证据一致或启动成功都不授予真实交易权限。

## 参数、结果与错误

每一级都支持 `--help`，例如 `northstar research paper create --help`。
金额参数使用精确十进制文本，身份参数使用 UUID。要求 `--request-id` 的命令必须提供固定身份；
超时或断连后先用 `advanced receipt` 查询，不自动生成新身份或重发未知命令。
数据导入可省略请求身份；需要重试去重时应显式提供并复用它。

操作结果输出 JSON，错误写入标准错误。退出码 0 表示完成并满足对应检查条件；
退出码 2 表示参数错误、运行失败或未满足检查条件。服务启动命令持续运行。
只维护当前分组命令，不接受旧的平铺命令别名。

## EDB 可选文件获取

更新后先使用数据库维护账号执行 `northstar maintenance init-db`，安装当前来源格式约束。
在 Data Hub 主机使用其数据库和来源目录配置，下载一个真实 SHFE 合约的连续日盘子时段：

```bash
northstar data collect-edb examples/edb.toml --request-id <本次接收的UUID>
northstar serve data-worker
northstar data attempt <返回的attempt_id>
```

第一条下载并保存原始 CSV，返回 PENDING；独立 worker 执行质量检查与发布。
已有 worker 时不必再启动。网页加工队列、来源和任务详情可查询后续结果。
配置只含 `[source]`，无需本地 CSV 或研究参数；示例为北京时间 13:30–15:00。
修改日期时同时修改两个 UTC 时刻，并核对合约与 tick/乘数。

当前限定近 364 天、已结束、最长两小时的连续日盘范围，固定免费 EDB 地址、无 token、无重定向与自动重试，响应最多 5 MiB。
跨午休、缺失或重复分钟会失败并保留原文，不补价格。按 FINAL_REVISED 的分钟结束时钟研究，不能声称历史首次可得。
请求 URL、原始内容哈希和映射版本一起留存；持仓量保留在原文，本轮不映射为研究输入。
重复 UUID 和相同响应复用已接收任务；响应变化则拒绝覆盖。重试下载仍会访问来源。
下载在 CLI 中执行，退出码 0 表示已经接收，不等于发布成功；下载失败前尚无持久采集任务。
定时计划、下载中断恢复、增量水位与补数尚未实现，已后置；该命令不等于行情自采 Recorder。
