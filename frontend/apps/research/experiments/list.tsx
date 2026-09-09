"use client";
import { useState } from "react";
import Link from "next/link";
import { App, Button, Card, Form, Input, Select, Table, Tabs } from "antd";
import { query, mutate, type CommandResponse } from "../api/client";
import { useData } from "../../../shared/data";
import { Heading, Failure, Identity } from "../../../shared/ui";
import { TaskTable } from "./tasks";
function parameters(value: unknown, path = ""): Record<string, string> {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return Object.assign(
      {},
      ...Object.entries(value).map(([key, item]) =>
        parameters(item, path ? `${path}.${key}` : key),
      ),
    );
  }
  return { [path]: JSON.stringify(value) ?? "—" };
}
export default function Experiments() {
  const tasks = useData(query("/api/tasks"), 2000),
    runs = useData(query("/api/runs"), 5000);
  const [search, setSearch] = useState("");
  const [comparison, setComparison] =
    useState<CommandResponse<"/api/run-comparisons">>();
  const { message } = App.useApp();
  const rows = (runs.data || []).filter((r) =>
    JSON.stringify([r.run_id, r.config.strategy, r.snapshot])
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const selected = (comparison || []).map((c) =>
    runs.data?.find((r) => r.run_id === c.run_id),
  );
  const left = parameters(selected[0]?.config.strategy),
    right = parameters(selected[1]?.config.strategy);
  const differences = [
    ...new Set([...Object.keys(left), ...Object.keys(right)]),
  ]
    .sort()
    .map((key) => ({ key, left: left[key] ?? "—", right: right[key] ?? "—" }));
  return (
    <>
      <Heading
        title="实验与回测"
        description="任务保留失败与中断证据；结果比较要求相同数据、代码、风险与成本假设。"
        actions={
          <Link href="/experiments/new">
            <Button type="primary">新建回测</Button>
          </Link>
        }
      />
      <Failure error={tasks.error || runs.error} />
      <Tabs
        items={[
          {
            key: "tasks",
            label: "运行任务",
            children: <TaskTable rows={tasks.data || []} />,
          },
          {
            key: "results",
            label: "固定结果",
            children: (
              <Card>
                <Input.Search
                  placeholder="搜索策略、快照或结果身份"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                <Table
                  rowKey="run_id"
                  dataSource={rows}
                  columns={[
                    {
                      title: "结果",
                      dataIndex: "run_id",
                      render: (v: string) => (
                        <Identity value={v} to={`/runs/${v}`} />
                      ),
                    },
                    {
                      title: "策略",
                      render: (_, r) => r.config.strategy.strategy_id,
                    },
                    {
                      title: "快照",
                      render: (_, r) => <Identity value={r.snapshot.id} />,
                    },
                    {
                      title: "期末权益",
                      render: (_, r) => r.summary.ending_equity,
                    },
                    {
                      title: "收益率",
                      render: (_, r) => r.summary.total_return,
                    },
                    {
                      title: "最大回撤",
                      render: (_, r) => r.summary.max_drawdown,
                    },
                    {
                      title: "累计费用",
                      render: (_, r) => r.summary.total_fees,
                    },
                  ]}
                />
              </Card>
            ),
          },
        ]}
      />
      <Card title="比较固定结果">
        <Form
          layout="vertical"
          onFinish={async (v) => {
            setComparison(undefined);
            try {
              setComparison(
                await mutate("/api/run-comparisons", { run_ids: v.run_ids }),
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
            <Select
              mode="multiple"
              options={(runs.data || []).map((r) => ({
                value: r.run_id,
                label: `${r.config.strategy.strategy_id} · ${r.run_id.slice(0, 12)}`,
              }))}
            />
          </Form.Item>
          <Button htmlType="submit">比较固定结果</Button>
        </Form>
        {comparison && (
          <Table
            rowKey="run_id"
            pagination={false}
            dataSource={comparison}
            columns={[
              { title: "策略", dataIndex: "strategy" },
              { title: "期末权益", dataIndex: "ending_equity" },
              { title: "收益率", dataIndex: "total_return" },
              { title: "最大回撤", dataIndex: "max_drawdown" },
              { title: "费用", dataIndex: "total_fees" },
              {
                title: "报告",
                dataIndex: "run_id",
                render: (v: string) => (
                  <Link href={`/runs/${v}`}>查看报告</Link>
                ),
              },
            ]}
          />
        )}
        {comparison && (
          <Table
            rowKey="key"
            pagination={false}
            dataSource={differences}
            columns={[
              { title: "策略参数与因子绑定", dataIndex: "key" },
              { title: comparison[0].strategy, dataIndex: "left" },
              { title: comparison[1].strategy, dataIndex: "right" },
              {
                title: "变化",
                render: (_, r) => (r.left === r.right ? "相同" : "不同"),
              },
            ]}
          />
        )}
      </Card>
    </>
  );
}
