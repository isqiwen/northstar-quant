"use client";
import { useEffect, useState } from "react";
import { Button, Card, Empty, Input, Select, Space, Table } from "antd";
import type { ExplorerCatalog } from "../api/generated";
import { Failure } from "../../../shared/ui";
import { explore } from "./api";

type Row = Record<string, unknown>;
export function AvailableData({
  catalog,
  opening,
  onOpen,
}: {
  catalog?: ExplorerCatalog;
  opening: boolean;
  onOpen: (receipt: string) => void;
}) {
  const [filter, setFilter] = useState({
    dataset: "",
    exchange: "",
    product: "",
    search: "",
    offset: 0,
  });
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<Error>();
  useEffect(() => {
    const dataset = new URLSearchParams(window.location.search).get("dataset");
    if (dataset) setFilter((f) => ({ ...f, dataset }));
  }, []);
  useEffect(() => {
    let active = true;
    setBusy(true);
    setRows([]);
    setError(undefined);
    const timer = setTimeout(() => {
      void explore("/api/explorer/available", filter)
        .then((r) => {
          if (active) {
            setRows(r.rows);
            setTotal(r.total);
          }
        })
        .catch((e) => {
          if (active) setError(e);
        })
        .finally(() => {
          if (active) setBusy(false);
        });
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [filter]);
  const label = (key: unknown) =>
    String(catalog?.datasets.find((d) => d.key === key)?.label || key);
  return (
    <Card title="已有数据 · 选一份直接查看" className="explorer-available">
      <p className="muted">
        这里只列出已有非空发布记录的数据，无需猜合约和日期。点击查看后自动打开这份数据末尾最多
        31 个自然日。
      </p>
      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          aria-label="已有数据类型"
          style={{ width: 155 }}
          value={filter.dataset}
          options={[
            { value: "", label: "全部数据类型" },
            ...(catalog?.datasets || [])
              .filter((d) => d.browsable)
              .map((d) => ({ value: String(d.key), label: String(d.label) })),
          ]}
          onChange={(dataset) =>
            setFilter((f) => ({ ...f, dataset, offset: 0 }))
          }
        />
        <Select
          aria-label="已有数据交易所"
          style={{ width: 155 }}
          value={filter.exchange}
          options={[
            { value: "", label: "全部交易所" },
            ...(catalog?.exchanges || []).map((value) => ({
              value,
              label: value,
            })),
          ]}
          onChange={(exchange) =>
            setFilter((f) => ({ ...f, exchange, offset: 0 }))
          }
        />
        <Input
          aria-label="搜索已有数据合约"
          placeholder="搜索已有数据的合约，如 RB"
          allowClear
          style={{ width: 260 }}
          value={filter.search}
          onChange={(e) =>
            setFilter((f) => ({ ...f, search: e.target.value, offset: 0 }))
          }
        />
        <Button
          onClick={() =>
            setFilter({
              dataset: "",
              exchange: "",
              product: "",
              search: "",
              offset: 0,
            })
          }
        >
          查看全部已有数据
        </Button>
      </Space>
      <Failure error={error} />
      <Table<Row>
        rowKey="receipt_id"
        size="small"
        loading={busy}
        dataSource={rows}
        scroll={{ x: 700 }}
        locale={{
          emptyText: (
            <Empty description="当前筛选没有已发布数据，试试其他类型或查看全部已有数据。" />
          ),
        }}
        pagination={{
          current: filter.offset / 10 + 1,
          pageSize: 10,
          total,
          showSizeChanger: false,
          onChange: (page) =>
            setFilter((f) => ({ ...f, offset: (page - 1) * 10 })),
        }}
        columns={[
          { title: "合约", dataIndex: "scope" },
          { title: "数据类型", dataIndex: "dataset", render: label },
          {
            title: "发布区间",
            render: (_, r) => `${r.start_at} — ${r.end_at}`,
          },
          {
            title: "这份数据的记录数",
            dataIndex: "row_count",
            render: (v) => Number(v).toLocaleString(),
          },
          {
            title: "查看",
            render: (_, r) => (
              <Button
                type="link"
                disabled={opening}
                onClick={() => onOpen(String(r.receipt_id))}
              >
                查看数据
              </Button>
            ),
          },
        ]}
      />
      <p className="muted">
        发布区间可能有休市或缺口，不表示每天都有数据。同一合约可有多份不同区间的数据；打开后固定版本，后台更新不会改变正在查看的结果。
      </p>
    </Card>
  );
}
