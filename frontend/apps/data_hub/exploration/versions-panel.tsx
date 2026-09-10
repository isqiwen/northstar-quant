"use client";
import { Card, Space, Table, Typography } from "antd";
import Link from "next/link";
import { useState } from "react";
import { RevisionCompare } from "./revision-comparison";
import type { ExplorerRange } from "../api/generated";
import { scopeUrl } from "./states";
type Row = Record<string, unknown>;
export function VersionsPanel({
  versions,
  linkRange,
  versionPage,
  versionTotal,
  onPage,
}: {
  versions: Row[];
  linkRange: ExplorerRange;
  versionPage: number;
  versionTotal: number;
  onPage: (offset: number) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  return (
    <Card title="已发布的不可变版本">
      <RevisionCompare selected={selected} />
      <Table<Row>
        rowKey="receipt_id"
        rowSelection={{
          selectedRowKeys: selected,
          hideSelectAll: true,
          preserveSelectedRowKeys: true,
          onChange: (keys) => setSelected(keys.map(String)),
          getCheckboxProps: (r) => ({
            disabled:
              selected.length >= 2 && !selected.includes(String(r.receipt_id)),
          }),
        }}
        dataSource={versions}
        scroll={{ x: 950, y: 500 }}
        pagination={{
          current: versionPage,
          pageSize: 50,
          total: versionTotal,
          showSizeChanger: false,
          onChange: (p) => onPage((p - 1) * 50),
        }}
        columns={[
          {
            title: "区间",
            render: (_, r) => `${r.start_at} — ${r.end_at}`,
          },
          { title: "发布时间", dataIndex: "created_at" },
          { title: "记录数", dataIndex: "row_count" },
          {
            title: "固定版本",
            render: (_, r) => (
              <Typography.Text copyable>{String(r.receipt_id)}</Typography.Text>
            ),
          },
          {
            title: "追溯",
            render: (_, r) => (
              <Space>
                <Link
                  href={`${scopeUrl("/browse", { ...linkRange, start: String(r.start_at), end: String(r.end_at) })}&receipt=${r.receipt_id}`}
                >
                  浏览此版本
                </Link>
                <Link href={`/sync?request=${r.request_id}`}>
                  校验与同步记录
                </Link>
              </Space>
            ),
          },
        ]}
      />
      <p className="muted">
        修订保留新旧版本；发布记录不代表已完成跨日研究语义。研究快照与供应商响应版本分别管理。
      </p>
    </Card>
  );
}
