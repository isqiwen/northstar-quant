"use client";
import { query, mutate } from "./api/client";
import { useState } from "react";
import { Card, Button, Space, Tabs, Select } from "antd";
import Link from "next/link";
import { useRouter, useParams } from "next/navigation";
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
import { Line } from "../../shared/chart";
import { Action } from "./runtime";
export function Streams() {
  const q = useData(query("/api/streams"), 5000);
  const queries = useData(query("/api/broker/queries"));
  const configs = useData(query("/api/configurations"));
  const navigate = useRouter().push;
  return (
    <>
      <Heading
        title="持续行情与影子策略"
        description="使用已核验查询与固定配置，接收时长和留存用途由操作明确指定。"
      />
      <Failure error={q.error || queries.error || configs.error} />
      <Action
        title="启动有界持续接收"
        path="/api/streams"
        fields={[
          {
            name: "query_batch_id",
            label: "已保存查询",
            kind: "select",
            options: queries.data?.map((r) => ({
              value: r.batch_id,
              label: `${r.instrument || ""} · ${r.batch_id}`,
            })),
          },
          {
            name: "configuration_id",
            label: "本地固定配置",
            kind: "select",
            options: configs.data?.map((r) => ({
              value: r.configuration_id,
              label: r.name,
            })),
          },
          {
            name: "duration_seconds",
            label: "接收时长（秒）",
            kind: "integer",
            initial: 300,
          },
          { name: "use_basis", label: "留存用途与许可依据" },
          {
            name: "allow_retention",
            label: "确认允许留存回报与行情",
            kind: "check",
          },
        ]}
        onDone={(r) => navigate(`/streams/${r.stream_id}`)}
      />
      <Records
        title="接收会话"
        rows={q.data}
        rowKey="stream_id"
        columns={[
          {
            title: "会话",
            dataIndex: "stream_id",
            render: (v) => <Identity value={v} to={`/streams/${v}`} />,
          },
          {
            title: "状态",
            dataIndex: "status",
            render: (v) => <Status value={v} />,
          },
          { title: "已接收", dataIndex: "received" },
          { title: "已处理", dataIndex: "cursor" },
        ]}
      />
    </>
  );
}
export function Stream() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/streams/${id}`), 3000);
  const budgets = useData(query(`/api/streams/${id}/opening-budgets`), 5000);
  const [sequence, setSequence] = useState<number>();
  const step = useData(
    query(
      sequence === undefined
        ? null
        : `/api/streams/${id}/decisions/${sequence}`,
    ),
  );
  const queryId = q.data?.binding?.request?.query_batch_id;
  const ledger = useData(
    query(queryId ? `/api/broker/queries/${queryId}/ledger-context` : null),
    5000,
  );
  const [upper, setUpper] = useState<number>();
  const safe = !q.error && !q.loading;
  const refresh = () => {
    q.refresh();
    budgets.refresh();
    ledger.refresh();
  };
  const rows = q.data?.steps || [];
  const bars = [...rows]
    .reverse()
    .flatMap((r) => (r.result.bar ? [r.result.bar] : []));
  return (
    <>
      <Heading
        title="持续接收详情"
        description="每三秒读取内核确认事实。控制不代表撤单、成交或执行授权。"
        actions={
          <Space>
            <Button onClick={refresh}>刷新观察</Button>
            <Link href={`/streams/${id}/authorizations`}>执行限额与授权</Link>
          </Space>
        }
      />
      <Failure error={q.error || budgets.error || ledger.error} />
      {q.data && (
        <>
          <Card>
            <Fields
              value={{
                连接: q.data.connection,
                影子状态: q.data.paused ? "PAUSED" : "ACTIVE",
                已接收: q.data.received,
                处理前缀: q.data.cursor,
                行情年龄秒: q.data.market_age_seconds,
                原因: q.data.reason,
              }}
            />
          </Card>
          <div className="grid-three">
            {(
              [
                ["PAUSE", "暂停影子计算"],
                ["RESUME", "恢复影子计算"],
                ["STOP", "停止接收"],
              ] as const
            ).map(([action, title]) => (
              <Action
                key={action}
                title={title}
                path={`/api/streams/${id}/control`}
                fixed={{ action }}
                disabled={!safe}
                onDone={refresh}
              />
            ))}
          </div>
          <Tabs
            items={[
              {
                key: "market",
                label: "行情与决定",
                children: (
                  <>
                    <Card title="已完成分钟">
                      <Line
                        name="分钟收盘价"
                        labels={bars.map((s) => s.completed_at)}
                        values={bars.map((s) => Number(s.close))}
                      />
                    </Card>
                    <Records
                      title="已保存的分钟与策略步骤"
                      rows={rows}
                      rowKey="sequence"
                      columns={[
                        {
                          title: "序号",
                          dataIndex: "sequence",
                          render: (v) => (
                            <Button type="link" onClick={() => setSequence(v)}>
                              {v}
                            </Button>
                          ),
                        },
                        { title: "时间", dataIndex: "committed_at" },
                        {
                          title: "收盘价",
                          dataIndex: "result",
                          render: (r) => r.bar?.close ?? "—",
                        },
                        {
                          title: "目标",
                          dataIndex: "result",
                          render: (r) =>
                            r.intent?.target_fraction ?? "无新目标",
                        },
                        {
                          title: "原因",
                          dataIndex: "result",
                          render: (r) => r.reason,
                        },
                      ]}
                    />
                    <Failure error={step.error} />
                    {step.data && (
                      <Evidence
                        value={step.data}
                        title={`固定步骤 ${sequence} 的输入与结果`}
                      />
                    )}
                    <Evidence
                      value={q.data.state}
                      title="行情、预热与策略状态"
                    />
                  </>
                ),
              },
              {
                key: "account",
                label: "账户事实",
                children: (
                  <>
                    <Card>
                      <Fields
                        value={{
                          账户进度: q.data.account_progress?.status,
                          已入账前缀: q.data.account_progress?.through_sequence,
                        }}
                      />
                    </Card>
                    <Action
                      title="补处理确认账户事实"
                      path={`/api/streams/${id}/account-catchup`}
                      disabled={!safe || !ledger.data?.baseline_id}
                      fixed={{
                        baseline_id: ledger.data?.baseline_id ?? undefined,
                      }}
                      fields={[
                        {
                          name: "through_sequence",
                          label: "固定来源前缀上界",
                          kind: "integer",
                        },
                      ]}
                      onDone={refresh}
                    />
                    <Action
                      title="登记流成交持仓"
                      path={`/api/streams/${id}/position-entries`}
                      disabled={!safe || !ledger.data?.baseline_id}
                      fixed={{
                        baseline_id: ledger.data?.baseline_id ?? undefined,
                      }}
                      fields={[
                        {
                          name: "through_sequence",
                          label: "固定成交前缀上界",
                          kind: "integer",
                        },
                      ]}
                      onDone={refresh}
                    />
                    <Evidence value={q.data.account_progress} />
                    <Evidence value={ledger.data} title="账本与核对" />
                  </>
                ),
              },
              {
                key: "budget",
                label: "开仓预算",
                children: (
                  <>
                    <Action
                      title="计算固定开仓预算"
                      path={`/api/streams/${id}/opening-budgets`}
                      disabled={!safe}
                      fields={[
                        {
                          name: "sequence",
                          label: "已保存策略步骤",
                          kind: "select",
                          options: rows
                            .filter((r) => r.result.intent)
                            .map((r) => ({
                              value: r.sequence,
                              label: String(r.sequence),
                            })),
                        },
                        {
                          name: "order_check_id",
                          label: "固定委托核对",
                          kind: "select",
                          options: budgets.data?.order_checks?.map((c) => ({
                            value: c.check_id,
                            label: c.check_id,
                          })),
                        },
                        { name: "limit_price", label: "限价" },
                      ]}
                      onDone={refresh}
                    />
                    <Records
                      title="预算记录"
                      rows={budgets.data?.budgets}
                      rowKey="budget_id"
                      columns={[
                        {
                          title: "预算",
                          dataIndex: "budget_id",
                          render: (v) => (
                            <Identity
                              value={v}
                              to={`/broker/opening-budgets/${v}`}
                            />
                          ),
                        },
                        {
                          title: "状态",
                          dataIndex: "status",
                          render: (v) => <Status value={v} />,
                        },
                      ]}
                    />
                  </>
                ),
              },
              {
                key: "archive",
                label: "归档发布",
                children: (
                  <>
                    <Action
                      title="归档固定行情前缀"
                      path={`/api/streams/${id}/archive`}
                      disabled={!safe}
                      fields={[
                        {
                          name: "through_sequence",
                          label: "归档前缀上界",
                          kind: "integer",
                        },
                        { name: "session_open", label: "开始时间（UTC）" },
                        { name: "session_close", label: "结束时间（UTC）" },
                        {
                          name: "allow_download",
                          label: "允许下载归档",
                          kind: "check",
                        },
                      ]}
                      onDone={refresh}
                    />
                    <Records
                      title="归档尝试"
                      rows={q.data.archives}
                      rowKey="attempt_id"
                      columns={[
                        {
                          title: "尝试",
                          dataIndex: "attempt_id",
                          render: (v) => (
                            <Identity value={v} to={`/attempts/${v}`} />
                          ),
                        },
                        {
                          title: "状态",
                          dataIndex: "status",
                          render: (v) => <Status value={v} />,
                        },
                        {
                          title: "快照",
                          dataIndex: "snapshot_id",
                          render: (v) =>
                            v ? (
                              <Identity value={v} to={`/datasets/${v}`} />
                            ) : null,
                        },
                      ]}
                    />
                  </>
                ),
              },
            ]}
          />
          <Evidence value={q.data} title="接收会话完整证据" />
        </>
      )}
    </>
  );
}
