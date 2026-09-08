"use client";
import { query, mutate, type CommandResponse } from "./api/client";
import { useState } from "react";
import { App, Button, Card, Form, Input, Select, Spin, Tabs } from "antd";
import Link from "next/link";
import { useRouter, useParams } from "next/navigation";
import { useData } from "../../shared/data";
import { ConfigurationSelect, SnapshotSelect } from "../../shared/forms";
import {
  Evidence,
  Failure,
  Fields,
  Heading,
  Identity,
  Records,
  Status,
} from "../../shared/ui";
import { Line } from "../../shared/chart";
export function ResearchHome() {
  const ds = useData(query("/api/datasets"));
  const cfg = useData(query("/api/configurations"));
  const runs = useData(query("/api/runs"));
  const attempts = useData(query("/api/research-attempts"), 5000);
  const navigate = useRouter().push;
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  const [comparison, setComparison] =
    useState<CommandResponse<"/api/run-comparisons">>();
  const [form] = Form.useForm();
  const selectedConfig = Form.useWatch("configuration_id", form);
  const options = (runs.data || []).map((r) => ({
    value: r.run_id,
    label: r.run_id.slice(0, 12),
  }));
  return (
    <>
      <Heading
        title="研究工作台"
        description="从固定输入到可复核结果，研究与生产执行保持独立。"
        actions={
          <Link href="/configurations/new">
            <Button>新建策略配置</Button>
          </Link>
        }
      />
      <Failure error={ds.error || cfg.error || runs.error} />
      <Card>
        <Fields
          value={{
            可用快照: ds.data?.length ?? "—",
            固定配置: cfg.data?.length ?? "—",
            研究结果: runs.data?.length ?? "—",
          }}
        />
      </Card>
      <div className="grid-two">
        <Card title="运行固定研究">
          <Form
            name="run"
            layout="vertical"
            onFinish={async (v) => {
              const selected = cfg.data?.find(
                (c) => c.configuration_id === v.configuration_id,
              );
              if (!selected) return;
              setBusy(true);
              try {
                const result = await mutate("/api/runs", {
                  snapshot_id: v.snapshot_id,
                  config: selected.config,
                });
                navigate(`/runs/${result.run_id}`);
              } catch (e) {
                message.error((e as Error).message);
              } finally {
                setBusy(false);
                attempts.refresh();
              }
            }}
          >
            <SnapshotSelect rows={ds.data || []} />
            <ConfigurationSelect rows={cfg.data || []} />
            <Button loading={busy} type="primary" htmlType="submit">
              运行固定研究
            </Button>
          </Form>
          <p className="muted">当前为有界同步计算，尝试在运行前保存。</p>
        </Card>
        <Card title="比较固定结果">
          <Form
            name="compare"
            layout="vertical"
            onFinish={async (v) => {
              try {
                setComparison(
                  await mutate("/api/run-comparisons", {
                    run_ids: v.run_ids,
                  }),
                );
              } catch (e) {
                message.error((e as Error).message);
              }
            }}
          >
            <Form.Item
              name="run_ids"
              label="选择两个研究结果"
              rules={[{ required: true, type: "array", len: 2 }]}
            >
              <Select mode="multiple" options={options} />
            </Form.Item>
            <Button htmlType="submit">比较固定结果</Button>
          </Form>
          <p className="muted">要求相同快照、代码版本、风险与成本假设。</p>
        </Card>
      </div>
      {comparison && (
        <Records
          title="结果比较"
          rows={comparison}
          rowKey="run_id"
          columns={[
            { title: "策略", dataIndex: "strategy" },
            { title: "期末权益", dataIndex: "ending_equity" },
            { title: "累计费用", dataIndex: "total_fees" },
            { title: "最大回撤", dataIndex: "max_drawdown" },
            { title: "收益率", dataIndex: "total_return" },
          ]}
        />
      )}
      <Records
        title="研究结果"
        rows={runs.data}
        loading={runs.loading}
        rowKey="run_id"
        columns={[
          {
            title: "结果",
            dataIndex: "run_id",
            render: (v) => <Identity value={v} to={`/runs/${v}`} />,
          },
          { title: "创建时间", dataIndex: "created_at" },
          {
            title: "代码版本",
            dataIndex: "code_revision",
            render: (v) => <Identity value={v} />,
          },
        ]}
      />
      <Card title="登记固定策略版本">
        <Form
          name="version"
          form={form}
          layout="vertical"
          onFinish={async (v) => {
            try {
              const result = await mutate("/api/strategy-versions", v);
              navigate(`/strategy-versions/${result.version_id}`);
            } catch (e) {
              message.error((e as Error).message);
            }
          }}
        >
          <div className="grid-two">
            <Form.Item
              name="name"
              label="策略版本名称"
              rules={[{ required: true }]}
            >
              <Input />
            </Form.Item>
            <ConfigurationSelect rows={cfg.data || []} />
          </div>
          <Form.Item
            name="run_ids"
            label="引用此配置的研究结果"
            rules={[{ required: true }]}
          >
            <Select mode="multiple" options={options} />
          </Form.Item>
          <Button disabled={!selectedConfig} htmlType="submit">
            登记固定版本
          </Button>
        </Form>
      </Card>
      <Records
        title="执行尝试"
        rows={attempts.data}
        rowKey="attempt_id"
        columns={[
          {
            title: "尝试",
            dataIndex: "attempt_id",
            render: (v) => <Identity value={v} />,
          },
          {
            title: "状态",
            dataIndex: "status",
            render: (v) => <Status value={v} />,
          },
          { title: "创建时间", dataIndex: "created_at" },
          { title: "说明", dataIndex: "error" },
        ]}
      />
    </>
  );
}
export function Report() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/runs/${id}`));
  const result = q.data?.result;
  const curve = result?.equity_curve || [];
  return (
    <>
      <Heading
        title="研究报告"
        description="历史研究证据，不能据此推断实盘盈利或执行许可。"
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
            <Tabs
              items={[
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
                        { title: "价格", dataIndex: "price" },
                        { title: "数量", dataIndex: "quantity_lots" },
                        { title: "费用", dataIndex: "fee" },
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
