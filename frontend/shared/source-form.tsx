"use client";
import { Form, Input, Select } from "antd";
export const sourceFields: [string, string][] = [
  ["exchange", "交易所"],
  ["symbol", "合约代码"],
  ["product", "品种"],
  ["timezone", "时区"],
  ["currency", "币种"],
  ["quantity_unit", "数量单位"],
  ["price_tick", "最小价格变动"],
  ["multiplier", "合约乘数"],
  ["trading_day", "交易日"],
  ["session_open", "时段开始（UTC）"],
  ["session_close", "时段结束（UTC）"],
  ["source_name", "来源名称"],
  ["source_reference", "来源说明"],
  ["availability_note", "可得时间依据说明"],
];
export function SourceFields() {
  return (
    <>
      <div className="grid-three">
        {sourceFields.map(([key, label]) => (
          <Form.Item
            key={key}
            name={["spec", key]}
            label={label}
            rules={[{ required: true }]}
          >
            <Input
              placeholder={
                key === "trading_day"
                  ? "YYYY-MM-DD"
                  : key.startsWith("session_")
                    ? "YYYY-MM-DDTHH:mm:ssZ"
                    : undefined
              }
            />
          </Form.Item>
        ))}
      </div>
      <Form.Item
        name={["spec", "session_kind"]}
        label="交易时段"
        rules={[{ required: true }]}
      >
        <Select
          options={[
            { value: "DAY", label: "日盘" },
            { value: "NIGHT", label: "夜盘（显式指定所属交易日）" },
          ]}
        />
      </Form.Item>
      <Form.Item
        name={["spec", "availability_basis"]}
        label="可得时间依据"
        rules={[{ required: true }]}
      >
        <Select
          options={[
            "SYNTHETIC",
            "SOURCE_DECLARED",
            "FINAL_REVISED",
            "LOCAL_CAPTURE_RECONSTRUCTED",
          ].map((value) => ({ value, label: value }))}
        />
      </Form.Item>
    </>
  );
}
