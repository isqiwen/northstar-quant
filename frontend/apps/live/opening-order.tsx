"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { Alert, Card } from "antd";
import Link from "next/link";
import { query } from "./api/client";
import { useData } from "../../shared/data";
import { Evidence, Failure, Fields, Heading, Identity } from "../../shared/ui";
import { Action } from "./runtime";

const reasons: Record<string, string> = {
  ACCOUNT_OBSERVATION_NOT_CURRENT: "资金查询已过期，请刷新账户并重新计算预算。",
  RECEIVER_QUERY_CHANGED: "账户查询已更新，请使用新查询重新计算预算。",
  MARKET_CHANGED_RECALCULATE_BUDGET: "行情已变化，请重新计算预算。",
  TARGET_SUPERSEDED: "策略已产生新的结果，请使用最新目标。",
  TARGET_PRECEDES_RESUME: "恢复运行前的目标已失效，请等待新的策略结果。",
  TARGET_OR_COMMAND_EXPIRED: "策略目标或操作已过期，请重新检查。",
  FIRST_OPENING_ACCOUNT_NOT_RECONCILED: "账户不满足已核对空仓条件。",
  "execution receiver is stopped or paused": "接收会话已暂停或停止。",
  "execution consent is not current": "执行授权已失效，请检查授权。",
};

export function OpeningOrder() {
  const { id } = useParams<{ id: string }>();
  const budget = useData(query(`/api/broker/opening-budgets/${id}`));
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
  const options = (consents.data?.authorizations ?? [])
    .filter((item) => item.status === "CONSENTED")
    .map((item) => ({
      label: item.authorization_id,
      value: item.authorization_id,
    }));
  return (
    <>
      <Heading
        title="固定开仓预算"
        description="预算保留原查询、账本和策略目标。实际委托前由接收内核重新检查。"
      />
      <Failure error={budget.error || stream.error || consents.error} />
      <Card title="本次委托">
        <Fields
          value={{
            会话: streamId,
            预算: id,
            方向: (budget.data?.budget as { side?: string } | undefined)?.side,
            手数: 1,
            限价: budget.data?.limit_price,
          }}
        />
        <Alert
          type="warning"
          showIcon
          title="提交会向 SimNow 发送一手限价开仓委托"
          description="仅允许已核对空仓账户的首次开仓。查询或行情过期、目标改变、存在未知事实时拒绝；提交后以柜台回报为准，不自动重发。"
        />
        {streamId && (
          <>
            <p>
              <Link href={`/streams/${streamId}/authorizations`}>
                查看或设置执行限额
              </Link>
            </p>
            <Action
              title="提交一手 SimNow 开仓"
              path={`/api/streams/${streamId}/opening-orders`}
              fixed={{ budget_id: id }}
              fields={[
                {
                  name: "authorization_id",
                  label: "使用的执行授权",
                  kind: "select",
                  options,
                },
              ]}
              disabled={
                !!budget.error ||
                !!stream.error ||
                !!consents.error ||
                !options.length ||
                budget.data?.status !== "WITHIN_BUDGET" ||
                (budget.data?.account_check as { status?: string } | undefined)
                  ?.status !== "UNCHANGED" ||
                stream.data?.status !== "RECEIVING" ||
                stream.data?.paused !== false
              }
              onDone={setResult}
            />
          </>
        )}
        {result?.status === "REJECTED" && (
          <Alert
            type="warning"
            showIcon
            title="本次开仓已拒绝"
            description={
              reasons[String(result.reason)] ?? String(result.reason)
            }
          />
        )}
        {typeof result?.order_id === "string" &&
          result.status !== "REJECTED" && (
            <p>
              订单：
              <Identity
                value={result.order_id}
                to={`/orders/${result.order_id}`}
              />
            </p>
          )}
      </Card>
      <Evidence title="固定预算与来源" value={budget.data} />
    </>
  );
}
