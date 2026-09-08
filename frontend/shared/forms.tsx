"use client";
import { Form, Input, InputNumber, Select } from "antd";
const labels: Record<string, string> = {
  max_lots: "最大手数",
  max_gross_notional: "最大名义金额",
  max_margin_fraction: "保证金比例上限",
  initial_margin_fraction: "初始保证金比例",
  max_adverse_price_move_fraction: "不利价格变动上限",
  initial_cash: "模拟初始资金",
  fee_per_lot: "每手费用",
  slippage_ticks: "滑点跳数",
};
export function Parameters({
  definitions,
  prefix = [],
}: {
  definitions: {
    name: string;
    label: string;
    default: string | number;
    unit?: string;
    minimum?: string | number;
    maximum?: string | number;
  }[];
  prefix?: string[];
}) {
  return (
    <div className="grid-three">
      {definitions.map((p) => (
        <Form.Item
          key={p.name}
          name={[...prefix, p.name]}
          label={p.label + (p.unit ? ` · ${p.unit}` : "")}
          initialValue={p.default}
          rules={[{ required: true }]}
          tooltip={
            p.minimum !== undefined
              ? `允许范围：${p.minimum} 至 ${p.maximum}`
              : undefined
          }
        >
          {typeof p.default === "number" ? (
            <InputNumber style={{ width: "100%" }} precision={0} />
          ) : (
            <Input />
          )}
        </Form.Item>
      ))}
    </div>
  );
}
export function ConfigFields({
  defaults,
  prefix,
}: {
  defaults: Record<string, string | number>;
  prefix: string;
}) {
  return (
    <Parameters
      prefix={[prefix]}
      definitions={Object.entries(defaults).map(([name, value]) => ({
        name,
        label: labels[name] || name,
        default: value,
      }))}
    />
  );
}
export function SnapshotSelect({
  rows,
  name = "snapshot_id",
  label = "固定数据快照",
}: {
  rows: { snapshot_id: string; symbol: string; trading_day: string }[];
  name?: string;
  label?: string;
}) {
  return (
    <Form.Item name={name} label={label} rules={[{ required: true }]}>
      <Select
        showSearch
        optionFilterProp="label"
        placeholder="选择已发布快照"
        options={rows.map((r) => ({
          value: r.snapshot_id,
          label: `${r.symbol} · ${r.trading_day} · ${String(r.snapshot_id).slice(0, 8)}`,
        }))}
      />
    </Form.Item>
  );
}
export function ConfigurationSelect({
  rows,
}: {
  rows: { configuration_id: string; name: string }[];
}) {
  return (
    <Form.Item
      name="configuration_id"
      label="固定策略配置"
      rules={[{ required: true }]}
    >
      <Select
        showSearch
        optionFilterProp="label"
        placeholder="选择已登记配置"
        options={rows.map((r) => ({
          value: r.configuration_id,
          label: `${r.name} · ${r.configuration_id.slice(0, 8)}`,
        }))}
      />
    </Form.Item>
  );
}
