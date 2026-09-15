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
                label: "核心接纳",
                children: (
                  <Tag
                    color={
                      data.admitted
                        ? "green"
                        : data.status === "INVALID"
                          ? "red"
                          : "orange"
                    }
                  >
                    {data.admitted
                      ? "核心已通过"
                      : labels[data.status] || data.status}
                  </Tag>
                ),
              },
              {
                key: "completeness",
                label: "完整度 / 质量",
                children: `核心 ${data.completeness.core_verified}/${data.completeness.core_total}；辅助资料 ${data.completeness.auxiliary_verified ?? 0}/${data.completeness.auxiliary_total ?? 0}；${data.quality.status === "COMPLETE" ? "全部已核验" : data.quality.status === "GAPS" ? "存在辅助缺失或待核验项" : "核心尚未通过"}`,
              },
            ]}
          />
          <Alert
            type={data.admitted ? "success" : "warning"}
            showIcon
            title={
              data.admitted
                ? "核心数据已接纳，辅助资料单独检查"
                : "核心数据尚未通过接纳检查"
            }
            description="辅助历史缺失不拒绝整个合约。只有单项核验通过的数据进入发布；策略仍需检查所需周期、字段和有效条款。"
          />
          <Table<Record<string, unknown>>
            rowKey="dataset"
            size="small"
            pagination={false}
            dataSource={data.requirements}
            columns={[
              { title: "数据集", dataIndex: "label", width: 130 },
              {
                title: "接纳作用",
                dataIndex: "admission_role",
                width: 90,
                render: (value: string) =>
                  value === "CORE" ? "核心必需" : "辅助资料",
              },
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
                    {!!row.diagnosis && (
                      <div>
                        <Tag>
                          {String(
                            (row.diagnosis as Record<string, unknown>).label,
                          )}
                        </Tag>
                        <div className="muted">
                          {String(
                            (row.diagnosis as Record<string, unknown>).action,
                          )}
                        </div>
                      </div>
                    )}
                    <div>{String(row.reason ?? "")}</div>
                    {Array.isArray(
                      (row.evidence as Record<string, unknown> | undefined)
                        ?.volume_differences,
                    ) &&
                      (
                        (row.evidence as Record<string, unknown>)
                          .volume_differences as unknown[]
                      ).length > 0 && (
                        <details>
                          <summary>
                            成交量差异明细（最多20日，不自动改值）
                          </summary>
                          {(
                            (row.evidence as Record<string, unknown>)
                              .volume_differences as Record<string, string>[]
                          ).map((item) => (
                            <div key={item.date}>
                              {item.date}：分钟合计 {item.minute_volume}{" "}
                              手；日线 {item.daily_volume} 手
                            </div>
                          ))}
                        </details>
                      )}
                    {!!(row.evidence as Record<string, unknown> | undefined)
                      ?.optional_unknown_fields && (
                      <div className="muted">
                        辅助字段缺失：
                        {Object.entries(
                          (row.evidence as Record<string, unknown>)
                            .optional_unknown_fields as Record<
                            string,
                            { count: number }
                          >,
                        )
                          .map(
                            ([field, detail]) =>
                              `${field}（${detail.count}条）`,
                          )
                          .join("、") || "无"}
                      </div>
                    )}
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
          {Array.isArray(data.quality.warnings) &&
            data.quality.warnings.length > 0 && (
              <Alert
                type="warning"
                title="完整度与质量提示（不等于合约被拒绝）"
                description={
                  <ul>
                    {data.quality.warnings.map((warning, index) => (
                      <li key={index}>{String(warning)}</li>
                    ))}
                  </ul>
                }
              />
            )}
          <p className="muted">{data.policy}</p>
        </>
      )}
    </Drawer>
  );
}
