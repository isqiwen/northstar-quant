"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Card,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Space,
} from "antd";
import { query, mutate } from "./api/client";
import { useData } from "../../shared/data";
import { requestId } from "../../shared/api";
import {
  Evidence,
  Failure,
  Fields,
  Heading,
  Identity,
  Records,
} from "../../shared/ui";
import { Action, useRuntime } from "./runtime";

type Limits = {
  expires_at: { toISOString(): string };
  max_order_lots: number;
  max_total_lots: number;
  fee: string;
  margin: string;
  gross: string;
  loss: string;
};
const states: Record<string, string> = {
  CONSENTED: "已确认限额，仍需就绪检查",
  REVOKED: "已撤销",
  PREVIOUS_RUNTIME: "旧运行实例，已失效",
  EXPIRED: "已到期",
};

export function Authorizations() {
  const { id } = useParams<{ id: string }>();
  const runtime = useRuntime();
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string>();
  const [pages, setPages] = useState<(number | null)[]>([null]);
  const before = pages[pages.length - 1];
  const base = query(`/api/streams/${id}/authorizations`);
  const history = useData(
    base && {
      ...base,
      path: base.path + (before === null ? "" : `?before=${before}`),
    },
    3000,
  );
  const stream = useData(query(`/api/streams/${id}`), 3000);
  const detail = useData(
    query(selected ? `/api/authorizations/${selected}` : null),
    3000,
  );
  const refresh = () => {
    setPages([null]);
    history.refresh();
    detail.refresh();
  };
  return (
    <>
      <Heading
        title="执行限额与授权"
        description="仅适用于当前账号、会话和固定策略。重启后不会自动恢复授权。"
      />
      <Failure error={history.error || stream.error || detail.error} />
      <Alert
        type="info"
        showIcon
        title="限额同意与下单就绪分开检查"
        description="保存限额不会直接下单。执行前还必须核对当前账户、行情、风险和未决订单；当前管理端尚未开放柜台报撤单。撤销授权也不代表既有订单已经撤销。"
      />
      <Card title="本次授权范围">
        <Fields
          value={{
            接收会话: id,
            当前运行实例: runtime.id,
            账号: stream.data?.binding.account_id,
            合约: stream.data?.binding.instrument,
            环境: stream.data?.binding.environment,
            固定配置: stream.data?.binding.request.configuration_id,
          }}
        />
      </Card>
      <Card title="确认新的执行限额">
        <Form<Limits>
          layout="vertical"
          onFinish={async (values) => {
            if (!runtime.canControl || !runtime.id) return;
            setBusy(true);
            try {
              const result = await mutate(
                `/api/streams/${id}/authorizations`,
                {
                  ...values,
                  expires_at: values.expires_at.toISOString(),
                  request_id: requestId(),
                },
                runtime.id,
              );
              setSelected(result.authorization_id);
              refresh();
              message.success("执行限额已保存，仍需当前就绪检查");
            } catch (e) {
              message.error((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="grid-two">
            <Form.Item
              name="expires_at"
              label="授权截止时间（本地时间，不超过本次接收结束）"
              rules={[{ required: true }]}
            >
              <DatePicker showTime style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item
              name="max_order_lots"
              label="每张订单最多手数"
              rules={[{ required: true }]}
            >
              <InputNumber precision={0} min={1} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item
              name="max_total_lots"
              label="本次授权累计委托手数上限（拒单不返还额度）"
              rules={[{ required: true }]}
            >
              <InputNumber precision={0} min={1} style={{ width: "100%" }} />
            </Form.Item>
            {(
              [
                ["fee", "单笔费用预算上限（元）"],
                ["margin", "单笔保证金预算上限（元）"],
                ["gross", "单笔名义金额上限（元）"],
                ["loss", "单笔损失预算上限（元）"],
              ] as const
            ).map(([name, label]) => (
              <Form.Item
                key={name}
                name={name}
                label={label}
                rules={[{ required: true }]}
              >
                <Input inputMode="decimal" />
              </Form.Item>
            ))}
          </div>
          <Button
            type="primary"
            htmlType="submit"
            loading={busy}
            disabled={
              !runtime.canControl ||
              stream.data?.status !== "RECEIVING" ||
              !!stream.error
            }
          >
            确认执行限额
          </Button>
        </Form>
      </Card>
      <Records
        title="固定授权记录"
        rowKey="authorization_id"
        rows={history.error ? [] : history.data?.authorizations}
        columns={[
          {
            title: "授权",
            dataIndex: "authorization_id",
            render: (v) => (
              <Button type="link" onClick={() => setSelected(String(v))}>
                <Identity value={v} />
              </Button>
            ),
          },
          {
            title: "状态",
            dataIndex: "status",
            render: (v) => states[String(v)] || String(v),
          },
          {
            title: "运行实例",
            dataIndex: "runtime_id",
            render: (v) => <Identity value={v} />,
          },
        ]}
      />
      <Space>
        <Button
          disabled={pages.length === 1}
          onClick={() => setPages((p) => p.slice(0, -1))}
        >
          上一页
        </Button>
        <Button
          disabled={!history.data?.next_before}
          onClick={() => {
            if (history.data?.next_before)
              setPages((p) => [...p, history.data!.next_before]);
          }}
        >
          下一页
        </Button>
        <Button onClick={refresh}>刷新最新记录</Button>
      </Space>
      {selected && detail.data && (
        <>
          <Card title="选中授权">
            <Fields
              value={{ 状态: states[detail.data.status] || detail.data.status }}
            />
            <Evidence value={detail.data} />
          </Card>
          <Action
            title="撤销该授权"
            path={`/api/authorizations/${selected}/revoke`}
            disabled={detail.data.status === "REVOKED"}
            onDone={refresh}
          />
        </>
      )}
    </>
  );
}
