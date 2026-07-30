# 正式版 PDF 报告版式说明

本版本对 PDF 报告做了正式化升级，新增：

- 封面页
- 页脚页码
- 关键指标卡片页
- 图表页
- 正文审计页

## 设计目标

1. 更适合归档和打印
2. 更适合作为周报 / 月报 / 年报正式附件发送
3. Markdown 保存正文，配套 JSON 保存指标与图表数据

## 渲染流程

```text
report.md + report.json
    ↓
解析元信息 / 指标 / 持仓
    ↓
生成正式版 PDF
    ↓
邮件自动附加 PDF
```

## 命令示例

```bash
northstar report pdf reports/daily/cn_futures_daily_trend_offline/futures_trend/20260730/report.md
northstar report daily --send-email --send-pdf
```
