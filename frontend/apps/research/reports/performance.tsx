"use client";
import { Alert, Card } from "antd";
import type { PerformanceReport } from "../api/generated";
import { Records } from "../../../shared/ui";
import { Line } from "../../../shared/chart";

export function Performance({ value }: { value: PerformanceReport }) {
  const columns = [
    { title: "区间", dataIndex: "period" },
    { title: "期初权益", dataIndex: "opening_equity" },
    { title: "期末权益", dataIndex: "closing_equity" },
    { title: "盈亏", dataIndex: "pnl" },
    { title: "收益率（小数）", dataIndex: "return_fraction" },
    { title: "观察数", dataIndex: "observations" },
  ];
  return (
    <>
      <Alert
        type="info"
        showIcon
        title="按交易日归属统计已观察区间；夜盘归入声明的交易日。不是完整日终或完整月份收益，缺失日期不补零，不推算年化。"
      />
      <Card title="按月汇总">
        <Records rowKey="period" rows={value.monthly} columns={columns} />
      </Card>
      <Card title="按交易日汇总">
        <Records rowKey="period" rows={value.daily} columns={columns} />
      </Card>
      <Card title="回撤区间">
        <Records
          rowKey="start_at"
          rows={value.drawdowns}
          columns={[
            { title: "前高时间", dataIndex: "peak_at" },
            { title: "首次回撤", dataIndex: "start_at" },
            { title: "低点时间", dataIndex: "trough_at" },
            { title: "截至 / 恢复时间", dataIndex: "end_at" },
            { title: "最大回撤", dataIndex: "drawdown" },
            { title: "回撤比例", dataIndex: "drawdown_fraction" },
            { title: "经过秒数（含休市）", dataIndex: "elapsed_seconds" },
            {
              title: "恢复状态",
              dataIndex: "recovered",
              render: (v: boolean) => (v ? "已恢复" : "截至末次观察未恢复"),
            },
          ]}
        />
      </Card>
      <Card title="滚动 20 个已观察交易日">
        <Alert
          type="info"
          showIcon
          title="仅在拥有 20 个已观察交易日后显示；可能跨过数据缺口，不代表连续 20 个交易日。期初权益非正时收益率留空。"
        />
        {value.rolling.length > 0 && (
          <Line
            name="滚动区间收益率"
            labels={value.rolling.map((r) => r.end_day)}
            values={value.rolling.map((r) =>
              r.return_fraction === null ? null : Number(r.return_fraction),
            )}
          />
        )}
        <Records
          rowKey="end_day"
          rows={value.rolling}
          columns={[
            { title: "起始交易日", dataIndex: "start_day" },
            { title: "结束交易日", dataIndex: "end_day" },
            { title: "收益率（小数）", dataIndex: "return_fraction" },
          ]}
        />
      </Card>
    </>
  );
}
