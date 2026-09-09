"use client";

import { App, Button, Card, Form, Input } from "antd";
import { useRef, useState } from "react";
import { query, mutate } from "./api/client";
import { useData } from "../../shared/data";
import { Failure, Heading, Identity, Records, Status } from "../../shared/ui";

const fields = [
  ["symbol", "合约代码", "RB2610"],
  ["product", "品种", "RB"],
  ["quantity_unit", "数量单位", "TON"],
  ["price_tick", "最小价格变动", "1"],
  ["multiplier", "合约乘数", "10"],
  ["trading_day", "交易日", "YYYY-MM-DD"],
  ["session_open", "时段开始（UTC）", "2026-09-08T05:30:00Z"],
  ["session_close", "时段结束（UTC）", "2026-09-08T07:00:00Z"],
];

export function TushareSync() {
  const { message } = App.useApp();
  const recent = useData(query("/api/sync"), 5000);
  const [busy, setBusy] = useState(false);
  const request = useRef<string | null>(null);
  return (
    <>
      <Heading
        title="Tushare 历史同步"
        description="保存研究数据，校验后发布固定版本。Data Hub 不订阅或录制实时行情。"
      />
      <Card title="提交历史区间">
        <p>
          当前接通 SHFE
          真实合约的一分钟数据，限定已过去日期、最长两小时的连续日盘时段。15
          分钟、日线及跨休市范围尚未接入当前加工流程。
        </p>
        <p>
          请核对合约单位和条款；按行情时间表示分钟结束解释数据，首次使用须以供应商样本核对。下载时间不代表历史首次可得时间。
        </p>
        <Form
          layout="vertical"
          onFinish={async (spec) => {
            setBusy(true);
            request.current ??= crypto.randomUUID();
            try {
              await mutate("/api/sync/tushare", {
                request_id: request.current,
                spec: {
                  ...spec,
                  exchange: "SHFE",
                  timezone: "Asia/Shanghai",
                  currency: "CNY",
                  source_name: "TUSHARE",
                  source_reference: "Tushare historical subscription",
                  availability_basis: "FINAL_REVISED",
                  availability_note:
                    "Bar-end interpretation; verify an entitled sample.",
                },
              });
              request.current = null;
              recent.refresh();
              message.success("同步任务已保存，由独立 worker 执行");
            } catch (e) {
              message.error((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="grid-three">
            {fields.map(([name, label, placeholder]) => (
              <Form.Item
                key={name}
                name={name}
                label={label}
                rules={[{ required: true }]}
              >
                <Input placeholder={placeholder} />
              </Form.Item>
            ))}
          </div>
          <p>
            需已开通历史分钟权限并确认本地研究留存；凭据由服务端配置，此页面不接收
            token。首次下载、指定区间补数使用相同入口，目前不自动安排每日任务。
          </p>
          <Button type="primary" htmlType="submit" loading={busy}>
            提交历史同步
          </Button>
        </Form>
      </Card>
      <Failure error={recent.error} />
      <Records
        title="最近同步任务"
        rows={recent.error ? undefined : recent.data}
        loading={recent.loading}
        rowKey="request_id"
        columns={[
          {
            title: "请求",
            dataIndex: "request_id",
            render: (v: string) => <Identity value={v} />,
          },
          {
            title: "同步状态",
            dataIndex: "status",
            render: (v: string) => <Status value={v} />,
          },
          {
            title: "合约与区间（UTC）",
            dataIndex: "parameters",
            render: (v: Record<string, unknown>) => (
              <>
                <div>
                  {String(v.symbol)} · {String(v.trading_day)}
                </div>
                <div>
                  {String(v.session_open).slice(11, 16)}–
                  {String(v.session_close).slice(11, 16)}
                </div>
              </>
            ),
          },
          {
            title: "加工任务",
            dataIndex: "attempt_id",
            render: (v: string | null) =>
              v ? <Identity value={v} to={`/attempts/${v}`} /> : "—",
          },
          { title: "失败原因", dataIndex: "error" },
        ]}
      />
      <p>
        RECEIVED
        表示原文已接收，是否发布请查看加工任务。失败后检查原因，再明确提交新任务；不会自动重试或覆盖旧结果。
      </p>
    </>
  );
}
