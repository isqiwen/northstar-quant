"use client";
import { useEffect, useRef, useState } from "react";
import { Button, Checkbox, Space, ConfigProvider, theme } from "antd";
import * as echarts from "echarts/core";
import { CandlestickChart, BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { movingAverage, macd } from "./chart-indicators";
echarts.use([
  CandlestickChart,
  BarChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  LegendComponent,
  CanvasRenderer,
]);
type Row = Record<string, unknown>;
const rising = "#f15a67",
  falling = "#24bea3";
export function PriceChart({
  rows,
  onSelect,
  settlement = false,
}: {
  rows: Row[];
  onSelect: (key: string) => void;
  settlement?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const instance = useRef<echarts.ECharts | undefined>(undefined);
  const zoom = useRef<{ start: number; end: number } | undefined>(undefined);
  const previousRows = useRef<Row[] | undefined>(undefined);
  const [showMA, setShowMA] = useState(true);
  const [showMACD, setShowMACD] = useState(false);
  const [hovered, setHovered] = useState(rows.length - 1);
  useEffect(() => {
    if (previousRows.current !== rows) zoom.current = undefined;
    previousRows.current = rows;
    const chart = echarts.init(ref.current!);
    instance.current = chart;
    setHovered(rows.length - 1);
    const labels = rows.map((r) =>
      String(r.trade_time || r.trade_date || r.end_date),
    );
    const num = (r: Row, k: string) =>
      r[k] == null || r[k] === "" || !Number.isFinite(Number(r[k]))
        ? null
        : Number(r[k]);
    const closes = rows.map((r) => num(r, "close"));
    const oscillator = macd(closes);
    const secondary = !settlement && showMACD;
    const axisIndices = settlement ? [0] : secondary ? [0, 1, 2] : [0, 1];
    const line = { color: "#27323f" };
    const compactNumber = (v: number) =>
      Math.abs(v) >= 1e8
        ? `${(v / 1e8).toFixed(1)}亿`
        : Math.abs(v) >= 1e4
          ? `${(v / 1e4).toFixed(1)}万`
          : String(v);
    chart.setOption({
      backgroundColor: "#10151d",
      animation: false,
      textStyle: {
        color: "#aab8c8",
        fontFamily: "system-ui, sans-serif",
        fontSize: 11,
      },
      legend: {
        top: 5,
        left: 16,
        textStyle: { color: "#b8c6d6", fontSize: 11 },
        itemWidth: 14,
        itemHeight: 7,
        data: settlement
          ? ["结算价"]
          : [
              ...(showMA ? [5, 10, 20, 60].map((p) => `MA${p}`) : []),
              "成交量",
              "持仓量",
              ...(secondary ? ["DIF", "DEA"] : []),
            ],
      },
      tooltip: {
        trigger: "axis",
        axisPointer: {
          type: "cross",
          lineStyle: { color: "#91a3b9", type: "dashed" },
          crossStyle: { color: "#91a3b9" },
          label: { backgroundColor: "#334155" },
        },
        renderMode: "richText",
        backgroundColor: "#1c2634",
        borderColor: "#3c4c60",
        textStyle: { color: "#e2e8f0", fontSize: 11 },
        confine: true,
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: settlement
        ? [{ left: 18, right: 76, top: 38, bottom: 65 }]
        : [
            { left: 18, right: 76, top: 38, height: secondary ? "44%" : "56%" },
            {
              left: 18,
              right: 76,
              top: secondary ? "55%" : "70%",
              height: secondary ? "15%" : "17%",
            },
            ...(secondary
              ? [{ left: 18, right: 76, top: "77%", height: "12%" }]
              : []),
          ],
      xAxis: axisIndices.map((i) => ({
        type: "category",
        data: labels,
        gridIndex: i,
        boundaryGap: true,
        axisLine: { lineStyle: line },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: {
          show: i === axisIndices.length - 1,
          color: "#7f91a7",
          hideOverlap: true,
        },
        axisPointer: { snap: true },
        min: "dataMin",
        max: "dataMax",
      })),
      yAxis: settlement
        ? [
            {
              scale: true,
              position: "right",
              name: "结算价",
              splitLine: { lineStyle: line },
            },
          ]
        : [
            {
              scale: true,
              gridIndex: 0,
              position: "right",
              splitLine: { lineStyle: { ...line, type: "dashed" } },
            },
            {
              scale: true,
              gridIndex: 1,
              position: "right",
              name: "成交量",
              splitLine: { show: false },
              axisLabel: { formatter: compactNumber },
            },
            {
              scale: true,
              gridIndex: 1,
              position: "left",
              name: "持仓量",
              splitLine: { show: false },
              axisLabel: { show: false },
            },
            ...(secondary
              ? [
                  {
                    scale: true,
                    gridIndex: 2,
                    position: "right",
                    name: "MACD",
                    splitLine: { show: false },
                  },
                ]
              : []),
          ],
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: axisIndices,
          ...(zoom.current || {
            startValue: Math.max(0, rows.length - 120),
            endValue: rows.length - 1,
          }),
          filterMode: "filter",
        },
        {
          type: "slider",
          xAxisIndex: axisIndices,
          bottom: 4,
          height: 20,
          borderColor: "#2b394b",
          backgroundColor: "#141d29",
          fillerColor: "rgba(68,113,166,.2)",
          textStyle: { color: "#9bacc0" },
          filterMode: "filter",
        },
      ],
      series: settlement
        ? [
            {
              name: "结算价",
              type: "line",
              connectNulls: false,
              showSymbol: false,
              lineStyle: { color: "#e4b962", width: 1.5 },
              data: rows.map((r) => num(r, "settle")),
            },
          ]
        : [
            {
              name: "K线",
              type: "candlestick",
              data: rows.map((r) => [
                num(r, "open"),
                num(r, "close"),
                num(r, "low"),
                num(r, "high"),
              ]),
              itemStyle: {
                color: rising,
                color0: falling,
                borderColor: rising,
                borderColor0: falling,
              },
              barMaxWidth: 16,
            },
            ...(showMA
              ? [5, 10, 20, 60].map((period, i) => ({
                  name: `MA${period}`,
                  type: "line",
                  showSymbol: false,
                  connectNulls: false,
                  data: movingAverage(closes, period),
                  lineStyle: {
                    width: 1,
                    color: ["#e4dfce", "#e7b653", "#b476df", "#5aabe9"][i],
                  },
                  emphasis: { disabled: true },
                }))
              : []),
            {
              name: "成交量",
              type: "bar",
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: rows.map((r) => ({
                value: num(r, "vol"),
                itemStyle: {
                  color:
                    (num(r, "close") ?? 0) >= (num(r, "open") ?? 0)
                      ? rising
                      : falling,
                },
              })),
              barMaxWidth: 16,
            },
            {
              name: "持仓量",
              type: "line",
              xAxisIndex: 1,
              yAxisIndex: 2,
              data: rows.map((r) => num(r, "oi")),
              showSymbol: false,
              connectNulls: false,
              lineStyle: { color: "#e4b962", width: 1 },
            },
            ...(secondary
              ? [
                  {
                    name: "MACD",
                    type: "bar",
                    xAxisIndex: 2,
                    yAxisIndex: 3,
                    data: oscillator.histogram.map((value) => ({
                      value,
                      itemStyle: {
                        color: (value ?? 0) >= 0 ? rising : falling,
                      },
                    })),
                  },
                  {
                    name: "DIF",
                    type: "line",
                    xAxisIndex: 2,
                    yAxisIndex: 3,
                    showSymbol: false,
                    data: oscillator.dif,
                    lineStyle: { color: "#e4dfce", width: 1 },
                  },
                  {
                    name: "DEA",
                    type: "line",
                    xAxisIndex: 2,
                    yAxisIndex: 3,
                    showSymbol: false,
                    data: oscillator.dea,
                    lineStyle: { color: "#e7b653", width: 1 },
                  },
                ]
              : []),
          ],
    });
    chart.on("datazoom", () => {
      const option = chart.getOption() as {
        dataZoom?: { start: number; end: number }[];
      };
      const current = option.dataZoom?.[0];
      if (current) zoom.current = { start: current.start, end: current.end };
    });
    chart.on("updateAxisPointer", (event: unknown) => {
      const value = (event as { axesInfo?: { value: number }[] }).axesInfo?.[0]
        ?.value;
      if (value != null && rows[value]) setHovered(value);
    });
    chart.on("click", (p) => {
      if (p.dataIndex !== undefined) onSelect(String(rows[p.dataIndex]?._key));
    });
    const resize = new ResizeObserver(() => chart.resize());
    resize.observe(ref.current!);
    return () => {
      resize.disconnect();
      chart.dispose();
      instance.current = undefined;
    };
  }, [rows, onSelect, settlement, showMA, showMACD]);
  const point = rows[hovered] || rows[rows.length - 1];
  return (
    <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}>
      <div ref={frame} className="price-chart-frame">
        <div className="chart-toolbar">
          <Space wrap size="small">
            {!settlement && (
              <>
                <Checkbox
                  checked={showMA}
                  onChange={(e) => setShowMA(e.target.checked)}
                >
                  均线 MA
                </Checkbox>
                <Checkbox
                  checked={showMACD}
                  onChange={(e) => setShowMACD(e.target.checked)}
                >
                  MACD
                </Checkbox>
              </>
            )}
            <Button
              size="small"
              onClick={() =>
                instance.current?.dispatchAction({
                  type: "dataZoom",
                  start: 0,
                  end: 100,
                })
              }
            >
              全范围
            </Button>
            <Button
              size="small"
              onClick={() =>
                instance.current?.dispatchAction({
                  type: "dataZoom",
                  start: Math.max(0, ((rows.length - 120) / rows.length) * 100),
                  end: 100,
                })
              }
            >
              最近120根
            </Button>
            <Button
              size="small"
              onClick={() => {
                if (document.fullscreenElement) void document.exitFullscreen();
                else void frame.current?.requestFullscreen();
              }}
            >
              全屏看图
            </Button>
          </Space>
          <span>{rows.length.toLocaleString()} 根 · 固定历史</span>
        </div>
        <div className="chart-quote" aria-live="polite">
          <strong>
            {String(
              point?.trade_time || point?.trade_date || point?.end_date || "",
            )}
          </strong>
          {(settlement
            ? [["结算", "settle"]]
            : [
                ["开", "open"],
                ["高", "high"],
                ["低", "low"],
                ["收", "close"],
                ["量", "vol"],
                ["持仓", "oi"],
              ]
          ).map(([label, key]) => (
            <span key={key}>
              {label} <b>{point?.[key] == null ? "—" : String(point[key])}</b>
            </span>
          ))}
        </div>
        <div
          ref={ref}
          className="price-chart-canvas"
          role="img"
          aria-label={
            settlement ? "固定数据结算价曲线" : "固定数据 K 线、成交量与持仓量"
          }
        />
        <div className="chart-caption">
          滚轮缩放 · 拖动平移 · 十字光标联动 · 点击查看精确记录
          {!settlement &&
            " · 均线/MACD仅作图示，从固定范围起点计算，缺值重新预热"}
        </div>
      </div>
    </ConfigProvider>
  );
}
