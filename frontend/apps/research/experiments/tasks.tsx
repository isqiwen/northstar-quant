"use client";
import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { App, Button, Card, Form, Progress, Space, Table, Tag } from "antd";
import { query, mutate, type GetResponse } from "../api/client";
import { useData } from "../../../shared/data";
import { SnapshotSelect, ConfigurationSelect } from "../../../shared/forms";
import {
  Heading,
  Failure,
  Evidence,
  Fields,
  Identity,
} from "../../../shared/ui";
const states: Record<string, string> = {
  QUEUED: "排队中",
  RUNNING: "运行中",
  FINALIZING: "保存结果",
  CANCEL_REQUESTED: "取消请求中",
  CANCELLED: "已取消",
  FAILED: "失败",
  INTERRUPTED: "已中断",
  SUCCEEDED: "完成",
};
type Task = GetResponse<`/api/tasks/${string}`>;
export function TaskTable({ rows }: { rows: Task[] }) {
  return (
    <Table
      rowKey="task_id"
      dataSource={rows}
      pagination={{ pageSize: 10 }}
      columns={[
        {
          title: "任务",
          dataIndex: "task_id",
          render: (v: string) => <Identity value={v} to={`/tasks/${v}`} />,
        },
        {
          title: "状态",
          dataIndex: "status",
          filters: Object.entries(states).map(([value, text]) => ({
            value,
            text,
          })),
          onFilter: (v, r) => r.status === v,
          render: (v: string) => <Tag>{states[v] || v}</Tag>,
        },
        {
          title: "进度",
          render: (_, r) => (
            <span>
              {r.completed} / {r.total}
            </span>
          ),
        },
        { title: "提交时间", dataIndex: "created_at" },
        { title: "说明", dataIndex: "reason" },
        {
          title: "结果",
          render: (_, r) =>
            r.run_id ? <Link href={`/runs/${r.run_id}`}>查看报告</Link> : "—",
        },
      ]}
    />
  );
}
export function NewExperiment() {
  const ds = useData(query("/api/datasets")),
    cfg = useData(query("/api/configurations"));
  const [busy, setBusy] = useState(false);
  const [identity, setIdentity] = useState<string>();
  const navigate = useRouter().push;
  const { message } = App.useApp();
  return (
    <>
      <Heading
        title="新建回测"
        description="固定数据快照、策略、风险和成本配置后提交；独立 worker 执行。"
        actions={
          <Link href="/configurations/new">
            <Button>新建策略配置</Button>
          </Link>
        }
      />
      <Failure error={ds.error || cfg.error} />
      <Card title="运行固定研究">
        <Form
          layout="vertical"
          onValuesChange={() => setIdentity(undefined)}
          onFinish={async (v) => {
            const selected = cfg.data?.find(
              (c) => c.configuration_id === v.configuration_id,
            );
            if (!selected) return;
            const requestId = identity || crypto.randomUUID();
            setIdentity(requestId);
            setBusy(true);
            try {
              const result = await mutate("/api/tasks", {
                request_id: requestId,
                snapshot_id: v.snapshot_id,
                config: selected.config,
              });
              navigate(`/tasks/${result.task_id}`);
            } catch (e) {
              message.error((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <SnapshotSelect rows={ds.data || []} />
          <ConfigurationSelect rows={cfg.data || []} />
          <Button type="primary" htmlType="submit" loading={busy}>
            提交回测
          </Button>
        </Form>
        <p className="muted">
          没有 worker
          时任务保留在队列。当前计算支持已发布的单交易日快照；跨日结算仍待实现。
        </p>
      </Card>
    </>
  );
}
export function TaskDetail() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/tasks/${id}`), 1000);
  const { message } = App.useApp();
  const task = q.data;
  async function control(action: string) {
    try {
      await mutate(`/api/tasks/${id}/control`, { action });
      q.refresh();
    } catch (e) {
      message.error((e as Error).message);
    }
  }
  return (
    <>
      <Heading
        title="回测任务"
        description="任务与尝试持久保存；页面关闭不会终止计算。"
      />
      <Failure error={q.error} />
      {task && (
        <>
          <Card>
            <Space>
              <Tag>{states[task.status] || task.status}</Tag>
              <Identity value={task.task_id} />
              {["QUEUED", "RUNNING"].includes(task.status) && (
                <Button onClick={() => control("cancel")}>请求取消</Button>
              )}
              {["FAILED", "INTERRUPTED"].includes(task.status) && (
                <Button onClick={() => control("retry")}>重试固定输入</Button>
              )}
              {task.run_id && (
                <Link href={`/runs/${task.run_id}`}>
                  <Button type="primary">查看研究报告</Button>
                </Link>
              )}
            </Space>
            <Progress
              percent={
                task.total ? Math.floor((task.completed / task.total) * 100) : 0
              }
              status={
                task.status === "SUCCEEDED"
                  ? "success"
                  : task.status === "FAILED"
                    ? "exception"
                    : "normal"
              }
            />
            <p>{task.reason}</p>
            <Fields
              value={{
                已处理: task.completed,
                总记录: task.total,
                快照: task.snapshot_id,
                代码: task.code_revision,
              }}
            />
          </Card>
          <Card title="执行尝试">
            <Table
              rowKey="attempt_id"
              dataSource={task.attempts}
              columns={[
                { title: "尝试", dataIndex: "attempt_id" },
                { title: "状态", dataIndex: "status" },
                { title: "开始", dataIndex: "started_at" },
                { title: "结束", dataIndex: "finished_at" },
                { title: "说明", dataIndex: "reason" },
              ]}
            />
          </Card>
          <Card title="固定输入与证据">
            <Evidence value={task} />
          </Card>
        </>
      )}
    </>
  );
}
