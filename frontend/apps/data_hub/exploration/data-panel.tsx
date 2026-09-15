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
  Spin,
  Drawer,
} from "antd";
import Link from "next/link";
import type { ExplorerRows } from "../api/generated";
import { Evidence, Failure } from "../../../shared/ui";
import { CompactButton } from "./compact-button";
import { explore } from "./api";
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
  "settle",
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
  compacted = false,
  instrumentName,
}: {
  compacted?: boolean;
  instrumentName?: string;
  result: ExplorerRows;
  exporting: boolean;
  onExport: () => void;
  onPage: (offset: number) => void;
}) {
  const [chartData, setChartData] = useState<ExplorerRows>();
  const [chartError, setChartError] = useState<Error>();
  useEffect(() => {
    let active = true;
    setChartData(undefined);
    setChartError(undefined);
    if (compacted || !result.total) return;
    void explore("/api/explorer/chart", {
      dataset: result.dataset,
      scope: result.scope,
      start: result.start,
      end: result.end,
      receipt_ids: result.receipt_ids,
    })
      .then((value) => {
        if (value.view_id !== result.view_id)
          throw new Error("图表版本与明细不一致");
        if (active) setChartData(value);
      })
      .catch((error) => {
        if (active) setChartError(error);
      });
    return () => {
      active = false;
    };
    // view_id binds the entire immutable range; table pagination must not reload or reset the chart.
  }, [result.view_id, compacted]);
  const chartRows = compacted ? result.rows : chartData?.rows || [];
  const [selected, setSelected] = useState("");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const settlement = result.dataset === "settlement";
  const [columns, setColumns] = useState(() =>
    result.fields
      .map((f) => String(f.key))
      .sort((a, b) => {
        const rank = (k: string) =>
          fieldOrder.includes(k) ? fieldOrder.indexOf(k) : 100;
        return rank(a) - rank(b) || a.localeCompare(b);
      }),
  );
  const choosePoint = useCallback((key: string) => {
    setSelected(key);
    setDetailsOpen(true);
  }, []);
  useEffect(() => setSelected(""), [result.offset]);
  const selectedRow =
    chartRows.find((r) => r._key === selected) ||
    result.rows.find((r) => r._key === selected);
  return (
    <>
      <Card className="quote-summary">
        <h2>
          {instrumentName || result.scope}{" "}
          <small>
            {instrumentName && instrumentName !== result.scope
              ? result.scope
              : ""}
          </small>
        </h2>
        <Space wrap>
          <Tag color="blue">Tushare</Tag>
          <Tag>
            {result.scope} · {result.dataset}
          </Tag>
          <Tag>
            {result.start} — {result.end}
          </Tag>
          <Tag>Asia/Shanghai</Tag>

          <strong>{result.total.toLocaleString()} 条记录</strong>
          <Button onClick={() => setDetailsOpen(true)}>数据明细与来源</Button>
          <Button
            disabled={!result.export_allowed || !result.total}
            loading={exporting}
            onClick={() => onExport()}
          >
            导出所选范围
          </Button>
        </Space>
      </Card>
      {!result.rows.length ? (
        <Card>
          <Empty description="所选范围暂无已发布记录；请查看覆盖与质量" />
        </Card>
      ) : (
        <>
          <Card
            className="market-chart-card"
            title={`${settlement ? "结算价" : "行情图"} · ${compacted ? "当前明细页" : "固定范围"}`}
          >
            <Failure error={chartError} />
            {!compacted && !chartData && !chartError && (
              <Spin tip="正在读取固定范围行情">
                <div style={{ height: 520 }} />
              </Spin>
            )}
            {settlement && chartRows.some((r) => r.settle != null) ? (
              <PriceChart rows={chartRows} onSelect={choosePoint} settlement />
            ) : !settlement &&
              chartRows.some((r) =>
                ["open", "high", "low", "close"].every((k) => r[k] != null),
              ) ? (
              <PriceChart rows={chartRows} onSelect={choosePoint} />
            ) : compacted || chartData ? (
              <Empty
                description={
                  settlement
                    ? "源端结算价为空，请查看明细"
                    : "源端未提供完整 OHLC，请查看精确明细"
                }
              />
            ) : null}
            <p className="muted">
              {settlement
                ? "供应商历史结算价不代表账户已经结算。缺值不连线；费用与保证金保持供应商原始口径，未知为空。点击数据点查看精确记录。"
                : "横轴按供应商记录排列，不填充休市价格。点击蜡烛查看精确记录；缺失成交量/持仓量保留为空。"}
              {compacted
                ? "合并视图图表显示当前明细页。"
                : "图表覆盖完整固定范围，明细翻页不会截断图表或均线。"}
              图表数值仅用于显示。
            </p>
          </Card>
        </>
      )}
      <Drawer
        rootClassName="market-drawer"
        title="数据明细与来源"
        width="min(1100px, 95vw)"
        open={detailsOpen}
        onClose={() => setDetailsOpen(false)}
      >
        <p className="muted">
          {result.note}{" "}
          {result.export_allowed ? "" : "来源当前未开放导出权限。"}
        </p>
        {!compacted && <CompactButton result={result} />}
        <details>
          <summary>查询读取统计</summary>
          <p className="muted">
            涉及 {Number(result.scan.files)} 个文件；解码{" "}
            {Number(result.scan.rows_decoded).toLocaleString()} 条记录， 读取{" "}
            {Number(result.scan.row_groups_read)} /{" "}
            {Number(result.scan.row_groups_total)} 个行组。 文件身份仍完整核验{" "}
            {Number(result.scan.verified_bytes).toLocaleString()}{" "}
            字节，行组裁剪减少解码， 不表示减少了文件哈希核验的读取量。
          </p>
        </details>
        <Card
          title={settlement ? "结算参数明细" : "行情明细"}
          extra={
            <span>
              明细第 {result.offset + 1}–{result.offset + result.rows.length} 条
            </span>
          }
        >
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
              title: String(result.fields.find((f) => f.key === k)?.label || k),
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
        </Card>{" "}
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
      </Drawer>
    </>
  );
}
