"use client";
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  CanvasRenderer,
]);
export function Line({
  labels,
  values,
  name,
}: {
  labels: string[];
  values: (number | null)[];
  name: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const chart = echarts.init(ref.current!);
    chart.setOption({
      tooltip: { trigger: "axis" },
      grid: { left: 65, right: 25, top: 20, bottom: 60 },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { color: "#93a0b5" },
      },
      yAxis: {
        type: "value",
        scale: true,
        splitLine: { lineStyle: { color: "#edf0f6" } },
      },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
      series: [
        {
          name,
          type: "line",
          data: values,
          showSymbol: false,
          lineStyle: { color: "#315bea", width: 2 },
          areaStyle: { color: "#315bea", opacity: 0.05 },
        },
      ],
    });
    const resize = new ResizeObserver(() => chart.resize());
    resize.observe(ref.current!);
    return () => {
      resize.disconnect();
      chart.dispose();
    };
  }, [labels, values, name]);
  return <div className="chart" ref={ref} role="img" aria-label={name} />;
}
