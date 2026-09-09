"use client";
import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  Empty,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import Link from "next/link";
import type { ExplorerRows } from "../api/generated";
import { Evidence } from "../../../shared/ui";
import { PriceChart } from "./price-chart";
type Row = Record<string, unknown>;
const fieldOrder = [
  "ts_code",
  "trade_time",
  "trade_date",
  "end_date",
  "open",
  "high",
  "low",
  "close",
  "vol",
  "oi",
  "amount_cny",
  "amount",
];
export function DataPanel({
  result,
  exporting,
  onExport,
  onPage,
}: {
  result: ExplorerRows;
  exporting: boolean;
  onExport: () => void;
  onPage: (offset: number) => void;
}) {
  const [selected, setSelected] = useState("");
  const [columns, setColumns] = useState(() =>
    result.fields
      .map((f) => String(f.key))
      .sort((a, b) => {
        const rank = (k: string) =>
          fieldOrder.includes(k) ? fieldOrder.indexOf(k) : 100;
        return rank(a) - rank(b) || a.localeCompare(b);
      }),
  );
  const choosePoint = useCallback((key: string) => setSelected(key), []);
  useEffect(() => setSelected(""), [result.offset]);
  const selectedRow = result.rows.find((r) => r._key === selected);
  return (
    <>
      <Card>
        <Space wrap>
          <Tag color="blue">Tushare</Tag>
          <Tag>
            {result.scope} · {result.dataset}
          </Tag>
          <Tag>Asia/Shanghai</Tag>
          <Tag>固定 {result.receipt_ids.length} 个分片</Tag>
          <strong>{result.total.toLocaleString()} 条记录</strong>
          <Button
            disabled={!result.export_allowed || !result.total}
            loading={exporting}
            onClick={() => onExport()}
          >
            导出所选范围
          </Button>
        </Space>
        <p className="muted">
          {result.note}{" "}
          {result.export_allowed ? "" : "来源当前未开放导出权限。"}
        </p>
      </Card>
      {!result.rows.length ? (
        <Card>
          <Empty description="所选范围暂无已发布记录；请查看覆盖与质量" />
        </Card>
      ) : (
        <>
          <Card
            title={`行情图 · 当前第 ${result.offset + 1}–${result.offset + result.rows.length} 条`}
          >
            {result.rows.some((r) =>
              ["open", "high", "low", "close"].every((k) => r[k] != null),
            ) ? (
              <PriceChart rows={result.rows} onSelect={choosePoint} />
            ) : (
              <Empty description="源端未提供完整 OHLC，请查看精确明细" />
            )}
            <p className="muted">
              横轴按供应商记录排列，不填充休市价格。缩放只作用于当前页；点击蜡烛查看精确记录。缺失成交量/持仓量保留为空，图表数值仅用于显示。
            </p>
          </Card>
          <Card title="行情明细">
            <Select
              aria-label="显示字段"
              mode="multiple"
              value={columns}
              onChange={setColumns}
              style={{ width: "100%", marginBottom: 16 }}
              options={result.fields.map((f) => ({
                value: String(f.key),
                label: String(f.label),
              }))}
            />
            <Table<Row>
              rowKey="_key"
              dataSource={result.rows}
              size="small"
              scroll={{ x: "max-content", y: 460 }}
              onRow={(r) => ({
                onClick: () => setSelected(String(r._key)),
              })}
              rowClassName={(r) =>
                r._key === selected ? "explorer-selected" : ""
              }
              pagination={{
                current: result.offset / 200 + 1,
                pageSize: 200,
                total: result.total,
                showSizeChanger: false,
                onChange: (p) => onPage((p - 1) * 200),
              }}
              columns={columns.map((k) => ({
                title: String(
                  result.fields.find((f) => f.key === k)?.label || k,
                ),
                dataIndex: k,
                render: (v) =>
                  v == null ? (
                    <Tag>缺失</Tag>
                  ) : (
                    <Typography.Text copyable={{ text: String(v) }}>
                      {String(v)}
                    </Typography.Text>
                  ),
              }))}
            />
          </Card>
        </>
      )}
      {selectedRow && (
        <Card title="选中记录 · 精确值与来源版本">
          <Evidence value={selectedRow} />
        </Card>
      )}
      <Tabs
        items={[
          {
            key: "fields",
            label: "字段说明与范围统计",
            children: (
              <Table<Row>
                rowKey="key"
                pagination={false}
                dataSource={result.fields}
                columns={[
                  { title: "字段", dataIndex: "key" },
                  { title: "含义", dataIndex: "label" },
                  { title: "单位 / 时间口径", dataIndex: "unit" },
                  { title: "缺失数", dataIndex: "missing" },
                  { title: "最小值", dataIndex: "minimum" },
                  { title: "最大值", dataIndex: "maximum" },
                ]}
              />
            ),
          },
          {
            key: "versions",
            label: "固定版本与来源",
            children: (
              <Card>
                <Typography.Text copyable={{ text: result.view_id }}>
                  查询版本 {result.view_id.slice(0, 12)}
                </Typography.Text>
                {result.versions.map((r) => (
                  <p key={String(r.receipt_id)}>
                    <Tag>{String(r.receipt_id)}</Tag>
                    {String(r.start_at)} — {String(r.end_at)} ·{" "}
                    <Link href={`/sync?request=${r.request_id}`}>
                      同步记录与校验规则
                    </Link>
                  </p>
                ))}
                {result.sources.map((r) => (
                  <p key={String(r.source_id)}>
                    <Link href={`/sources/${r.source_id}`}>
                      查看来源记录 {String(r.source_id)}
                    </Link>
                  </p>
                ))}
              </Card>
            ),
          },
        ]}
      />
    </>
  );
}
