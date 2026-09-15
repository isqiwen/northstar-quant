"use client";

import { Button, Card, Input, Select, Space, Table, Tag } from "antd";
import { useEffect, useState } from "react";
import type { CollectionPage } from "./api/generated";
import { queryCollections } from "./sync-query";
import { Failure } from "../../shared/ui";
import { exchangeName } from "./exploration/instrument-labels";

const states: Record<string, string> = {
  COLLECTING: "待采集 / 采集中",
  VERIFYING: "待核心接纳核验",
  REJECTED: "已拒绝",
  PUBLISHED: "核心已接纳 / 已发布",
};
type Row = Record<string, unknown>;
export function SyncContracts({
  onReview,
  onRequests,
}: {
  onReview: (scope: string) => void;
  onRequests: (scope: string) => void;
}) {
  const [filter, setFilter] = useState({
    exchange: "",
    product: "",
    search: "",
    status: "",
    page: 1,
  });
  const [result, setResult] = useState<CollectionPage>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true);
    setResult(undefined);
    async function refresh() {
      try {
        const value = await queryCollections(
          {
            exchange: filter.exchange,
            product: filter.product,
            search: filter.search,
            status: filter.status,
            offset: (filter.page - 1) * 20,
            limit: 20,
          },
          abort.signal,
        );
        if (active) {
          setResult(value);
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
  }, [filter]);
  return (
    <Card title="合约数据与进度">
      <p className="muted">
        每行一个合约。核心数据通过后发布；辅助完整度、质量与各周期可用性在合约详情中查看。
      </p>
      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          aria-label="采集合约交易所"
          style={{ width: 160 }}
          value={filter.exchange}
          options={[
            { value: "", label: "全部交易所" },
            ...(result?.exchanges ?? []).map((value) => ({
              value,
              label: exchangeName(value),
            })),
          ]}
          onChange={(exchange) =>
            setFilter({ ...filter, exchange, product: "", page: 1 })
          }
        />
        <Select
          aria-label="采集合约品种"
          style={{ width: 150 }}
          value={filter.product}
          options={[
            { value: "", label: "全部品种" },
            ...(result?.products ?? []).map((value) => ({
              value,
              label: value,
            })),
          ]}
          onChange={(product) => setFilter({ ...filter, product, page: 1 })}
        />
        <Input.Search
          aria-label="搜索采集合约"
          placeholder="中文名称 / 合约代码"
          allowClear
          style={{ width: 230 }}
          onSearch={(search) => setFilter({ ...filter, search, page: 1 })}
        />
        <Select
          aria-label="合约采集状态"
          style={{ width: 190 }}
          value={filter.status}
          options={[
            { value: "", label: "全部合约状态" },
            ...Object.entries(states).map(([value, label]) => ({
              value,
              label,
            })),
          ]}
          onChange={(status) => setFilter({ ...filter, status, page: 1 })}
        />
      </Space>
      <Failure error={error} />
      <Table<Row>
        rowKey="scope"
        size="small"
        loading={loading}
        scroll={{ x: 900 }}
        dataSource={result?.items ?? []}
        pagination={{
          current: filter.page,
          pageSize: 20,
          total: result?.total ?? 0,
          showSizeChanger: false,
          showTotal: (total) => `共 ${total} 个合约`,
          onChange: (page) => setFilter({ ...filter, page }),
        }}
        columns={[
          {
            title: "交易所",
            width: 90,
            dataIndex: "exchange",
            render: (value) => exchangeName(String(value)),
          },
          {
            title: "合约类型",
            width: 150,
            render: (_, row) => {
              const type = row.contract_type as
                Record<string, string> | undefined;
              return (
                <>
                  <div>{type?.label ?? "待核实"}</div>
                  <div className="muted">{type?.delivery}</div>
                </>
              );
            },
          },
          {
            title: "合约",
            width: 180,
            render: (_, row) => (
              <>
                <strong>{String(row.display_name || row.scope)}</strong>
                <div className="muted">{String(row.scope)}</div>
              </>
            ),
          },
          {
            title: "采集 / 发布",
            width: 160,
            render: (_, row) => (
              <Tag
                color={
                  row.status === "PUBLISHED"
                    ? "green"
                    : row.status === "REJECTED"
                      ? "red"
                      : "blue"
                }
              >
                {states[String(row.status)]}
              </Tag>
            ),
          },
          {
            title: "原因",
            render: (_, row) =>
              String(
                row.reason ||
                  (row.lifecycle_status !== "ENDED"
                    ? row.lifecycle_reason
                    : "—"),
              ),
            ellipsis: true,
          },
          {
            title: "查看",
            fixed: "right",
            width: 190,
            render: (_, row) => (
              <Space>
                <Button type="link" onClick={() => onReview(String(row.scope))}>
                  合约详情
                </Button>
                <Button
                  type="link"
                  onClick={() => onRequests(String(row.scope))}
                >
                  请求诊断
                </Button>
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}
