"use client";
import { useEffect, useState } from "react";
import { Descriptions, Drawer, Select, Spin, Table } from "antd";
import { useData } from "../../shared/data";
import { Failure } from "../../shared/ui";
import { mutate, query } from "./api/client";
import type { CatalogRows } from "./api/generated";

const labels: Record<string, string> = {
  exchanges: "交易所",
  products: "品种",
  contracts: "合约主数据",
  trading_calendar: "交易日历",
  daily: "日线",
  weekly: "周线",
  monthly: "月线",
  settlement: "结算参数",
  trading_limits: "涨跌停与保证金",
  positions: "持仓排名",
  warehouse_receipts: "仓单日报",
  weekly_statistics: "品种交易周报",
};
const fields: Record<string, string> = {
  exchange: "交易所",
  product: "品种",
  contract: "合约",
  trading_day: "交易日",
  timestamp_label: "供应商时间标签",
  period_end: "周期结束日",
  open: "开盘价",
  high: "最高价",
  low: "最低价",
  close: "收盘价",
  volume: "成交量",
  open_interest: "持仓量",
  turnover_cny: "成交额（元）",
  settlement_price: "结算价",
  previous_settlement_price: "前结算价",
  name: "中文名称",
  listing_date: "上市日",
  last_trade_date: "最后交易日",
  last_delivery_date: "最后交割日",
  contract_multiplier: "合约乘数",
  price_tick: "最小变动价位",
};
function domainLabel(domain: string) {
  const last = domain.split("/").at(-1) || domain;
  return last.startsWith("interval=")
    ? last.slice(9).replace("min", " 分钟")
    : labels[last] || last;
}
export function CatalogSnapshot({
  id,
  onClose,
}: {
  id?: string;
  onClose: () => void;
}) {
  const detail = useData(id ? query(`/api/catalog/snapshots/${id}`) : null);
  const [domain, setDomain] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<CatalogRows>();
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setDomain("");
    setOffset(0);
    setData(undefined);
    setError(undefined);
  }, [id]);
  const domains = [
    ...new Set((detail.data?.files || []).map((f) => String(f.domain))),
  ];
  const selected =
    domain ||
    domains.find((d) => d.startsWith("market/futures/contracts/")) ||
    domains[0];
  const contract = detail.data?.contract;
  useEffect(() => {
    if (!id || !selected || !contract) return;
    let active = true;
    setBusy(true);
    setData(undefined);
    setError(undefined);
    void mutate(`/api/catalog/snapshots/${id}/query`, {
      domain: selected,
      contract,
      start: "",
      end: "",
      offset,
      limit: 50,
    })
      .then((result) => {
        if (active) setData(result);
      })
      .catch((failure: Error) => {
        if (active) setError(failure);
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [id, selected, contract, offset]);
  const columns = [
    ...new Set((data?.rows || []).flatMap((r) => Object.keys(r))),
  ];
  return (
    <Drawer title="合约标准数据" open={!!id} onClose={onClose} size="large">
      <Failure error={detail.error || error} />
      {detail.loading && <Spin />}
      {detail.data && (
        <>
          <Descriptions
            column={1}
            items={[
              {
                key: "contract",
                label: "交易所 → 品种 → 合约",
                children: `${detail.data.exchange} → ${detail.data.product} → ${detail.data.contract}`,
              },
              {
                key: "identity",
                label: "固定快照",
                children: (
                  <span style={{ overflowWrap: "anywhere" }}>{id}</span>
                ),
              },
            ]}
          />
          <p className="muted">
            查看此固定版本的标准字段。未知值显示为“—”；供应商时间标签和原始费用字段仍需语义核验。
          </p>
          <Select
            aria-label="标准数据类型"
            style={{ width: "100%", marginBottom: 16 }}
            value={selected}
            options={domains.map((value) => ({
              value,
              label: domainLabel(value),
            }))}
            onChange={(value) => {
              setDomain(value);
              setOffset(0);
            }}
          />
          <Table<Record<string, unknown>>
            size="small"
            loading={busy}
            scroll={{ x: "max-content" }}
            rowKey={(_, index) => String(offset + (index || 0))}
            dataSource={data?.rows || []}
            columns={columns.map((key) => ({
              key,
              dataIndex: key,
              title: fields[key] || key,
              render: (value) =>
                value === null || value === undefined ? "—" : String(value),
            }))}
            pagination={{
              current: offset / 50 + 1,
              pageSize: 50,
              total: data?.total || 0,
              showSizeChanger: false,
              onChange: (page) => setOffset((page - 1) * 50),
            }}
          />
        </>
      )}
    </Drawer>
  );
}
