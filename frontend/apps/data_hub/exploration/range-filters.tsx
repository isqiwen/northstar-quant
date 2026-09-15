"use client";
import { Button, Card, Form, Input, Select, Space, Tag } from "antd";
import type { ExplorerCatalog, ExplorerRange } from "../api/generated";
import { exchangeName } from "./instrument-labels";
type Row = Record<string, unknown>;
export function RangeFilters({
  catalog,
  filter,
  exchange,
  product,
  contracts,
  contractTotal,
  preset,
  busy,
  onSubmit,
  onChange,
  onSearch,
  onExchange,
  onProduct,
}: {
  catalog?: ExplorerCatalog;
  filter: ExplorerRange;
  exchange: string;
  product: string;
  contracts: Row[];
  contractTotal: number;
  preset: string[];
  busy: boolean;
  onSubmit: () => void;
  onChange: (value: Partial<ExplorerRange>) => void;
  onSearch: (value: string) => void;
  onExchange: (value: string) => void;
  onProduct: (value: string) => void;
}) {
  return (
    <Card title="自定义合约和日期范围" className="explorer-filters">
      <Form layout="vertical" onFinish={onSubmit}>
        <div className="explorer-controls">
          <Form.Item label="交易所">
            <Select
              aria-label="交易所"
              value={exchange}
              options={[
                { value: "", label: "全部交易所" },
                ...(catalog?.exchanges || []).map((v) => ({
                  value: v,
                  label: exchangeName(v),
                })),
              ]}
              onChange={(v) => {
                onExchange(v);
              }}
            />
          </Form.Item>
          <Form.Item label="品种">
            <Select
              aria-label="品种"
              showSearch
              optionFilterProp="label"
              value={product}
              options={[
                { value: "", label: "全部品种" },
                ...Array.from(
                  new Set(
                    (catalog?.products || [])
                      .filter((r) => !exchange || r.exchange === exchange)
                      .map((r) => String(r.product)),
                  ),
                ).map((v) => ({ value: v, label: v })),
              ]}
              onChange={(v) => {
                onProduct(v);
              }}
            />
          </Form.Item>
          <Form.Item label="合约">
            <Select
              aria-label="合约"
              showSearch
              filterOption={false}
              onSearch={onSearch}
              value={filter.scope || undefined}
              placeholder="搜索中文名称或合约代码"
              notFoundContent="目录中暂无匹配合约"
              options={contracts.map((r) => ({
                value: String(r.ts_code),
                label: `${r.display_name || r.ts_code} · ${r.ts_code}${r.kind === "2" ? " · 连续序列" : ""}`,
              }))}
              onChange={(v) => onChange({ scope: v })}
            />
          </Form.Item>
          <Form.Item label="数据类型">
            <Select
              aria-label="数据类型"
              value={filter.dataset}
              options={(catalog?.datasets || [])
                .filter((r) => r.browsable)
                .map((r) => ({
                  value: String(r.key),
                  label: String(r.label),
                }))}
              onChange={(v) => {
                onChange({ dataset: v });
              }}
            />
          </Form.Item>
          <Form.Item label="开始日期">
            <Input
              aria-label="开始日期"
              type="date"
              value={filter.start}
              onChange={(e) => onChange({ start: e.target.value })}
            />
          </Form.Item>
          <Form.Item label="结束日期">
            <Input
              aria-label="结束日期"
              type="date"
              value={filter.end}
              onChange={(e) => onChange({ end: e.target.value })}
            />
          </Form.Item>
        </div>
        <p className="muted">
          开始和结束日期包含首尾两天，筛选行情记录，不是下载时间。分钟数据按供应商时间的自然日期（上海时区），日线按供应商交易日期；周/月线按行情标签与计算截止日期的较早日期。夜盘尚未在此按交易日重新归集。
        </p>
        <Space wrap>
          {preset.length > 0 && (
            <Tag color="blue">正在查看指定的历史分片版本</Tag>
          )}
          <Button
            type="primary"
            htmlType="submit"
            loading={busy}
            disabled={!filter.scope}
          >
            查询数据
          </Button>
          <span className="muted">
            合约匹配 {contractTotal} 个，最多显示前 50 个；可输入代码缩小范围。
          </span>
        </Space>
      </Form>
    </Card>
  );
}
