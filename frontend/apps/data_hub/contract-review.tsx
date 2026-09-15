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
  UNKNOWN: "规则待核实",
  NOT_APPLICABLE: "不适用",
  RELATED: "关联研究资料",
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
                key: "type",
                label: "合约类型 / 交割方式",
                children: `${data.contract_type.label} / ${data.contract_type.delivery}`,
              },
              {
                key: "typeBasis",
                label: "分类依据",
                children: String(data.contract_type.basis),
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
            type={data.admitted ? "success" : "warning"}
            showIcon
            title={
              data.admitted
                ? "适用数据已通过整体验收"
                : "按合约类型核验必需数据"
            }
            description="不适用资料、独立研究序列不阻塞发布；规则待核实、采集中和数据异常分别处理。请求完成不代表记录完整。"
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
              {
                title: "依据与缺口",
                render: (_, row) => (
                  <>
                    <div>{String(row.reason ?? "")}</div>
                    {typeof row.reference === "string" && (
                      <a href={row.reference} target="_blank" rel="noreferrer">
                        规则参考
                      </a>
                    )}
                  </>
                ),
              },
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
