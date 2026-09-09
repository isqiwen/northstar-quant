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

## Tushare 历史同步

先使用 Data Hub 数据库维护账号运行 `northstar maintenance init-db` 安装当前同步表与来源约束。
在 Data Hub 私有部署配置中填写 `NORTHSTAR_TUSHARE_TOKEN`，仅 data-worker 使用，不提交 token 到仓库。
已有 EDB 实测记录的开发库不能自动转换成 Tushare 来源；本轮不修改个人数据库或旧原文。

```bash
northstar data sync-tushare examples/tushare.toml --request-id <本次请求UUID>
northstar serve data-worker
northstar data sync <请求UUID>
northstar data attempt <任务返回的attempt_id>
```

已有 worker 时不重复启动。网页入口为 Data Hub 首页 → Tushare 历史同步，提交同一类任务并查看最近状态。
CLI 提交返回 PENDING，先持久任务、后下载；RECEIVED 表示原文已进入加工，是否发布要查看加工结果。
下载失败或中断保留失败任务，需要明确新建请求；同 UUID 同参数返回原记录，不因重试重复下载。

配置只含 `[source]`，无需本地 CSV 或研究参数。示例是北京时间 13:30–15:00，两个时刻使用 UTC。
当前只支持过去日期、最长两小时的连续 SHFE 日盘一分钟范围，不能跨午休或夜盘；缺失/重复分钟不补造。
Tushare trade_time 按分钟结束解释，FINAL_REVISED 表示假定完成时可得，实际样本口径仍须核对。
当前没有定时增量/自动分片/水位和 15 分钟到日线完整输入支持，不能将有界同步当成全部功能已完成。
Data Hub 不提供实时录制命令，Live 的行情与交易命令仍由独立 Live 拥有。
