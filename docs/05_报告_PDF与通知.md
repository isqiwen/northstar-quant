# 报告、PDF 与通知

报告是研究和运行审计制品，不是重新计算策略结果的入口。回测报告的指标、数据来源、有效
配置、目标权重、代码指纹和结果校验和由 `manifest.json` 冻结；阅读报告时应先确认
`run_id`、数据集和研究准入结论。

通知按用途分层：统一本地日志是审计底线；私有部署 ntfy 是唯一的外部即时告警通道；邮件只负责
投递已经生成的报告。三者不能互相替代，企业微信与 Telegram 不再支持。

## 报告制品

每个报告目录至少包含：

| 文件 | 用途 |
| --- | --- |
| `report.md` | 面向人工阅读的 Markdown 正文。 |
| `report.json` | 指标、图表和结构化审计数据。 |
| `manifest.json` | 回测制品的输入、配置、代码和结果指纹；周期报告不一定包含。 |
| `report.pdf` | 按需生成的阅读/归档副本。 |

官方报告位于 `runtime.reports_dir/<类型>/<画像>/<策略>/<周期>[/<run-id>]/`。回测使用
`run-id` 作为不可变制品身份；周期报告按其周期更新同一目录。Dashboard 会递归发现同时拥有
有效 `report.json` 且 `artifact_id` 与目录一致的 `report.md`，不会把手工 Markdown 或残缺
目录误认为正式报告。

PDF 从现有 `report.md` 与 `report.json` 渲染，不会改变 Markdown、JSON 或回测 manifest。
多空期货持仓会以正负方向展示；全空仓或全空头组合会明确标注，而不是伪造饼图。

## 生成报告

周期报告会运行画像对应的回测工作流并写入新的报告制品。默认策略选择为 `portfolio`，
即画像中所有启用策略；只有需要明确缩小策略范围时才传入单一策略 ID。

```powershell
uv run --offline --no-sync northstar report daily --profile cn_futures_daily_trend_offline
uv run --offline --no-sync northstar report weekly --profile cn_futures_daily_trend_offline
uv run --offline --no-sync northstar report monthly --profile cn_futures_daily_trend_offline
uv run --offline --no-sync northstar report yearly --profile cn_futures_daily_trend_offline
```

生成或重渲染已有制品的 PDF：

```powershell
uv run --offline --no-sync northstar report pdf <report.md 的路径>
```

回测报告的研究准入、数据质量和统计样本不足会如实显示。报告生成成功不等于策略通过
候选研究准入，更不等于获得 simulated、CTP 或真实资金授权。

报告、标准市场数据和回测 manifest 是研究证据，项目没有自动删除它们的任务。`northstar data
cleanup` 只处理下载缓存和明确安全的临时文件；具体双重确认与范围见[配置说明](02_配置说明.md)。

## 即时告警：私有 ntfy

ntfy 只发送运行、风控、对账和执行异常的**简要**即时提示；它不附带报告、PDF、交易凭据、数据库
连接信息、CTP 前置地址或完整账户/持仓明细。告警外发前会先进入统一日志，因此 ntfy 不可达、手机
离线或消息延迟时，仍应以本地日志、运行健康记录和报告制品为审计依据。

只允许连接自建的 HTTPS ntfy 服务；仅本机开发验证可使用 loopback HTTP。在本机未跟踪的 `.env` 中，
按 `.env.example` 的 ntfy 字段填写私有服务地址、主题与发布令牌；不要把值写入示例文件、命令行参数
或仓库。服务端应默认拒绝访问，
为应用创建仅能写入指定主题的发布身份，并为手机订阅创建独立的只读身份。不得改用公共 `ntfy.sh`，
也不得配置任何从通知反向触发下单、撤单或 kill switch 的动作。

Docker/Caddy 私有服务、首次身份初始化和移动端送达边界见
[Linux 一键部署](07_Linux一键部署.md#私有-ntfy可选)。

## 邮件报告投递

邮件只投递已经生成的报告及其可选 PDF 附件，不承担实时告警职责，也不会作为 ntfy 的自动回退。仅在
操作者已在本地 `.env` 配置 SMTP 和收件人后，才显式请求发送：

```powershell
uv run --offline --no-sync northstar report daily --profile cn_futures_daily_trend_offline --send-email
uv run --offline --no-sync northstar report send <report.md 的路径> --attach-pdf
```

周期报告命令的 `--send-pdf/--no-send-pdf` 与 `report send` 的
`--attach-pdf/--no-attach-pdf` 默认均为附加 PDF。`NORTHSTAR_REPORT_EMAIL_ATTACH_PDF`
只在调用方没有显式指定附件行为时作为默认值；不要把它理解为覆盖所有 CLI 参数。

常用本地环境变量：

```text
NORTHSTAR_SMTP_HOST=
NORTHSTAR_SMTP_PORT=465
NORTHSTAR_SMTP_USERNAME=
NORTHSTAR_SMTP_PASSWORD=
NORTHSTAR_SMTP_SENDER=
NORTHSTAR_SMTP_USE_SSL=true
NORTHSTAR_REPORT_RECIPIENTS=
NORTHSTAR_REPORT_EMAIL_SUBJECT_PREFIX=Northstar Quant
NORTHSTAR_REPORT_EMAIL_ATTACH_PDF=true
```

SMTP 密码、收件人和 ntfy 发布令牌只保存在本地环境变量或受控密钥系统，绝不能提交到仓库。
若未配置收件人，发送器会跳过发送并记录原因；不要依赖一次“未报错”的调用来确认邮件已送达，
更不能据此确认即时告警已送达。

## 调度边界

当前没有 production 画像，因此 `northstar live scheduler` 不能作为周期报告、邮件投递或即时告警的可用
启动方式；它会失败关闭。人工生成离线报告不需要启动调度器。将来生产调度启用前，必须先
满足 [执行与安全边界](03_执行与安全边界.md) 与
[项目主规划与实施状态](08_项目主规划与实施状态.md) 的全部门禁。
