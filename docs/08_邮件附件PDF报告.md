# 邮件附件 PDF 报告

本版新增能力：

- 报告生成后，可把 Markdown 自动转换成 PDF
- 发送日报 / 周报 / 月报 / 年报邮件时，默认附加 PDF
- 仍然保留 Markdown 附件，便于审计和后续二次处理
- 新增 CLI 命令：`northstar report pdf`

## 一、设计说明

每份报告是一个目录化制品：

1. `report.md` 保存面向阅读的正文
2. `report.json` 保存指标、持仓和图表数据
3. PDF 渲染器同时读取两者生成 `report.pdf`
4. 发送邮件时附带 Markdown 和 PDF

这样做的好处是：
- Markdown 易于审计和版本管理
- JSON 适合可靠地传递结构化图表数据
- PDF 适合阅读、转发和归档
- Markdown 正文不需要混入供渲染器使用的 JSON

## 二、依赖

本版新增依赖：

- `reportlab`：负责 PDF 渲染

## 三、命令示例

### 1. 生成日报并附 PDF 发送

```bash
northstar report daily --send-email --send-pdf
```

### 2. 生成周报但不附 PDF

```bash
northstar report weekly --send-email --no-send-pdf
```

### 3. 手动把现有 Markdown 报告转成 PDF

```bash
northstar report pdf reports/daily/cn_futures_daily_trend_offline/futures_trend/20260730/report.md
```

### 4. 发送现有报告，并附加 PDF

```bash
northstar report send reports/daily/cn_futures_daily_trend_offline/futures_trend/20260730/report.md --attach-pdf
```

## 四、环境变量

```env
NORTHSTAR_REPORT_EMAIL_ATTACH_PDF=true
```

说明：
- 为 `true` 时，邮件发送默认附加 PDF
- 为 `false` 时，只有显式传入 `--attach-pdf` 才附加 PDF

## 五、当前 PDF 渲染支持范围

目前支持：
- 一级标题
- 二级标题
- 项目符号
- 普通段落
- Markdown 表格

这已经足够覆盖 Northstar Quant 当前日报 / 周报 / 月报 / 年报模板。
