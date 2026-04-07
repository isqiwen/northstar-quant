# 正式版 PDF 报告版式说明

本版本对 PDF 报告做了正式化升级，新增：

- 封面页
- 页脚页码
- 关键指标卡片页
- 图表页
- 正文审计页

## 设计目标

1. 更适合归档和打印
2. 更适合作为周报 / 月报正式附件发送
3. 仍然以 Markdown 作为单一事实来源

## 渲染流程

```text
Markdown 报告
    ↓
解析元信息 / 指标 / 持仓
    ↓
生成正式版 PDF
    ↓
邮件自动附加 PDF
```

## 命令示例

```bash
northstar report pdf reports/etf_rotation_daily_report.md
northstar report daily --send-email --send-pdf
```
