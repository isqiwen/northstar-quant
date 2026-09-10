"use client";
import { useEffect, useState } from "react";
import { App, Card, Space, Table, Tag } from "antd";
import Link from "next/link";
import { query } from "../api/client";
import type { ExplorerRows } from "../api/generated";
import { useData } from "../../../shared/data";
import { download } from "../../../shared/api";
import { Evidence, Failure, Heading } from "../../../shared/ui";
import { explore } from "./api";
import { DataPanel } from "./data-panel";

export function Compactions() {
  const jobs = useData(query("/api/explorer/compactions"), 5000);
  return (
    <Card title="固定版本合并">
      <p>
        合并选定范围以减少重复读取，保留原始版本与逐行来源。请从数据浏览的固定结果提交。
      </p>
      <Failure error={jobs.error} />
      <Table
        rowKey="compaction_id"
        dataSource={jobs.data}
        pagination={{ pageSize: 10 }}
        columns={[
          {
            title: "计划",
            render: (_, row) => (
              <Link href={`/compactions/${row.compaction_id}`}>
                {row.compaction_id.slice(0, 12)}
              </Link>
            ),
          },
          { title: "创建时间", dataIndex: "created_at" },
          { title: "状态", dataIndex: "status" },
          { title: "失败原因", dataIndex: "error" },
        ]}
      />
    </Card>
  );
}

export function CompactionDetail({ id }: { id: string }) {
  const job = useData(query(`/api/explorer/compactions/${id}`), 3000);
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<ExplorerRows>();
  const [error, setError] = useState<Error>();
  const [exporting, setExporting] = useState(false);
  const { message } = App.useApp();
  const status = job.data?.status;
  useEffect(() => {
    if (status !== "SUCCEEDED") return;
    let active = true;
    setResult(undefined);
    explore(`/api/explorer/compactions/${id}/query`, { offset, limit: 200 })
      .then((value) => {
        if (active) {
          setResult(value);
          setError(undefined);
        }
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [id, offset, status]);
  async function exportRows() {
    if (!result) return;
    setExporting(true);
    try {
      const rows: Record<string, unknown>[] = [];
      for (let offset = 0; offset < result.total; offset += 1000) {
        const page = await explore(`/api/explorer/compactions/${id}/export`, {
          offset,
          limit: 1000,
        });
        if (page.view_id !== result.view_id)
          throw new Error("固定版本身份不一致");
        rows.push(...page.rows);
      }
      download(
        { ...result, rows, offset: 0, limit: rows.length },
        `northstar-${id}.json`,
      );
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setExporting(false);
    }
  }
  return (
    <>
      <Heading
        title="固定版本合并"
        description="后台合并不会改变旧版本。成功后按同一内容身份和逐行来源读取。"
      />
      <Failure error={job.error || error} />
      <Card>
        <Space>
          <Tag>{status ?? "读取中"}</Tag>
          <Link href="/versions">版本与来源</Link>
        </Space>
        {job.data?.error && <p>{job.data.error}</p>}
        {job.data && <Evidence value={job.data.plan} />}
      </Card>
      {result && (
        <DataPanel
          result={result}
          exporting={exporting}
          onExport={() => void exportRows()}
          onPage={setOffset}
          compacted
        />
      )}
    </>
  );
}
