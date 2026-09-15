"use client";

import { Alert, Card, Table, Tag } from "antd";
import type { FactorAnalysis } from "../api/generated";

const number = (value: number | null) =>
  value === null ? "不可评价" : value.toFixed(4);
const percent = (value: number | null) =>
  value === null ? "不可评价" : `${(value * 100).toFixed(4)}%`;
const reasons: Record<string, string> = {
  FACTOR_UNAVAILABLE: "因子未就绪",
  LABEL_OUTSIDE_FIXED_WINDOW: "标签超出固定窗口",
  FACTOR_NOT_AVAILABLE_BEFORE_LABEL_INTERVAL: "因子可得时间晚于标签区间开始",
  LABEL_CROSSES_SESSION_GAP_OR_TRADING_DAY: "标签跨休市缺口或交易日",
};

export default function FactorDiagnostics({
  value,
}: {
  value: FactorAnalysis;
}) {
  return (
    <Card title="前瞻收益与稳定性诊断">
      <Alert
        type="info"
        showIcon
        title="单合约时间序列粗筛"
        description="按 1、5、15 根后续连续 Bar 检查因子与价格收益的关系。相关性和全窗口分组用于事后描述，不代表可实现收益、样本外能力或交易许可。"
      />
      <Table
        rowKey="bars"
        pagination={false}
        dataSource={value.horizons}
        columns={[
          { title: "前瞻 Bar 数", dataIndex: "bars" },
          { title: "有效样本", dataIndex: "samples" },
          { title: "Pearson", dataIndex: "pearson", render: number },
          { title: "Spearman", dataIndex: "spearman", render: number },
          {
            title: "分组切换比例",
            dataIndex: "group_change_fraction",
            render: percent,
          },
          {
            title: "状态",
            dataIndex: "status",
            render: (status) => (
              <Tag>
                {status === "DESCRIPTIVE_ONLY"
                  ? "仅描述性分析"
                  : status === "INSUFFICIENT_SAMPLE"
                    ? "样本不足"
                    : "变化不足"}
              </Tag>
            ),
          },
        ]}
        expandable={{
          expandedRowRender: (row) => (
            <>
              <p>
                {Object.entries(row.excluded)
                  .map(([key, count]) => `${reasons[key] || key}：${count}`)
                  .join("；") || "全部样本符合标签条件"}
              </p>
              <Table
                rowKey="group"
                size="small"
                pagination={false}
                dataSource={row.groups}
                columns={[
                  {
                    title: "因子分组（平均秩，同值不拆组）",
                    dataIndex: "group",
                  },
                  { title: "样本", dataIndex: "samples" },
                  {
                    title: "平均前瞻价格收益",
                    dataIndex: "mean_forward_return",
                    render: percent,
                  },
                ]}
              />
              <Table
                rowKey="trading_day"
                size="small"
                pagination={{ pageSize: 8 }}
                dataSource={row.days}
                columns={[
                  { title: "交易日", dataIndex: "trading_day" },
                  { title: "样本", dataIndex: "samples" },
                  {
                    title: "日内 Spearman",
                    dataIndex: "spearman",
                    render: number,
                  },
                  {
                    title: "平均前瞻价格收益",
                    dataIndex: "mean_forward_return",
                    render: percent,
                  },
                ]}
              />
            </>
          ),
        }}
      />
      {value.limitations.map((text) => (
        <p key={text} className="muted">
          {text}
        </p>
      ))}
    </Card>
  );
}
