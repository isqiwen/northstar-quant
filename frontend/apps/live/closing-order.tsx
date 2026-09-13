"use client";

import { useState } from "react";
import { Alert, Button, Card } from "antd";
import Link from "next/link";
import { query } from "./api/client";
import { useData } from "../../shared/data";
import { Failure, Fields, Identity } from "../../shared/ui";
import { Action } from "./runtime";

export function ClosingOrder({
  orderId,
  budgetId,
}: {
  orderId: string;
  budgetId: string;
}) {
  const budget = useData(query(`/api/broker/opening-budgets/${budgetId}`));
  const streamId = budget.data?.stream_id as string | undefined;
  const stream = useData(
    query(streamId ? `/api/streams/${streamId}` : null),
    3000,
  );
  const consents = useData(
    query(streamId ? `/api/streams/${streamId}/authorizations` : null),
    3000,
  );
  const [result, setResult] = useState<Record<string, unknown>>();
  const current = stream.data?.latest_query;
  const options = (consents.data?.authorizations ?? [])
    .filter((x) => x.status === "CONSENTED")
    .map((x) => ({ label: x.authorization_id, value: x.authorization_id }));
  return (
    <Card title="请求平仓">
      <Failure error={budget.error || stream.error || consents.error} />
      <Alert
        type="warning"
        showIcon
        title="向 SimNow 请求平掉本次一手今仓"
        description="由当前账本与新柜台查询核对持仓。主动暂停不阻止受限平仓；提交后仍以柜台成交回报为准。未知费用继续保留，不视为零。"
      />
      <Fields
        value={{ 开仓订单: orderId, 当前查询: current?.query_id, 平仓手数: 1 }}
      />
      {streamId && (
        <>
          <p>
            <Link href={`/streams/${streamId}`}>刷新柜台账户</Link> ·{" "}
            <Link href={`/streams/${streamId}/authorizations`}>
              设置执行限额
            </Link>
          </p>
          <Button
            onClick={() => {
              stream.refresh();
              consents.refresh();
            }}
          >
            刷新平仓依据
          </Button>
          <Action
            title="提交一手 SimNow 平今仓"
            path={`/api/streams/${streamId}/closing-orders`}
            fixed={{ opening_order_id: orderId, query_id: current?.query_id }}
            fields={[
              {
                name: "authorization_id",
                label: "使用的执行授权",
                kind: "select",
                options,
              },
              { name: "limit_price", label: "平仓限价" },
            ]}
            disabled={
              !!budget.error ||
              !!stream.error ||
              !!consents.error ||
              !options.length ||
              stream.data?.status !== "RECEIVING" ||
              current?.status !== "COMPLETE" ||
              !current?.query_id
            }
            onDone={setResult}
          />
        </>
      )}
      {result?.status === "REJECTED" && (
        <Alert
          type="warning"
          showIcon
          title="本次平仓请求已拒绝"
          description={String(result.reason)}
        />
      )}
      {typeof result?.order_id === "string" && result.status !== "REJECTED" && (
        <p>
          平仓订单：
          <Identity value={result.order_id} to={`/orders/${result.order_id}`} />
        </p>
      )}
    </Card>
  );
}
