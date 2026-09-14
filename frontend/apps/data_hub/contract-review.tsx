"use client";
import { useEffect, useState } from "react";
import { Alert, Descriptions, Drawer, Spin, Table, Tag } from "antd";
import { mutate } from "./api/client";
import type { ContractReview as Review } from "./api/generated";
import { Failure } from "../../shared/ui";
import { exchangeName } from "./exploration/instrument-labels";

const labels: Record<string, string> = {
  NOT_ELIGIBLE: "不在下载范围",
  VERIFIED: "已核验",
  RECEIVED: "响应已收到",
  COLLECTING: "待补齐",
  UNKNOWN: "依据不足",
  INVALID: "存在异常",
  VERIFICATION_PENDING: "整体验收待核验",
};
export function ContractReview({
  scope,
  onClose,
}: {
  scope?: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<Review>();
  const [error, setError] = useState<Error>();
  useEffect(() => {
    setData(undefined);
    setError(undefined);
    if (!scope) return;
    let active = true;
    void mutate("/api/sync/contracts/review", { scope })
      .then((result) => {
        if (active) setData(result);
      })
      .catch((failure: Error) => {
        if (active) setError(failure);
      });
    return () => {
      active = false;
    };
  }, [scope]);
  return (
    <Drawer
      title="合约全生命周期验收"
      open={!!scope}
      onClose={onClose}
      size="large"
    >
      <Failure error={error} />
      {!!scope && !data && !error && <Spin />}
      {data && (
        <>
          <Descriptions
            column={1}
            items={[
              {
                key: "name",
                label: "交易所 → 品种 → 合约",
                children: `${exchangeName(data.exchange)} → ${data.product} → ${data.display_name} (${data.scope})`,
              },
              {
                key: "life",
                label: "上市 / 最后交易日",
                children: `${data.listing_date ?? "未知"} / ${data.last_trade_date ?? "未知"}`,
              },
              {
                key: "delivery",
                label: "交割月份 / 最后交割日",
                children: `${data.delivery_month ?? "未知"} / ${data.last_delivery_date ?? "未知"}`,
              },
              {
                key: "lifecycle",
                label: "合约生命周期",
                children: data.lifecycle_reason,
              },
              {
                key: "firstDelivery",
                label: "首次交割日",
                children: data.first_delivery_date ?? "供应商未提供，不推算",
              },
              {
                key: "end",
                label: "行情检查至",
                children: data.required_end ?? "等待完整交易日历",
              },
              {
                key: "status",
                label: "整体验收",
                children: (
                  <Tag color={data.status === "INVALID" ? "red" : "orange"}>
                    {labels[data.status] || data.status}
                  </Tag>
                ),
              },
            ]}
          />
          <Alert
            type="warning"
            showIcon
            title="已下载不等于整合约完整"
            description="必须全部必需数据通过。未知和待补齐不会自动按缺失处理，也不会触发清理。"
          />
          <Table<Record<string, unknown>>
            rowKey="dataset"
            size="small"
            pagination={false}
            dataSource={data.requirements}
            columns={[
              { title: "数据集", dataIndex: "label", width: 130 },
              {
                title: "核验",
                dataIndex: "status",
                width: 110,
                render: (value: string) => (
                  <Tag color={value === "INVALID" ? "red" : "default"}>
                    {labels[value] || value}
                  </Tag>
                ),
              },
              { title: "依据与缺口", dataIndex: "reason" },
            ]}
          />
          <ul>
            {(data.reasons ?? []).map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          <p className="muted">{data.policy}</p>
        </>
      )}
    </Drawer>
  );
}
