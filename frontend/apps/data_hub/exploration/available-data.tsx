"use client";
import { useEffect, useState } from "react";
import { Button, Card, Empty, Input, Select, Space, Table } from "antd";
import type { ExplorerCatalog } from "../api/generated";
import { Failure } from "../../../shared/ui";
import { exchangeName } from "./instrument-labels";
import { explore } from "./api";
import { ContractReview } from "../contract-review";

type Row = Record<string, unknown>;
export function AvailableData({
  catalog,
  opening,
  selectedScope,
  onOpen,
}: {
  catalog?: ExplorerCatalog;
  opening: boolean;
  selectedScope?: string;
  onOpen: (scope: string, dataset: string) => void;
}) {
  const [filter, setFilter] = useState({
    dataset: "",
    exchange: "",
    product: "",
    search: "",
    offset: 0,
  });
  const [reviewScope, setReviewScope] = useState<string>();
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
      <p className="muted">已下载记录供检查 · 尚不代表整合约验收通过</p>
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
            setFilter((f) => ({ ...f, exchange, product: "", offset: 0 }))
          }
        />
        <Select
          aria-label="已有数据品种"
          style={{ width: "100%" }}
          value={filter.product}
          disabled={!filter.exchange}
          options={[
            { value: "", label: filter.exchange ? "全部品种" : "先选择交易所" },
            ...(catalog?.products || [])
              .filter((p) => p.exchange === filter.exchange)
              .map((p) => ({
                value: String(p.product),
                label: String(p.product),
              })),
          ]}
          onChange={(product) =>
            setFilter((f) => ({ ...f, product, offset: 0 }))
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
        rowKey="scope"
        size="small"
        loading={busy}
        dataSource={rows}
        onRow={(r) => ({
          onClick: () => {
            if (!opening) onOpen(String(r.scope), filter.dataset);
          },
          tabIndex: 0,
          onKeyDown: (e) => {
            if (!opening && e.key === "Enter")
              onOpen(String(r.scope), filter.dataset);
          },
        })}
        rowClassName={(r) =>
          r.scope === selectedScope ? "instrument-active" : ""
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
            title: "合约 / 可用周期",
            render: (_, r) => (
              <div className="instrument-cell">
                <strong>{String(r.display_name || r.scope)}</strong>
                <span>
                  <span>{String(r.scope)}</span>
                </span>
                <small>{(r.periods as string[]).map(label).join(" · ")}</small>
                <small>
                  {String(r.available_start)} — {String(r.available_end)}
                </small>
              </div>
            ),
          },
          {
            title: "",
            width: 82,
            render: (_, r) => (
              <div className="instrument-action">
                <Button
                  size="small"
                  type="link"
                  disabled={opening}
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpen(String(r.scope), filter.dataset);
                  }}
                >
                  查看数据
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={(e) => {
                    e.stopPropagation();
                    setReviewScope(String(r.scope));
                  }}
                >
                  整体验收
                </Button>
              </div>
            ),
          },
        ]}
      />
      <ContractReview
        scope={reviewScope}
        onClose={() => setReviewScope(undefined)}
      />
      <p className="muted">
        每个合约只列一次。选中后打开最近发布中有记录的日期；区间可能有缺口。
      </p>
    </Card>
  );
}
