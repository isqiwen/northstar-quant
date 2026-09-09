"use client";
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { CandlestickChart, BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
echarts.use([
  CandlestickChart,
  BarChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  CanvasRenderer,
]);
type Row = Record<string, unknown>;
export function PriceChart({
  rows,
  onSelect,
}: {
  rows: Row[];
  onSelect: (key: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const chart = echarts.init(ref.current!);
    const labels = rows.map((r) =>
      String(r.trade_time || r.trade_date || r.end_date),
    );
    const num = (r: Row, k: string) =>
      r[k] == null || r[k] === "" ? null : Number(r[k]);
    chart.setOption({
      animation: false,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        renderMode: "richText",
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 65, right: 28, top: 20, height: "48%" },
        { left: 65, right: 28, top: "63%", height: "20%" },
      ],
      xAxis: [0, 1].map((i) => ({
        type: "category",
        data: labels,
        gridIndex: i,
        axisLabel: { show: i === 1 },
      })),
      yAxis: [
        { scale: true, gridIndex: 0 },
        { scale: true, gridIndex: 1, name: "成交量" },
        {
          scale: true,
          gridIndex: 1,
          name: "持仓量",
          position: "right",
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1] },
        { type: "slider", xAxisIndex: [0, 1], bottom: 4, height: 20 },
      ],
      series: [
        {
          name: "OHLC",
          type: "candlestick",
          data: rows.map((r) => [
            num(r, "open"),
            num(r, "close"),
            num(r, "low"),
            num(r, "high"),
          ]),
          itemStyle: {
            color: "#cf4c56",
            color0: "#16866c",
            borderColor: "#cf4c56",
            borderColor0: "#16866c",
          },
        },
        {
          name: "成交量",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: rows.map((r) => num(r, "vol")),
          itemStyle: { color: "#8fa8de" },
        },
        {
          name: "持仓量",
          type: "line",
          xAxisIndex: 1,
          yAxisIndex: 2,
          data: rows.map((r) => num(r, "oi")),
          showSymbol: false,
          lineStyle: { color: "#c08d36" },
        },
      ],
    });
    chart.on("click", (p) => {
      if (p.dataIndex !== undefined) onSelect(String(rows[p.dataIndex]?._key));
    });
    const resize = new ResizeObserver(() => chart.resize());
    resize.observe(ref.current!);
    return () => {
      resize.disconnect();
      chart.dispose();
    };
  }, [rows, onSelect]);
  return (
    <div
      ref={ref}
      style={{ height: 390 }}
      role="img"
      aria-label="固定数据 K 线、成交量与持仓量"
    />
  );
}
