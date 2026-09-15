# 命令行使用

CLI 用于启动、检查、维护和自动化。日常数据管理、研究与 Live 控制以网页为主。
以下示例中的 `northstar` 是安装后的命令；源码环境在仓库根目录加上 `uv run --project backend`。

## 常用入口

| 命令 | 用途 |
|---|---|
| `northstar serve data-worker` | 独立执行已接收的数据加工任务，无监听端口 |
| `northstar serve data-api` | 启动 Data Hub API，默认端口 19082 |
| `northstar serve research-worker` | 独立执行已持久接收的研究任务 |
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
northstar data sync
northstar data datasets
northstar data dataset <snapshot-id>
northstar research run <snapshot-id> --study backend/tests/data/intraday.toml
northstar research list
northstar research show <run-id>
northstar research replay <run-id>
```

在 Data Hub 网页配置 token 并启用全部历史同步；`data sync` 只查询持久进度。
`research run <snapshot_id>` 消费已具备研究语义的固定快照，使用 Research 自己的数据库。
没有 CLI 文件导入、手工重处理或指定区间下载命令。
`research configure` / `research configurations` 管理固定配置；
`research paper` 下的 `create`、`list`、`show`、`next` 操作文件回放模拟账户。

## 维护

```sh
northstar maintenance init-db
northstar maintenance init-auth <directory>
northstar maintenance backup <new-directory>
northstar maintenance restore <backup-directory>
northstar maintenance audit-data
northstar maintenance prune-data                    # 预览孤立对象
northstar maintenance prune-data --apply <plan_id>  # 按刚预览的固定清单清理
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

操作结果输出 JSON，错误写入标准错误。退出码 0 表示完成并满足对应检查条件；
退出码 2 表示参数错误、运行失败或未满足检查条件。服务启动命令持续运行。
只维护当前分组命令，不接受旧的平铺命令别名。

## Tushare 自动同步

```sh
northstar data sync
northstar serve data-worker
```

worker 由部署系统独立监管，已有 worker 时不重复启动。网页设置 token、开始/暂停或重试异常任务；
不支持选择品种、级别、Tick 或上传文件。同步全部可用期货历史接口，缺少权限明确显示。
`data sync` 输出进度、区间及失败原因，不输出 token。暂停在当前分片提交后停止认领，重启后保留设置与任务。

Research 持久任务可用 `northstar research tasks` 列出，`northstar research task <任务UUID>` 查看；
`northstar research cancel <任务UUID>` 请求取消，`northstar research retry <任务UUID>` 显式重试失败或中断任务。
它们访问 Research 本机 SQLite，不需要启动管理网页。
