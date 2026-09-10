"use client";
import { query } from "./api/client";
import { Alert, Card, Descriptions, Spin, Tabs } from "antd";
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
                        { title: "净持仓手数", dataIndex: "position_lots" },
                        { title: "平仓盈亏", dataIndex: "trade_realized_pnl" },
                        { title: "结算盈亏", dataIndex: "settlement_pnl" },
                        { title: "累计实现盈亏", dataIndex: "realized_pnl" },
                        { title: "浮动盈亏", dataIndex: "unrealized_pnl" },
                        { title: "累计费用", dataIndex: "total_fees" },
                        { title: "回撤", dataIndex: "drawdown" },
                      ]}
                    />
                  ),
                },
                {
                  key: "evaluation",
                  label: "评价条件",
                  children: (
                    <>
                      <Alert
                        type="info"
                        showIcon
                        title="探索性研究：重复选参或反复使用此区间，不构成未使用的样本外验证。"
                      />
                      <Descriptions
                        bordered
                        size="small"
                        column={{ xs: 1, sm: 1, md: 2 }}
                        items={[
                          {
                            label: "评价身份",
                            children: (
                              <Identity
                                value={result.evaluation.plan.plan_id}
                              />
                            ),
                          },
                          { label: "输入窗口", children: "整个固定快照" },
                          {
                            label: "行情开始（UTC）",
                            children: result.evaluation.plan.event_start || "—",
                          },
                          {
                            label: "行情结束（UTC）",
                            children: result.evaluation.plan.event_end || "—",
                          },
                          {
                            label: "计划记录数",
                            children:
                              result.evaluation.plan.expected_bars ?? "—",
                          },
                          {
                            label: "已观察记录数",
                            children: result.evaluation.observed_bars,
                          },
                          {
                            label: "窗口状态",
                            children:
                              result.evaluation.status === "COMPLETE_WINDOW"
                                ? "已完成固定输入窗口"
                                : result.evaluation.status ===
                                    "INCOMPLETE_WINDOW"
                                  ? "输入窗口尚未完成"
                                  : "未验证来源",
                          },
                          {
                            label: "比较基准",
                            children: "不交易、无利息的初始现金",
                          },
                          {
                            label: "基准期末权益",
                            children: result.evaluation.benchmark_ending_equity,
                          },
                          {
                            label: "相对基准收益率",
                            children: result.evaluation.excess_return,
                          },
                          {
                            label: "年化与夏普",
                            children: "未计算；不从盘中样本推断年化表现",
                            span: 2,
                          },
                        ]}
                      />
                    </>
                  ),
                },
                {
                  key: "exposure",
                  label: "敞口与保证金",
                  children: (
                    <>
                      <Alert
                        type="info"
                        showIcon
                        title="保证金按多空双边持仓计算。未成交订单另行预占手续费、开仓保证金或平仓手数；预占是预算，不是已发生费用或柜台冻结事实。"
                      />
                      <Records
                        rowKey="observation_id"
                        rows={curve}
                        columns={[
                          { title: "时间", dataIndex: "at" },
                          { title: "多头手数", dataIndex: "long_lots" },
                          { title: "空头手数", dataIndex: "short_lots" },
                          { title: "净名义敞口", dataIndex: "net_exposure" },
                          { title: "总名义敞口", dataIndex: "gross_exposure" },
                          { title: "条款保证金", dataIndex: "margin_used" },
                          { title: "扣除保证金后资金", dataIndex: "available" },
                          { title: "手续费预占", dataIndex: "reserved_fee" },
                          {
                            title: "开仓保证金预占",
                            dataIndex: "reserved_margin",
                          },
                          {
                            title: "平仓预占手数",
                            dataIndex: "reserved_close_lots",
                          },
                          {
                            title: "扣除全部预占后资金",
                            dataIndex: "available_after_reservations",
                          },
                        ]}
                      />
                    </>
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
                  key: "terms",
                  label: "费用与保证金条款",
                  children: (
                    <>
                      {!result.data?.terms.length && (
                        <Alert
                          type="info"
                          showIcon
                          title="该研究使用配置中的模拟费用和保证金假设，未绑定历史条款。"
                        />
                      )}
                      <Records
                        rowKey="terms_id"
                        rows={result.data?.terms || []}
                        columns={[
                          { title: "版本", dataIndex: "terms_id" },
                          { title: "生效", dataIndex: "effective_from" },
                          { title: "失效", dataIndex: "effective_until" },
                          { title: "可得时间", dataIndex: "available_at" },
                          { title: "下限", dataIndex: "lower_limit" },
                          { title: "上限", dataIndex: "upper_limit" },
                          ...(
                            [
                              ["开仓费用", "open_fee"],
                              ["平今费用", "close_today_fee"],
                              ["平昨费用", "close_yesterday_fee"],
                              ["多头保证金", "long_margin"],
                              ["空头保证金", "short_margin"],
                            ] as const
                          ).map(([title, dataIndex]) => ({
                            title,
                            dataIndex,
                            render: (value: {
                              by_money: string;
                              by_volume: string;
                            }) =>
                              `金额 × ${value.by_money} + 手数 × ${value.by_volume}`,
                          })),
                          { title: "金额精度", dataIndex: "money_quantum" },
                          { title: "费用舍入", dataIndex: "fee_rounding" },
                          { title: "依据", dataIndex: "source_reference" },
                        ]}
                      />
                    </>
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
