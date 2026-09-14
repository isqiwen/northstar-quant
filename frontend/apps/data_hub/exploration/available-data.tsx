"use client";
import { useEffect, useState } from "react";
import { Button, Card, Empty, Input, Select, Space, Table } from "antd";
import type { ExplorerCatalog } from "../api/generated";
import { Failure } from "../../../shared/ui";
import { exchangeName } from "./instrument-labels";
import { explore } from "./api";

type Row = Record<string, unknown>;
export function AvailableData({
  catalog,
  opening,
  selectedReceipt,
  onOpen,
}: {
  catalog?: ExplorerCatalog;
  opening: boolean;
  selectedReceipt?: string;
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
    <Card title="已有数据 · 合约" className="explorer-available">
      <p className="muted">选择合约查看历史行情 · 自动定位有数据的日期</p>
      <Space wrap className="instrument-search">
        <Select
          aria-label="已有数据类型"
          style={{ width: "100%" }}
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
          style={{ width: "100%" }}
          value={filter.exchange}
          options={[
            { value: "", label: "全部交易所" },
            ...(catalog?.exchanges || []).map((value) => ({
              value,
              label: exchangeName(value),
            })),
          ]}
          onChange={(exchange) =>
            setFilter((f) => ({ ...f, exchange, offset: 0 }))
          }
        />
        <Input
          aria-label="搜索已有数据合约"
          placeholder="中文名称 / 合约代码"
          allowClear
          style={{ width: "100%" }}
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
        onRow={(r) => ({
          onDoubleClick: () => {
            if (!opening) onOpen(String(r.receipt_id));
          },
        })}
        rowClassName={(r) =>
          r.receipt_id === selectedReceipt ? "instrument-active" : ""
        }
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
          {
            title: "合约 / 发布区间",
            render: (_, r) => (
              <div className="instrument-cell">
                <strong>{String(r.display_name || r.scope)}</strong>
                <span>
                  <span>{String(r.scope)}</span> · {label(r.dataset)}
                </span>
                <small>
                  {String(r.start_at)} — {String(r.end_at)}
                </small>
              </div>
            ),
          },
          {
            title: "记录",
            width: 82,
            render: (_, r) => (
              <div className="instrument-action">
                <span>{Number(r.row_count).toLocaleString()}</span>
                <Button
                  size="small"
                  type="link"
                  disabled={opening}
                  onClick={() => onOpen(String(r.receipt_id))}
                >
                  查看数据
                </Button>
              </div>
            ),
          },
        ]}
      />
      <p className="muted">
        区间可能有缺口。点击打开末尾最多31个自然日，可在下方调整范围。
      </p>
    </Card>
  );
}
