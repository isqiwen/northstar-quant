# 报告、PDF 与通知

报告是研究和运行审计制品，不是重新计算策略结果的入口。回测报告的指标、数据来源、有效
配置、目标权重、代码指纹和结果校验和由 `manifest.json` 冻结；阅读报告时应先确认
`run_id`、数据集和研究准入结论。

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
uv run northstar report daily --profile cn_futures_daily_trend_offline
uv run northstar report weekly --profile cn_futures_daily_trend_offline
uv run northstar report monthly --profile cn_futures_daily_trend_offline
uv run northstar report yearly --profile cn_futures_daily_trend_offline
```

生成或重渲染已有制品的 PDF：

```powershell
uv run northstar report pdf <report.md 的路径>
```

回测报告的研究准入、数据质量和统计样本不足会如实显示。报告生成成功不等于策略通过
候选研究准入，更不等于获得 simulated、CTP 或真实资金授权。

报告、标准市场数据和回测 manifest 是研究证据，项目没有自动删除它们的任务。`northstar data
cleanup` 只处理下载缓存和明确安全的临时文件；具体双重确认与范围见[配置说明](02_配置说明.md)。

## 邮件通知

仅在操作者已在本地 `.env` 配置 SMTP 和收件人后，才显式请求发送：

```powershell
uv run northstar report daily --profile cn_futures_daily_trend_offline --send-email
uv run northstar report send <report.md 的路径> --attach-pdf
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

SMTP 密码、收件人和任何外部通知凭据只保存在本地环境变量或受控密钥系统，绝不能提交到
仓库。若未配置收件人，发送器会跳过发送并记录原因；不要依赖一次“未报错”的调用来确认
邮件已送达。

## 调度边界

当前没有 production 画像，因此 `northstar live scheduler` 不能作为周期报告或邮件的可用
启动方式；它会失败关闭。人工生成离线报告不需要启动调度器。将来生产调度启用前，必须先
满足 [执行与安全边界](03_执行与安全边界.md) 与
[项目主规划与实施状态](08_项目主规划与实施状态.md) 的全部门禁。
