"use client";
import { query } from "./api/client";
import { Card, Spin, Tabs } from "antd";
import { useParams } from "next/navigation";
import { useData } from "../../shared/data";
import {
  Evidence,
  Failure,
  Fields,
  Heading,
  Identity,
  Records,
} from "../../shared/ui";
import { Line } from "../../shared/chart";
export function Report() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/runs/${id}`));
  const result = q.data?.result;
  const curve = result?.equity_curve || [];
  return (
    <>
      <Heading
        title="研究报告"
        description="固定输入历史模拟 · 金额明细保留精确值 · 不代表柜台成交或执行许可。"
      />
      <Failure error={q.error} />
      {q.loading ? (
        <Spin />
      ) : (
        result && (
          <>
            <Card>
              <Fields
                value={{
                  合约: result.data?.symbol,
                  期末权益: result.summary.ending_equity,
                  总收益率: result.summary.total_return,
                  累计费用: result.summary.total_fees,
                  最大回撤: result.summary.max_drawdown,
                  成交次数: result.summary.fill_count,
                }}
              />
            </Card>
            <Card title="权益轨迹">
              <Line
                name="账户权益"
                labels={curve.map((r) => r.at)}
                values={curve.map((r) => Number(r.equity))}
              />
            </Card>
            <Card title="回撤轨迹">
              <Line
                name="账户回撤"
                labels={curve.map((r) => r.at)}
                values={curve.map((r) =>
                  r.drawdown == null ? null : Number(r.drawdown),
                )}
              />
            </Card>
            <Tabs
              items={[
                {
                  key: "ledger",
                  label: "资金与持仓",
                  children: (
                    <Records
                      rowKey="observation_id"
                      rows={curve}
                      columns={[
                        { title: "时间", dataIndex: "at" },
                        { title: "权益", dataIndex: "equity" },
                        { title: "资金", dataIndex: "cash" },
                        { title: "持仓手数", dataIndex: "position_lots" },
                        { title: "已实现盈亏", dataIndex: "realized_pnl" },
                        { title: "浮动盈亏", dataIndex: "unrealized_pnl" },
                        { title: "累计费用", dataIndex: "total_fees" },
                        { title: "回撤", dataIndex: "drawdown" },
                      ]}
                    />
                  ),
                },
                {
                  key: "decisions",
                  label: "策略与风险决定",
                  children: (
                    <Records
                      rowKey="observation_id"
                      rows={result.decisions}
                      columns={[
                        {
                          title: "观察",
                          dataIndex: "observation_id",
                          render: (v) => <Identity value={v} />,
                        },
                        { title: "决定", dataIndex: "decision_kind" },
                        { title: "目标", dataIndex: "target_fraction" },
                        { title: "风险", dataIndex: "reason" },
                      ]}
                    />
                  ),
                },
                {
                  key: "fills",
                  label: "模拟成交",
                  children: (
                    <Records
                      rowKey="fill_id"
                      rows={result.fills}
                      columns={[
                        {
                          title: "成交",
                          dataIndex: "fill_id",
                          render: (v) => <Identity value={v} />,
                        },
                        { title: "方向", dataIndex: "side" },
                        { title: "开平", dataIndex: "offset" },
                        { title: "价格", dataIndex: "price" },
                        { title: "数量", dataIndex: "quantity_lots" },
                        { title: "费用", dataIndex: "fee" },
                      ]}
                    />
                  ),
                },
                {
                  key: "orders",
                  label: "订单过程",
                  children: (
                    <Records
                      rowKey="update_key"
                      rows={result.orders.map((row, index) => ({
                        ...row,
                        update_key: String(index),
                      }))}
                      columns={[
                        { title: "时间", dataIndex: "at" },
                        {
                          title: "订单",
                          dataIndex: "order_id",
                          render: (v) => <Identity value={v} />,
                        },
                        { title: "状态", dataIndex: "status" },
                        { title: "原因", dataIndex: "reason" },
                        { title: "委托手数", dataIndex: "quantity_lots" },
                        { title: "已成交", dataIndex: "filled_lots" },
                        { title: "剩余", dataIndex: "remaining_lots" },
                      ]}
                    />
                  ),
                },
                {
                  key: "settlements",
                  label: "跨日结算",
                  children: (
                    <Records
                      rowKey="settlement_id"
                      rows={result.settlements}
                      columns={[
                        { title: "交易日", dataIndex: "trading_day" },
                        { title: "下一交易日", dataIndex: "next_trading_day" },
                        { title: "结算价", dataIndex: "price" },
                        { title: "盯市盈亏", dataIndex: "variation_pnl" },
                        { title: "结算后资金", dataIndex: "cash" },
                        { title: "可得时间", dataIndex: "available_at" },
                        { title: "依据", dataIndex: "source_reference" },
                      ]}
                    />
                  ),
                },
                {
                  key: "evidence",
                  label: "输入与证据",
                  children: <Evidence value={q.data} />,
                },
              ]}
            />
          </>
        )
      )}
    </>
  );
}
