"""正式版 PDF 报告渲染模块。

本模块把系统生成的 Markdown 报告升级渲染为更正式的 PDF 版式：
1. 封面页：突出报告标题、周期、策略与生成时间；
2. 目录页：提供正式文档结构感；
3. 风险提示页：补齐偏机构风格的说明与边界；
4. 关键指标卡片页：把常用绩效指标做成卡片；
5. 图表页：给出净值曲线、回撤曲线、月度收益热力表、持仓权重图；
6. 正文页：保留 Markdown 正文，满足审计与归档。

设计原则：
- Markdown 只承载人类可读正文，结构化指标与图表数据统一读取同目录的 report.json；
- PDF 渲染尽量正式、稳健、可维护；
- 中文可读性优先，不依赖外部字体文件；
- 缺少结构化报告数据时明确失败，避免生成内容不完整却看似正常的 PDF。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from reportlab.graphics import renderPDF
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.pdfmetrics import registerFont
from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from northstar_quant.foundation.reporting.artifacts import load_report_data


@dataclass
class ReportMeta:
    title: str = 'Northstar Quant 报告'
    generated_at: str = ''
    period_label: str = ''
    strategy_id: str = ''
    benchmark_symbol: str = ''
    report_type: str = ''


@dataclass
class MetricItem:
    key: str
    value: str


@dataclass
class ReportStructure:
    meta: ReportMeta = field(default_factory=ReportMeta)
    metrics: list[MetricItem] = field(default_factory=list)
    holdings: list[dict[str, str]] = field(default_factory=list)
    analytics: dict = field(default_factory=dict)


def _register_chinese_font() -> str:
    font_name = 'STSong-Light'
    try:
        registerFont(UnicodeCIDFont(font_name))
    except Exception:
        pass
    return font_name


def _build_styles(font_name: str):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='NSQCoverTitle', parent=styles['Title'], fontName=font_name, fontSize=26, leading=32, alignment=TA_CENTER, textColor=colors.HexColor('#0f172a'), spaceAfter=5 * mm))
    styles.add(ParagraphStyle(name='NSQCoverSubTitle', parent=styles['BodyText'], fontName=font_name, fontSize=12, leading=18, alignment=TA_CENTER, textColor=colors.HexColor('#475569'), spaceAfter=1.5 * mm))
    styles.add(ParagraphStyle(name='NSQSectionTitle', parent=styles['Heading2'], fontName=font_name, fontSize=15, leading=20, alignment=TA_LEFT, textColor=colors.HexColor('#1d4ed8'), spaceBefore=4 * mm, spaceAfter=2 * mm))
    styles.add(ParagraphStyle(name='NSQBody', parent=styles['BodyText'], fontName=font_name, fontSize=10.5, leading=16, alignment=TA_LEFT, textColor=colors.HexColor('#111827'), spaceAfter=1.6 * mm))
    styles.add(ParagraphStyle(name='NSQBullet', parent=styles['BodyText'], fontName=font_name, fontSize=10.5, leading=16, leftIndent=5 * mm, textColor=colors.HexColor('#111827'), spaceAfter=1.2 * mm))
    styles.add(ParagraphStyle(name='NSQSmall', parent=styles['BodyText'], fontName=font_name, fontSize=9, leading=13, alignment=TA_CENTER, textColor=colors.HexColor('#64748b')))
    styles.add(ParagraphStyle(name='NSQTOC', parent=styles['BodyText'], fontName=font_name, fontSize=11, leading=18, alignment=TA_LEFT, textColor=colors.HexColor('#0f172a')))
    return styles


def _clean_inline_markup(text: str) -> str:
    return text.replace('**', '').replace('`', '').strip()


def _looks_like_table_separator(line: str) -> bool:
    stripped = line.replace('|', '').replace(':', '').replace('-', '').strip()
    return stripped == '' and '-' in line


def _split_table_row(line: str) -> list[str]:
    return [_clean_inline_markup(cell) for cell in line.strip().strip('|').split('|')]


def _infer_report_type(title: str) -> str:
    if '回测报告' in title:
        return '回测报告'
    if '日报' in title:
        return '日报'
    if '周报' in title:
        return '周报'
    if '月报' in title:
        return '月报'
    if '年报' in title:
        return '年报'
    return '报告'


def _parse_metric_value(value: str) -> float | None:
    cleaned = str(value).replace(',', '').strip()
    is_percent = cleaned.endswith('%')
    if is_percent:
        cleaned = cleaned[:-1]
    try:
        num = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(num):
        return None
    return num / 100.0 if is_percent else num


def _format_metric_display(key: object, value: object) -> str:
    """以与 Markdown 报告一致的单位显示结构化指标。"""

    if value is None:
        return "N/A（样本不足或不适用）"
    if isinstance(value, bool):
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "N/A（非有限值）"
    key_text = str(key)
    if any(marker in key_text for marker in ("事件数", "观测数", "周期数", "订单数", "成交数量")):
        return f"{number:,.0f}" if number.is_integer() else f"{number:,.4f}"
    if key_text in {"累计手续费", "累计成交名义金额"}:
        return f"{number:,.2f}"
    if any(
        marker in key_text
        for marker in (
            "收益率",
            "波动率",
            "回撤",
            "占比",
            "换手",
            "跟踪误差",
            "超额收益",
            "保证金/权益",
            "可用资金/权益",
        )
    ):
        return f"{number:.2%}"
    if "比率" in key_text:
        return f"{number:.4f}"
    return f"{number:.6f}"


def _nonzero_holding_weights(
    holdings: list[dict[str, str]],
    *,
    limit: int = 8,
) -> list[tuple[str, float]]:
    """提取可绘制的非零目标权重，保留多空方向。"""

    weights: list[tuple[str, float]] = []
    for row in holdings[:limit]:
        value = _parse_metric_value(str(row.get("target_weight", "")))
        if value is None or abs(value) <= 1e-12:
            continue
        weights.append((str(row.get("symbol", "未知标的")), float(value)))
    return weights


def _format_absolute_weight(value: float) -> str:
    """以绝对值展示权重，方向由调用方单独标注。"""

    absolute = abs(value)
    return f"{absolute:.2%}" if absolute <= 1 else f"{absolute:.2f}"


def parse_markdown_report(markdown_path: str | Path) -> ReportStructure:
    md_path = Path(markdown_path)
    lines = md_path.read_text(encoding='utf-8').splitlines()
    data = load_report_data(md_path)

    title = next(
        (
            _clean_inline_markup(line.strip()[2:])
            for line in lines
            if line.strip().startswith("# ")
        ),
        "Northstar Quant 报告",
    )
    metrics = data.get("metrics") or {}
    holdings = data.get("holdings") or []
    analytics = data.get("analytics") or {}
    if not isinstance(metrics, dict):
        raise ValueError("report.json 的 metrics 必须是对象")
    if not isinstance(holdings, list):
        raise ValueError("report.json 的 holdings 必须是数组")
    if not isinstance(analytics, dict):
        raise ValueError("report.json 的 analytics 必须是对象")

    return ReportStructure(
        meta=ReportMeta(
            title=title,
            generated_at=str(data.get("generated_at") or ""),
            period_label=str(data.get("period_label") or ""),
            strategy_id=str(data.get("strategy_id") or ""),
            benchmark_symbol=str(data.get("benchmark_symbol") or ""),
            report_type=_infer_report_type(title),
        ),
        metrics=[
            MetricItem(str(key), _format_metric_display(key, value))
            for key, value in metrics.items()
        ],
        holdings=[
            {str(key): str(value) for key, value in row.items()}
            for row in holdings
            if isinstance(row, dict)
        ],
        analytics=analytics,
    )


def _draw_page_frame(canvas, doc, meta: ReportMeta, font_name: str) -> None:
    width, height = A4
    canvas.saveState()
    canvas.setFont(font_name, 9)
    canvas.setFillColor(colors.HexColor('#64748b'))
    canvas.drawString(18 * mm, height - 11 * mm, 'Northstar Quant')
    canvas.drawRightString(width - 18 * mm, height - 11 * mm, f"{meta.report_type} | {meta.strategy_id or '未指定策略'}")
    canvas.setStrokeColor(colors.HexColor('#cbd5e1'))
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, 12 * mm, width - 18 * mm, 12 * mm)
    canvas.drawString(18 * mm, 7.5 * mm, meta.period_label or '')
    canvas.drawRightString(width - 18 * mm, 7.5 * mm, f"第 {canvas.getPageNumber()} 页")
    canvas.restoreState()


class DrawingFlowable(Flowable):
    def __init__(self, drawing: Drawing, width: float, height: float):
        super().__init__()
        self.drawing = drawing
        self.width = width
        self.height = height

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        renderPDF.draw(self.drawing, self.canv, 0, 0)


def _metric_cards(metrics: list[MetricItem], font_name: str) -> Table:
    cards = []
    for item in metrics:
        card = Table(
            [[Paragraph(item.key, ParagraphStyle(name=f'k_{item.key}', fontName=font_name, fontSize=9, textColor=colors.HexColor('#64748b')))],
             [Paragraph(f"<b>{item.value}</b>", ParagraphStyle(name=f'v_{item.key}', fontName=font_name, fontSize=15, leading=18, textColor=colors.HexColor('#0f172a')))]],
            colWidths=[78 * mm],
        )
        card.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#dbeafe')),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ]))
        cards.append(card)
    if not cards:
        return Table([[Paragraph('暂无关键指标', ParagraphStyle(name='empty', fontName=font_name, fontSize=10))]])
    if len(cards) % 2 == 1:
        cards.append(Table([['']], colWidths=[78 * mm], rowHeights=[24 * mm]))
    rows = [[cards[i], cards[i + 1]] for i in range(0, len(cards), 2)]
    outer = Table(rows, colWidths=[82 * mm, 82 * mm], hAlign='LEFT')
    outer.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    return outer


def _build_metrics_bar_chart(metrics: list[MetricItem], font_name: str) -> Drawing:
    # 条形图只能放同为收益率/风险比例的无量纲指标。成交数量、手续费、观测数等
    # 混入同一坐标轴会制造看似精确但没有比较意义的图形。
    chartable_keys = {
        "总收益率",
        "年化收益率",
        "年化波动率",
        "最大回撤",
        "基准总收益率",
        "相对基准总超额收益",
        "total_return",
        "annualized_return",
        "max_drawdown",
    }
    parsed: list[tuple[str, float]] = []
    for metric in metrics:
        if metric.key not in chartable_keys:
            continue
        value = _parse_metric_value(metric.value)
        if value is not None:
            parsed.append((metric.key, value))
    parsed = parsed[:6]
    drawing = Drawing(170 * mm, 75 * mm)
    drawing.add(String(12 * mm, 66 * mm, '收益与风险比例指标', fontName=font_name, fontSize=11, fillColor=colors.HexColor('#0f172a')))
    if not parsed:
        drawing.add(String(12 * mm, 50 * mm, '暂无可绘制的数值型关键指标', fontName=font_name, fontSize=10, fillColor=colors.HexColor('#64748b')))
        return drawing
    labels = [k for k, _ in parsed]
    values = [v for _, v in parsed]
    max_abs = max(abs(v) for v in values) or 1
    y_max = max(math.ceil(max_abs * 1.25 * 100) / 100, 0.05)
    y_min = min(min(values), 0)
    chart = VerticalBarChart()
    chart.x = 12 * mm
    chart.y = 10 * mm
    chart.height = 50 * mm
    chart.width = 135 * mm
    chart.data = [values]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.fontName = font_name
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.labels.fontName = font_name
    chart.valueAxis.labels.fontSize = 8
    chart.valueAxis.valueMin = y_min
    chart.valueAxis.valueMax = y_max
    chart.valueAxis.valueStep = max((y_max - y_min) / 5, 0.01)
    chart.bars[0].fillColor = colors.HexColor('#2563eb')
    chart.strokeColor = colors.HexColor('#cbd5e1')
    drawing.add(chart)
    return drawing


def _build_holdings_pie_chart(holdings: list[dict[str, str]], font_name: str) -> Drawing:
    drawing = Drawing(170 * mm, 82 * mm)
    drawing.add(String(12 * mm, 74 * mm, '绝对目标权重分布（方向见图例）', fontName=font_name, fontSize=11, fillColor=colors.HexColor('#0f172a')))
    weights = _nonzero_holding_weights(holdings)
    if not weights:
        drawing.add(String(12 * mm, 55 * mm, '暂无非零目标权重，未绘制绝对暴露分布', fontName=font_name, fontSize=10, fillColor=colors.HexColor('#64748b')))
        return drawing
    pie = Pie()
    pie.x = 10 * mm
    pie.y = 6 * mm
    pie.width = 62 * mm
    pie.height = 62 * mm
    # 饼图不能表达负数。以绝对权重展示总暴露，并在标签与图例中保留多空方向，
    # 避免全空仓时数据和为零导致 ReportLab 除零，也避免把空头误标成零权重。
    pie.data = [abs(v) for _, v in weights]
    pie.labels = [f"{name} {'多头' if value > 0 else '空头'}" for name, value in weights]
    pie.slices.strokeWidth = 0.5
    palette = ['#2563eb', '#60a5fa', '#93c5fd', '#38bdf8', '#0ea5e9', '#0284c7', '#7dd3fc', '#bae6fd']
    for idx in range(min(len(weights), len(palette))):
        pie.slices[idx].fillColor = colors.HexColor(palette[idx])
    pie.sideLabels = True
    pie.simpleLabels = False
    drawing.add(pie)
    legend = Legend()
    legend.x = 92 * mm
    legend.y = 18 * mm
    legend.alignment = 'right'
    legend.fontName = font_name
    legend.fontSize = 8.5
    legend.colorNamePairs = [
        (
            pie.slices[idx].fillColor,
            f"{name} {'多头' if value > 0 else '空头'} {_format_absolute_weight(value)}",
        )
        for idx, (name, value) in enumerate(weights)
    ]
    drawing.add(legend)
    return drawing


def _build_equity_curve_chart(analytics: dict, font_name: str) -> Drawing:
    drawing = Drawing(170 * mm, 78 * mm)
    drawing.add(String(12 * mm, 70 * mm, '策略净值曲线（含可用基准）', fontName=font_name, fontSize=11, fillColor=colors.HexColor('#0f172a')))
    curve = analytics.get('equity_curve') or []
    if len(curve) < 2:
        drawing.add(String(12 * mm, 52 * mm, '暂无足够净值序列数据', fontName=font_name, fontSize=10, fillColor=colors.HexColor('#64748b')))
        return drawing
    visible_curve = curve[-60:]
    points = [(idx, float(item.get('equity', 0.0))) for idx, item in enumerate(visible_curve)]
    chart_series = [points]
    values = [value for _, value in points]
    benchmark = analytics.get('benchmark') or {}
    benchmark_curve = benchmark.get('equity_curve') if isinstance(benchmark, dict) else None
    if (
        isinstance(benchmark, dict)
        and benchmark.get('status') == 'available'
        and isinstance(benchmark_curve, list)
    ):
        benchmark_by_date = {
            str(item.get('date', ''))[:10]: _parse_metric_value(str(item.get('equity', '')))
            for item in benchmark_curve
            if isinstance(item, dict)
        }
        aligned = [
            benchmark_by_date.get(str(item.get('date', ''))[:10])
            for item in visible_curve
        ]
        if aligned and all(value is not None for value in aligned):
            benchmark_points = [
                (index, float(value))
                for index, value in enumerate(aligned)
                if value is not None
            ]
            chart_series.append(benchmark_points)
            values.extend(value for _, value in benchmark_points)
            drawing.add(String(105 * mm, 65 * mm, '蓝：策略', fontName=font_name, fontSize=8, fillColor=colors.HexColor('#2563eb')))
            drawing.add(String(130 * mm, 65 * mm, '橙：基准', fontName=font_name, fontSize=8, fillColor=colors.HexColor('#ea580c')))
    min_y = min(values)
    max_y = max(values)
    if abs(max_y - min_y) < 1e-9:
        max_y = min_y + 0.01
    chart = LinePlot()
    chart.x = 12 * mm
    chart.y = 10 * mm
    chart.height = 52 * mm
    chart.width = 145 * mm
    chart.data = chart_series
    chart.lines[0].strokeColor = colors.HexColor('#2563eb')
    chart.lines[0].strokeWidth = 2
    if len(chart_series) > 1:
        chart.lines[1].strokeColor = colors.HexColor('#ea580c')
        chart.lines[1].strokeWidth = 1.5
    chart.xValueAxis.labels.fontName = font_name
    chart.xValueAxis.labels.fontSize = 8
    chart.yValueAxis.labels.fontName = font_name
    chart.yValueAxis.labels.fontSize = 8
    chart.yValueAxis.valueMin = min_y * 0.98
    chart.yValueAxis.valueMax = max_y * 1.02
    chart.yValueAxis.valueStep = max((chart.yValueAxis.valueMax - chart.yValueAxis.valueMin) / 5, 0.01)
    drawing.add(chart)
    return drawing


def _build_drawdown_chart(analytics: dict, font_name: str) -> Drawing:
    drawing = Drawing(170 * mm, 78 * mm)
    drawing.add(String(12 * mm, 70 * mm, '回撤曲线', fontName=font_name, fontSize=11, fillColor=colors.HexColor('#0f172a')))
    curve = analytics.get('drawdown_curve') or []
    if len(curve) < 2:
        drawing.add(String(12 * mm, 52 * mm, '暂无足够回撤序列数据', fontName=font_name, fontSize=10, fillColor=colors.HexColor('#64748b')))
        return drawing
    points = [(idx, float(item.get('drawdown', 0.0))) for idx, item in enumerate(curve[-60:])]
    min_y = min(y for _, y in points)
    max_y = max(y for _, y in points)
    if abs(max_y - min_y) < 1e-9:
        min_y = min_y - 0.01
    chart = LinePlot()
    chart.x = 12 * mm
    chart.y = 10 * mm
    chart.height = 52 * mm
    chart.width = 145 * mm
    chart.data = [points]
    chart.lines[0].strokeColor = colors.HexColor('#dc2626')
    chart.lines[0].strokeWidth = 2
    chart.xValueAxis.labels.fontName = font_name
    chart.xValueAxis.labels.fontSize = 8
    chart.yValueAxis.labels.fontName = font_name
    chart.yValueAxis.labels.fontSize = 8
    chart.yValueAxis.valueMin = min_y * 1.02 if min_y < 0 else min_y - 0.02
    chart.yValueAxis.valueMax = max(0.0, max_y)
    chart.yValueAxis.valueStep = max((chart.yValueAxis.valueMax - chart.yValueAxis.valueMin) / 5, 0.01)
    drawing.add(chart)
    return drawing


def _build_monthly_heatmap_table(analytics: dict, font_name: str) -> Table:
    rows = analytics.get('monthly_returns') or []
    if not rows:
        return Table([[Paragraph('暂无月度收益数据', ParagraphStyle(name='empty_heat', fontName=font_name, fontSize=10))]])
    by_year: dict[str, dict[int, float]] = {}
    for item in rows:
        month = str(item.get('month', ''))
        if len(month) < 7:
            continue
        year = month[:4]
        mon = int(month[5:7])
        by_year.setdefault(year, {})[mon] = float(item.get('return', 0.0))
    header = ['年份'] + [f'{i}月' for i in range(1, 13)]
    table_rows = [header]
    for year in sorted(by_year):
        row = [year]
        for mon in range(1, 13):
            v = by_year[year].get(mon)
            row.append('' if v is None else f'{v:.2%}')
        table_rows.append(row)
    table = Table(table_rows, colWidths=[18 * mm] + [12 * mm] * 12, hAlign='LEFT')
    style = [
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
    ]
    for r in range(1, len(table_rows)):
        for c in range(1, 13):
            cell = table_rows[r][c]
            if not cell:
                continue
            val = float(cell.rstrip('%')) / 100.0
            bg = colors.HexColor('#dcfce7') if val >= 0 else colors.HexColor('#fee2e2')
            style.append(('BACKGROUND', (c, r), (c, r), bg))
    table.setStyle(TableStyle(style))
    return table


def _build_holdings_bar_chart(holdings: list[dict[str, str]], font_name: str) -> Drawing:
    drawing = Drawing(170 * mm, 78 * mm)
    drawing.add(String(12 * mm, 70 * mm, '目标权重条形图（正=多头，负=空头）', fontName=font_name, fontSize=11, fillColor=colors.HexColor('#0f172a')))
    weights = _nonzero_holding_weights(holdings)
    if not weights:
        drawing.add(String(12 * mm, 52 * mm, '暂无非零目标权重数据', fontName=font_name, fontSize=10, fillColor=colors.HexColor('#64748b')))
        return drawing
    chart = VerticalBarChart()
    chart.x = 12 * mm
    chart.y = 10 * mm
    chart.height = 50 * mm
    chart.width = 140 * mm
    chart.data = [[v for _, v in weights]]
    chart.categoryAxis.categoryNames = [k for k, _ in weights]
    chart.categoryAxis.labels.fontName = font_name
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.labels.fontName = font_name
    chart.valueAxis.labels.fontSize = 8
    values = [value for _, value in weights]
    max_abs = max(abs(value) for value in values)
    padding = max(max_abs * 0.1, 0.01)
    chart.valueAxis.valueMin = min(0.0, min(values)) - (
        padding if min(values) < 0 else 0.0
    )
    chart.valueAxis.valueMax = max(0.0, max(values)) + (
        padding if max(values) > 0 else 0.0
    )
    chart.valueAxis.valueStep = max(
        (chart.valueAxis.valueMax - chart.valueAxis.valueMin) / 5,
        0.01,
    )
    chart.bars[0].fillColor = colors.HexColor('#0ea5e9')
    drawing.add(chart)
    return drawing


def _build_table(rows: list[list[str]], font_name: str) -> Table:
    col_widths = [55 * mm, 40 * mm, 40 * mm]
    if rows and len(rows[0]) == 2:
        col_widths = [70 * mm, 65 * mm]
    table = Table(rows, colWidths=col_widths, repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 9.6),
        ('LEADING', (0, 0), (-1, -1), 13),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8eefc')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#0f172a')),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    return table


def _append_markdown_lines(story: list, lines: Iterable[str], styles, font_name: str) -> None:
    lines = list(lines)
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 1.4 * mm))
            i += 1
            continue
        if stripped.startswith('## '):
            story.append(Paragraph(_clean_inline_markup(stripped[3:]), styles['NSQSectionTitle']))
            i += 1
            continue
        if stripped.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                current = lines[i].strip()
                if not _looks_like_table_separator(current):
                    rows.append(_split_table_row(current))
                i += 1
            if rows:
                story.append(_build_table(rows, font_name))
                story.append(Spacer(1, 2.3 * mm))
            continue
        if stripped.startswith('- '):
            story.append(Paragraph(_clean_inline_markup(stripped[2:].strip()), styles['NSQBullet'], bulletText='•'))
            i += 1
            continue
        story.append(Paragraph(_clean_inline_markup(stripped), styles['NSQBody']))
        i += 1


def markdown_to_pdf(markdown_path: str | Path, output_path: str | Path | None = None) -> str:
    md_path = Path(markdown_path)
    pdf_path = Path(output_path) if output_path else md_path.with_suffix('.pdf')
    report = parse_markdown_report(md_path)
    font_name = _register_chinese_font()
    styles = _build_styles(font_name)
    raw_lines = md_path.read_text(encoding='utf-8').splitlines()

    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=16 * mm, title=report.meta.title, author='Northstar Quant')
    story: list = []

    logo = Drawing(170 * mm, 24 * mm)
    logo.add(Rect(0, 4 * mm, 18 * mm, 18 * mm, rx=2 * mm, ry=2 * mm, fillColor=colors.HexColor('#0f172a'), strokeColor=colors.HexColor('#0f172a')))
    logo.add(String(4.8 * mm, 10.5 * mm, 'N', fontName=font_name, fontSize=18, fillColor=colors.white))
    logo.add(String(24 * mm, 11 * mm, 'Northstar Quant', fontName=font_name, fontSize=16, fillColor=colors.HexColor('#0f172a')))

    story.append(Spacer(1, 12 * mm))
    story.append(DrawingFlowable(logo, 170 * mm, 24 * mm))
    story.append(Spacer(1, 18 * mm))
    story.append(Paragraph(report.meta.title or 'Northstar Quant 报告', styles['NSQCoverTitle']))
    story.append(Paragraph(f"{report.meta.report_type} · {report.meta.period_label or '未标注周期'}", styles['NSQCoverSubTitle']))
    story.append(Spacer(1, 10 * mm))
    cover_table = Table([
        ['策略名称', report.meta.strategy_id or '未指定'],
        ['基准标的', report.meta.benchmark_symbol or '未指定'],
        ['生成时间', report.meta.generated_at or '未记录'],
        ['文档级别', '个人研究 / 正式归档版'],
    ], colWidths=[38 * mm, 100 * mm], hAlign='CENTER')
    cover_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#eff6ff')),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#1d4ed8')),
        ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#bfdbfe')),
        ('INNERGRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#dbeafe')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(cover_table)
    story.append(Spacer(1, 14 * mm))
    story.append(Paragraph('本报告由 Northstar Quant 自动生成，用于个人量化系统的审计、复盘、归档与沟通。', styles['NSQCoverSubTitle']))
    story.append(Spacer(1, 75 * mm))
    story.append(Paragraph('Confidential · For Personal Quant Research Use', styles['NSQSmall']))
    story.append(PageBreak())

    story.append(Paragraph('目录', styles['NSQSectionTitle']))
    story.append(Spacer(1, 4 * mm))
    for idx, title in enumerate(['1. 风险提示与使用边界', '2. 关键指标总览', '3. 核心图表页', '4. 月度收益热力表', '5. 正文与审计内容'], start=2):
        dots = '·' * max(10, 42 - len(title))
        story.append(Paragraph(f'{title} {dots} {idx}', styles['NSQTOC']))
    story.append(PageBreak())

    story.append(Paragraph('风险提示与使用边界', styles['NSQSectionTitle']))
    for text in [
        '本报告基于系统回测、策略目标仓位和账户同步结果自动生成，仅用于研究、复盘和执行审计。',
        '除非显式说明，报告中的绩效指标不代表未来收益承诺，也不构成投资建议。',
        '图表与指标读取同目录 report.json；结构化数据缺失时系统会拒绝生成不完整 PDF。',
        '如需实盘使用，应结合交易成本、流动性、时区、券商返回状态与对账记录综合判断。',
    ]:
        story.append(Paragraph(text, styles['NSQBullet'], bulletText='•'))
    story.append(PageBreak())

    story.append(Paragraph('关键指标总览', styles['NSQSectionTitle']))
    story.append(Spacer(1, 2 * mm))
    story.append(_metric_cards(report.metrics, font_name))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph('说明：指标卡片用于快速概览策略在当前周期内的主要表现，最终解释需结合正文和持仓审计部分。', styles['NSQBody']))
    story.append(PageBreak())

    story.append(Paragraph('核心图表页', styles['NSQSectionTitle']))
    story.append(DrawingFlowable(_build_equity_curve_chart(report.analytics, font_name), 170 * mm, 78 * mm))
    story.append(Spacer(1, 3 * mm))
    story.append(DrawingFlowable(_build_drawdown_chart(report.analytics, font_name), 170 * mm, 78 * mm))
    story.append(PageBreak())

    story.append(Paragraph('结构与配置图表', styles['NSQSectionTitle']))
    story.append(DrawingFlowable(_build_metrics_bar_chart(report.metrics, font_name), 170 * mm, 75 * mm))
    story.append(Spacer(1, 4 * mm))
    story.append(DrawingFlowable(_build_holdings_bar_chart(report.holdings, font_name), 170 * mm, 78 * mm))
    story.append(Spacer(1, 4 * mm))
    story.append(DrawingFlowable(_build_holdings_pie_chart(report.holdings, font_name), 170 * mm, 82 * mm))
    story.append(PageBreak())

    story.append(Paragraph('月度收益热力表', styles['NSQSectionTitle']))
    story.append(Spacer(1, 2 * mm))
    story.append(_build_monthly_heatmap_table(report.analytics, font_name))
    story.append(PageBreak())

    story.append(Paragraph('正文与审计内容', styles['NSQSectionTitle']))
    story.append(Spacer(1, 2 * mm))
    _append_markdown_lines(story, raw_lines[1:], styles, font_name)

    doc.build(story, onFirstPage=lambda canvas, doc: _draw_page_frame(canvas, doc, report.meta, font_name), onLaterPages=lambda canvas, doc: _draw_page_frame(canvas, doc, report.meta, font_name))
    return str(pdf_path)
