"use client";
import { Card, Spin, Tabs } from "antd";
import { useParams } from "next/navigation";
import { useData, type Query } from "./data";
import type {
  DatasetSummary,
  DatasetDetails,
  DatasetLineage,
} from "./api/generated";
import {
  Evidence,
  Failure,
  Fields,
  Heading,
  Identity,
  Records,
  Status,
  show,
} from "./ui";
export const datasetColumns = [
  { title: "合约", dataIndex: "symbol" },
  {
    title: "交易日",
    dataIndex: "trading_days",
    render: (days: string[]) => days.join("、"),
  },
  { title: "分钟数", dataIndex: "bar_count" },
  {
    title: "快照",
    dataIndex: "snapshot_id",
    render: (v: string) => <Identity value={v} to={`/datasets/${v}`} />,
  },
];
export function DatasetList({
  read,
}: {
  read: () => Query<DatasetSummary[]> | null;
}) {
  const q = useData(read());
  return (
    <>
      <Heading
        title="数据快照"
        description="固定发布的数据及其来源、质量与可得时间。"
      />
      <Failure error={q.error} />
      <Records
        rows={q.data}
        loading={q.loading}
        columns={datasetColumns}
        rowKey="snapshot_id"
        title="已发布数据"
      />
    </>
  );
}
export function DatasetDetail({
  read,
  readLineage,
}: {
  read: (id: string) => Query<DatasetDetails> | null;
  readLineage?: (id: string) => Query<DatasetLineage> | null;
}) {
  const lineage = !!readLineage;
  const { id } = useParams<{ id: string }>();
  const q = useData(id ? read(id) : null);
  const l = useData(id && readLineage ? readLineage(id) : null);
  return (
    <>
      <Heading
        title={`${q.data?.symbol || "数据"} · 固定快照`}
        description="固定内容与来源证据不会随模板编辑改变。"
      />
      <Failure error={q.error} />
      {q.loading ? (
        <Spin />
      ) : (
        q.data && (
          <>
            <Card>
              <Fields
                value={{
                  合约: q.data.symbol,
                  交易日: q.data.trading_days.join("、"),
                  分钟数: q.data.bar_count,
                  快照: id,
                }}
              />
            </Card>
            <Tabs
              items={[
                {
                  key: "quality",
                  label: "质量与来源",
                  children: (
                    <>
                      <Evidence
                        value={q.data}
                        title="数据质量、来源与时间口径"
                      />
                      {lineage && (
                        <>
                          <Failure error={l.error} />
                          <Evidence value={l.data} title="来源与发布关系" />
                        </>
                      )}
                    </>
                  ),
                },
              ]}
            />
          </>
        )
      )}
    </>
  );
}
