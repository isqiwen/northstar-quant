"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { Alert, Button, Card, Space } from "antd";
import { query } from "./api/client";
import { useData } from "../../shared/data";
import {
  Evidence,
  Failure,
  Fields,
  Heading,
  Identity,
  Records,
  Status,
} from "../../shared/ui";

const kinds: Record<string, string> = {
  SEND_ATTEMPT: "发送尝试已保存",
  TRANSPORT_RETURNED: "发送调用返回",
  TRANSPORT_FAILED: "发送调用异常",
  CANCEL_ATTEMPT: "撤单尝试已保存",
  CANCEL_TRANSPORT_RETURNED: "撤单调用返回",
  CANCEL_TRANSPORT_FAILED: "撤单调用异常",
  CANCEL_REJECTED: "柜台拒绝撤单",
  CANCEL_NOT_NEEDED: "订单已终结，无需撤单",
  BROKER_REPORT: "柜台订单回报",
  FEE_CONFIRMED: "实际费用及覆盖已确认",
  FILL: "逐笔成交已入账",
};

export function Orders() {
  const [pages, setPages] = useState<(number | null)[]>([null]);
  const before = pages[pages.length - 1];
  const base = query("/api/orders");
  const q = useData(
    base && {
      ...base,
      path: base.path + (before === null ? "" : `?before=${before}`),
    },
  );
  return (
    <>
      <Heading
        title="订单与预占"
        description="所属 Live 实例保存的订单、剩余预算与执行事实。"
        actions={
          <Button
            onClick={() => {
              setPages([null]);
              q.refresh();
            }}
          >
            刷新最新订单
          </Button>
        }
      />
      <Failure error={q.error} />
      <Alert
        type="info"
        showIcon
        title="请求不等于确认成交"
        description="发送返回、撤单请求、断线和授权到期都不会释放未决预算。UNKNOWN 需要核对，不能盲目重发。成交已完成但费用尚未确认时，仍保留手续费预占，账户现金不可用于新增风险。当前尚未开放柜台报撤单入口。"
      />
      <Records
        title="本地订单"
        rowKey="order_id"
        loading={q.loading}
        rows={q.error ? [] : q.data?.orders}
        columns={[
          {
            title: "订单",
            dataIndex: "order_id",
            render: (v) => <Identity value={v} to={`/orders/${v}`} />,
          },
          {
            title: "合约",
            dataIndex: "contract_id",
            render: (v) => <Identity value={v} />,
          },
          {
            title: "状态",
            dataIndex: "status",
            render: (v) => <Status value={v} />,
          },
          { title: "委托手数", dataIndex: "quantity_lots" },
          { title: "已确认成交手数", dataIndex: "filled_lots" },
          { title: "费用待确认手数", dataIndex: "fee_pending_lots" },
          {
            title: "保证金预占",
            dataIndex: ["reservation", "reserved_margin"],
          },
          { title: "手续费预占", dataIndex: ["reservation", "reserved_fee"] },
          {
            title: "冻结平仓手数",
            dataIndex: ["reservation", "reserved_close_lots"],
          },
        ]}
      />
      <Space>
        <Button
          disabled={pages.length === 1 || q.loading}
          onClick={() => setPages(pages.slice(0, -1))}
        >
          上一批订单
        </Button>
        <Button
          disabled={!q.data?.next_before || q.loading || !!q.error}
          onClick={() => setPages([...pages, q.data!.next_before])}
        >
          下一批订单
        </Button>
      </Space>
    </>
  );
}

export function OrderDetail() {
  const { id } = useParams<{ id: string }>();
  const [pages, setPages] = useState([0]);
  const q = useData(
    query(`/api/orders/${id}?after=${pages[pages.length - 1]}`),
  );
  const record = q.error ? undefined : q.data?.record;
  return (
    <>
      <Heading
        title="订单事实"
        description="预算来自固定订单；成交数量来自逐笔事实，费用单独确认，不以累计回报代替。"
        actions={<Button onClick={q.refresh}>刷新观察</Button>}
      />
      <Failure error={q.error} />
      {record && (
        <>
          {record.requires_reconciliation && (
            <Alert
              type="warning"
              showIcon
              title="订单结果待核对"
              description="保留当前身份和剩余预算，不重复发送。恢复本地记录并不代表已完成柜台核对。"
            />
          )}
          <Card title="固定订单">
            <Fields
              value={{
                订单: record.order_id,
                状态: record.status,
                合约: record.contract_id,
                委托手数: record.quantity_lots,
                已确认成交手数: record.filled_lots,
                费用待确认手数: record.fee_pending_lots,
                授权引用: record.authorization_id,
                发送尝试: record.attempt_id,
                原运行身份: record.runtime_id,
              }}
            />
          </Card>
          <Card title="剩余预占">
            <Fields
              value={{
                保证金: record.reservation.reserved_margin,
                手续费: record.reservation.reserved_fee,
                新增总敞口: record.reservation.reserved_gross,
                滑点损失预算: record.reservation.reserved_loss,
                冻结平仓手数: record.reservation.reserved_close_lots,
              }}
            />
          </Card>
          <Records
            title="事件记录"
            rowKey="event_id"
            rows={q.data?.events}
            columns={[
              { title: "顺序", dataIndex: "sequence" },
              {
                title: "事件",
                dataIndex: "kind",
                render: (v) => kinds[String(v)] || String(v),
              },
              { title: "保存时间", dataIndex: "recorded_at" },
              {
                title: "事实身份",
                dataIndex: "event_id",
                render: (v) => <Identity value={v} />,
              },
            ]}
          />
          <Space>
            <Button
              disabled={pages.length === 1 || q.loading}
              onClick={() => setPages(pages.slice(0, -1))}
            >
              上一批事件
            </Button>
            <Button
              disabled={!q.data?.next_after || q.loading}
              onClick={() => setPages([...pages, q.data!.next_after!])}
            >
              下一批事件
            </Button>
          </Space>
          <Evidence value={record.order} title="原始订单请求与预算" />
          <Evidence value={q.data?.events} title="当前事件批次的完整事实" />
        </>
      )}
    </>
  );
}
