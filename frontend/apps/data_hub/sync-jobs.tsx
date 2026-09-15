"use client";

import { App, Button, Card, Select, Space, Table } from "antd";
import { useEffect, useState } from "react";
import { query } from "./api/client";
import { querySyncJobs } from "./sync-query";
import type { SyncJobPage } from "./api/generated";
import { fetchQuery } from "../../shared/data";
import { Failure } from "../../shared/ui";

export type JobFilter = { dataset: string; status: string; page: number };
type Row = Record<string, unknown>;
const labels: Record<string, string> = {
  PENDING: "待同步",
  RUNNING: "处理中",
  WAITING: "等待重试或源端发布",
  BLOCKED: "需处理",
  VALIDATED: "响应已校验",
  SPLIT: "请求已拆分",
};

export function SyncJobs({
  ownerScope,
  datasets,
  filter,
  onFilter,
  onDetail,
}: {
  ownerScope: string;
  datasets: Row[];
  filter: JobFilter;
  onFilter: (value: JobFilter) => void;
  onDetail: (value: Row) => void;
}) {
  const { message } = App.useApp();
  const [result, setResult] = useState<SyncJobPage>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setResult(undefined);
    setError(undefined);
    setLoading(true);
    async function refresh() {
      try {
        const page = await querySyncJobs(
          {
            owner_scope: ownerScope,
            dataset: filter.dataset,
            status: filter.status,
            offset: (filter.page - 1) * 10,
            limit: 10,
          },
          abort.signal,
        );
        if (active) {
          setResult(page);
          setError(undefined);
        }
      } catch (e) {
        if (active) {
          setError(e as Error);
          setResult(undefined);
        }
      } finally {
        if (active) {
          setLoading(false);
          timer = setTimeout(refresh, 3000);
        }
      }
    }
    void refresh();
    return () => {
      active = false;
      abort.abort();
      clearTimeout(timer);
    };
  }, [ownerScope, filter.dataset, filter.status, filter.page]);
  const names = Object.fromEntries(
    datasets.map((r) => [String(r.key), String(r.label)]),
  );
  async function open(row: Row, receipt = false) {
    try {
      onDetail(
        await fetchQuery(
          query(
            receipt
              ? `/api/sync/receipts/${row.receipt_id}`
              : `/api/sync/jobs/${row.request_id}`,
          ),
        ),
      );
    } catch (e) {
      message.error((e as Error).message);
    }
  }
  return (
    <Card title="内部采集记录">
      <p className="muted">
        {ownerScope
          ? `仅查看 ${ownerScope} 所属请求，包含关联的品种级数据。`
          : "采集服务的技术诊断记录。"}
        响应校验不代表完整合约已发布。
      </p>
      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          aria-label="任务数据类型"
          style={{ width: 220 }}
          value={filter.dataset}
          options={[
            { value: "", label: "全部数据" },
            ...datasets.map((r) => ({
              value: String(r.key),
              label: String(r.label),
            })),
          ]}
          onChange={(dataset) => onFilter({ ...filter, dataset, page: 1 })}
        />
        <Select
          aria-label="任务状态"
          style={{ width: 220 }}
          value={filter.status}
          options={[
            { value: "", label: "全部状态" },
            ...Object.entries(labels).map(([value, label]) => ({
              value,
              label,
            })),
          ]}
          onChange={(status) => onFilter({ ...filter, status, page: 1 })}
        />
        <Button onClick={() => onFilter({ dataset: "", status: "", page: 1 })}>
          清除筛选
        </Button>
      </Space>
      <Failure error={error} />
      <Table
        scroll={{ x: 1000 }}
        rowKey="request_id"
        loading={loading}
        dataSource={result?.items ?? []}
        pagination={{
          current: filter.page,
          pageSize: 10,
          total: result?.total ?? 0,
          showSizeChanger: false,
          showTotal: (total) => `共 ${total} 条`,
          onChange: (page) => onFilter({ ...filter, page }),
        }}
        columns={[
          {
            title: "数据",
            render: (_, r) => names[String(r.dataset)] ?? String(r.dataset),
          },
          { title: "合约 / 范围", dataIndex: "scope" },
          {
            title: "区间",
            render: (_, r) => `${r.start_at || "目录"} — ${r.end_at || ""}`,
          },
          {
            title: "状态",
            render: (_, r) => labels[String(r.status)] ?? String(r.status),
          },
          { title: "请求次数", dataIndex: "attempts" },
          { title: "原因", dataIndex: "error" },
          {
            title: "下次重试",
            render: (_, r) =>
              r.status === "WAITING" && r.next_at
                ? new Date(String(r.next_at)).toLocaleString()
                : "—",
          },
          {
            title: "查看",
            render: (_, r) => (
              <Space>
                <Button size="small" onClick={() => void open(r)}>
                  记录
                </Button>
                {!!r.receipt_id && (
                  <Button size="small" onClick={() => void open(r, true)}>
                    固定数据
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}
