"use client";

import { Identity, Records } from "../../shared/ui";
import type { PositionEntry } from "./api/generated";

export function CashFlows({ entries }: { entries?: PositionEntry[] }) {
  return (
    <Records
      title="已识别资金流水（不代表完整资金核对）"
      rows={entries?.flatMap((entry) => entry.added_cash_flows ?? [])}
      rowKey="cash_flow_id"
      columns={[
        {
          title: "流水",
          dataIndex: "cash_flow_id",
          render: (value) => <Identity value={value} />,
        },
        { title: "金额（正入负出）", dataIndex: "amount" },
        { title: "币种", dataIndex: "currency" },
        { title: "发生时间（UTC）", dataIndex: "transferred_at" },
        { title: "接收时间（UTC）", dataIndex: "available_at" },
        {
          title: "冲正原流水",
          dataIndex: "reverses_id",
          render: (value) => (value ? <Identity value={value} /> : "—"),
        },
        {
          title: "来源回报",
          dataIndex: "source_reference",
          render: (value: string) => {
            const [kind, id, sequence] = value.split(":");
            return (
              <Identity
                value={`回报 ${sequence}`}
                to={kind === "stream" ? `/streams/${id}` : `/broker/${id}`}
              />
            );
          },
        },
      ]}
    />
  );
}
