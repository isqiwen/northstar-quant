"use client";
import Link from "next/link";
import { useState } from "react";
import {
  App,
  Alert,
  Button,
  Card,
  Form,
  Input,
  Select,
  Table,
  Tag,
} from "antd";
import { query, mutate } from "../api/client";
import { requestId } from "../../../shared/api";
import { useData } from "../../../shared/data";
import { Failure, Identity } from "../../../shared/ui";

const phases: Record<string, string> = {
  train: "训练",
  validation: "验证",
  test: "测试",
};
const states: Record<string, string> = {
  SEARCHING: "训练与验证",
  TESTING: "独立测试",
  SUCCEEDED: "完成",
  FAILED: "失败",
  INTERRUPTED: "已中断，请检查任务",
  IMPLEMENTATION_MISMATCH: "实现版本已变更",
  NOT_QUEUED: "等待排队",
  QUEUED: "排队中",
  RUNNING: "运行中",
  FINALIZING: "保存结果",
  CANCELLED: "已取消",
  CANCEL_REQUESTED: "请求取消",
};
export default function Studies() {
  const studies = useData(query("/api/experiments"), 2000);
  const datasets = useData(query("/api/datasets"));
  const configurations = useData(query("/api/configurations"));
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [identity, setIdentity] = useState<string>();
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Failure
        error={studies.error || datasets.error || configurations.error}
      />
      <Alert
        type="info"
        showIcon
        title="固定参数实验"
        description="三个时间有序、互不重叠的固定快照；候选使用相同账户、风险和成本。按验证净收益选择，仅测试胜出配置，不按测试收益重新选参。各窗口独立预热和建账；当前不拟合机器学习模型。重复利用测试区间不构成未使用样本。"
      />
      <Card title="新建有限参数实验">
        <Form
          form={form}
          layout="vertical"
          disabled={busy}
          onValuesChange={() => setIdentity(undefined)}
          onFinish={async (v) => {
            const id = identity || requestId();
            setIdentity(id);
            setBusy(true);
            try {
              const selected = (v.configurations as string[]).map((id) => {
                const config = configurations.data?.find(
                  (c) => c.configuration_id === id,
                );
                if (!config) throw new Error("配置不可用，请刷新后重试");
                return config.config;
              });
              await mutate("/api/experiments", {
                request_id: id,
                hypothesis: v.hypothesis,
                train_snapshot: v.train,
                validation_snapshot: v.validation,
                test_snapshot: v.test,
                configurations: selected,
              });
              message.success("实验已保存，独立 worker 将安排任务");
              form.resetFields();
              setIdentity(undefined);
            } catch (e) {
              message.error((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Form.Item
            name="hypothesis"
            label="研究假设"
            rules={[{ required: true, max: 1000 }]}
          >
            <Input.TextArea rows={2} maxLength={1000} />
          </Form.Item>
          <div className="grid-three">
            {Object.entries(phases).map(([phase, label]) => (
              <Form.Item
                key={phase}
                name={phase}
                label={`${label}快照`}
                rules={[{ required: true }]}
              >
                <Select
                  showSearch
                  optionFilterProp="label"
                  options={(datasets.data || []).map((d) => ({
                    value: d.snapshot_id,
                    label: `${d.symbol} · ${d.session_open} · ${d.snapshot_id.slice(0, 8)}`,
                  }))}
                />
              </Form.Item>
            ))}
          </div>
          <Form.Item
            name="configurations"
            label="候选配置（2–64 个，仅策略或因子参数不同）"
            rules={[{ required: true, type: "array", min: 2, max: 64 }]}
          >
            <Select
              mode="multiple"
              options={(configurations.data || []).map((c) => ({
                value: c.configuration_id,
                label: `${c.name} · ${c.configuration_id.slice(0, 8)}`,
              }))}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={busy}>
            固定计划并提交
          </Button>{" "}
          <Link href="/configurations/new">创建候选配置</Link>
        </Form>
      </Card>
      <Table
        rowKey="experiment_id"
        dataSource={studies.data || []}
        pagination={{ pageSize: 10 }}
        columns={[
          {
            title: "实验",
            render: (_, r) => <Identity value={r.experiment_id} />,
          },
          { title: "假设", render: (_, r) => String(r.plan.hypothesis) },
          {
            title: "状态",
            render: (_, r) => <Tag>{states[r.status] || r.status}</Tag>,
          },
          {
            title: "选中配置",
            render: (_, r) =>
              r.selection?.winner ? (
                <Identity value={String(r.selection.winner)} />
              ) : (
                "—"
              ),
          },
        ]}
        expandable={{
          expandedRowRender: (r) => (
            <>
              <p>
                选择规则：验证净收益最高；同分按固定配置身份排序。失败候选保留，测试结果不参与选参。
              </p>
              <Table
                rowKey="task_id"
                pagination={false}
                dataSource={r.trials}
                columns={[
                  {
                    title: "候选",
                    render: (_, t) => (
                      <Identity value={String(t.candidate_id)} />
                    ),
                  },
                  { title: "阶段", render: (_, t) => phases[String(t.phase)] },
                  {
                    title: "状态",
                    render: (_, t) =>
                      states[String(t.status)] || String(t.status),
                  },
                  {
                    title: "任务与诊断",
                    render: (_, t) =>
                      t.status !== "NOT_QUEUED" ? (
                        <Link href={`/tasks/${t.task_id}`}>查看任务</Link>
                      ) : (
                        "—"
                      ),
                  },
                  {
                    title: "账户报告",
                    render: (_, t) =>
                      t.run_id ? (
                        <Link href={`/runs/${t.run_id}`}>查看报告</Link>
                      ) : (
                        "—"
                      ),
                  },
                ]}
              />
            </>
          ),
        }}
      />
    </>
  );
}
