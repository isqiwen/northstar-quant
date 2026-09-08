"use client";
import { query, mutate } from "./api/client";
import { useState, useEffect } from "react";
import { App, Button, Card, Form, Input, Select, Spin, Tabs, Tag } from "antd";
import Link from "next/link";
import { useRouter, useParams } from "next/navigation";
import { download } from "../../shared/api";
import { useData } from "../../shared/data";
import { ConfigFields, Parameters, SnapshotSelect } from "../../shared/forms";
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
export function Catalog() {
  const q = useData(query("/api/catalog"));
  const runs = useData(query("/api/factor-runs"));
  const versions = useData(query("/api/strategy-versions"));
  return (
    <>
      <Heading
        title="因子与策略"
        description="计算实现、不可变参数与研究证据，各有明确身份。"
        actions={
          <Link href="/configurations/new">
            <Button type="primary">新建策略配置</Button>
          </Link>
        }
      />
      <Failure error={q.error} />
      <Tabs
        items={[
          {
            key: "factors",
            label: "因子库",
            children: (
              <>
                <div className="grid-two">
                  {q.data?.factors.map((f) => (
                    <Card
                      key={f.factor_id}
                      title={f.name}
                      extra={<Tag>{f.category}</Tag>}
                    >
                      <p className="muted">{f.description}</p>
                      <p>
                        <Identity value={f.factor_id} />
                      </p>
                      <p>
                        {f.capabilities.map((c: string) => (
                          <Tag key={c}>{c}</Tag>
                        ))}
                      </p>
                      <Link href={`/factors/${f.factor_id}`}>
                        <Button>配置并计算</Button>
                      </Link>
                    </Card>
                  ))}
                </div>
                <Records
                  title="计算记录"
                  rows={runs.data}
                  rowKey="attempt_id"
                  columns={[
                    {
                      title: "计算",
                      dataIndex: "attempt_id",
                      render: (v) => (
                        <Identity value={v} to={`/factor-runs/${v}`} />
                      ),
                    },
                    {
                      title: "状态",
                      dataIndex: "status",
                      render: (v) => <Status value={v} />,
                    },
                    {
                      title: "参数版本",
                      dataIndex: "revision_id",
                      render: (v) => <Identity value={v} />,
                    },
                    { title: "创建时间", dataIndex: "created_at" },
                  ]}
                />
              </>
            ),
          },
          {
            key: "strategies",
            label: "策略库",
            children: (
              <div className="grid-two">
                {q.data?.strategies.map((s) => (
                  <Card
                    key={s.strategy_id}
                    title={s.name}
                    extra={<Tag>{s.category}</Tag>}
                  >
                    <p>{s.description}</p>
                    <Identity value={s.strategy_id} />
                    <p>
                      <Link
                        href={`/configurations/new?strategy=${s.strategy_id}`}
                      >
                        <Button>创建参数版本</Button>
                      </Link>
                    </p>
                  </Card>
                ))}
              </div>
            ),
          },
          {
            key: "versions",
            label: "版本与候选",
            children: (
              <Records
                title="固定策略版本"
                rows={versions.data}
                rowKey="version_id"
                columns={[
                  { title: "名称", dataIndex: "name" },
                  {
                    title: "版本",
                    dataIndex: "version_id",
                    render: (v) => (
                      <Identity value={v} to={`/strategy-versions/${v}`} />
                    ),
                  },
                  { title: "创建时间", dataIndex: "created_at" },
                ]}
              />
            ),
          },
        ]}
      />
    </>
  );
}
export function Factor() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/factor-definitions/${id}`));
  const ds = useData(query("/api/datasets"));
  const { message } = App.useApp();
  const navigate = useRouter().push;
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Heading
        title={q.data?.name || "因子计算"}
        description="固定参数、快照和可得时间，计算不会改变已有版本。"
      />
      <Failure error={q.error} />
      {q.data && (
        <Card title="计算配置">
          <Form
            layout="vertical"
            onFinish={async (v) => {
              setBusy(true);
              try {
                const revision = await mutate("/api/factor-revisions", {
                  factor_id: id!,
                  parameters: v.parameters,
                });
                const result = await mutate("/api/factor-runs", {
                  revision_id: revision.revision_id,
                  snapshot_id: v.snapshot_id,
                });
                navigate(`/factor-runs/${result.attempt_id}`);
              } catch (e) {
                message.error((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <SnapshotSelect rows={ds.data || []} />
            <Parameters
              definitions={q.data.parameters}
              prefix={["parameters"]}
            />
            <Button loading={busy} htmlType="submit" type="primary">
              固定参数并计算
            </Button>
          </Form>
        </Card>
      )}
    </>
  );
}
function Slot({ alias, factor }: { alias: string; factor: string }) {
  const q = useData(query(`/api/factor-definitions/${factor}`));
  return (
    <Card size="small" title={`因子依赖 · ${q.data?.name || factor}`}>
      <Failure error={q.error} />
      {q.data && (
        <Parameters
          definitions={q.data.parameters}
          prefix={["factors", alias]}
        />
      )}
    </Card>
  );
}
export function Configuration() {
  const registry = useData(query("/api/catalog"));
  const defaults = useData(query("/api/configuration-defaults"));
  const [id, setId] = useState("trend.momentum");
  useEffect(() => {
    setId(
      new URLSearchParams(window.location.search).get("strategy") ||
        "trend.momentum",
    );
  }, []);
  const q = useData(query(`/api/strategy-definitions/${id}`));
  const navigate = useRouter().push;
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Heading
        title="新建策略配置"
        description="参数保存为固定版本，活动会话保持原配置。"
      />
      <Card>
        <Select
          aria-label="策略实现"
          value={id}
          onChange={setId}
          style={{ width: 360 }}
          options={registry.data?.strategies.map((s) => ({
            value: s.strategy_id,
            label: s.name,
          }))}
        />
      </Card>
      <Failure error={q.error} />
      {q.data && defaults.data ? (
        <Form
          key={id}
          layout="vertical"
          onFinish={async (v) => {
            setBusy(true);
            try {
              const bindings = Object.fromEntries(
                Object.entries(q.data!.factor_slots).map(([alias, factor]) => [
                  alias,
                  { factor_id: factor, parameters: v.factors[alias] },
                ]),
              );
              await mutate("/api/configurations", {
                name: v.name,
                config: {
                  strategy: {
                    strategy_id: id,
                    parameters: v.parameters,
                    factor_bindings: bindings,
                  },
                  risk: v.risk,
                  simulation: v.simulation,
                },
              });
              message.success("不可变配置已保存");
              navigate("/");
            } catch (e) {
              message.error((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Card title="策略参数">
            <Form.Item
              name="name"
              label="配置名称"
              rules={[{ required: true, max: 80 }]}
            >
              <Input />
            </Form.Item>
            <Parameters
              definitions={q.data.parameters}
              prefix={["parameters"]}
            />
          </Card>
          {Object.entries(q.data.factor_slots).map(([alias, factor]) => (
            <Slot key={alias} alias={alias} factor={String(factor)} />
          ))}
          <Card title="风险限额">
            <ConfigFields defaults={defaults.data.risk} prefix="risk" />
          </Card>
          <Card title="模拟成本与初始资金">
            <ConfigFields
              defaults={defaults.data.simulation}
              prefix="simulation"
            />
          </Card>
          <Button type="primary" htmlType="submit" loading={busy}>
            保存不可变配置
          </Button>
        </Form>
      ) : (
        <Spin />
      )}
    </>
  );
}
export function FactorRun() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/factor-runs/${id}`));
  const revisions = useData(query("/api/factor-revisions"));
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  const rows = q.data?.result?.values || [];
  return (
    <>
      <Heading
        title="因子计算结果"
        description="可用性与覆盖率检查，不代表收益评价或交易许可。"
      />
      <Failure error={q.error} />
      {q.data && (
        <>
          <Card>
            <Fields
              value={{
                状态: q.data.status,
                样本数: rows.length,
                参数版本: q.data.revision_id,
              }}
            />
          </Card>
          <Card title="因子时间序列">
            <Line
              name="因子值"
              labels={rows.map((r) => r.at)}
              values={rows.map((r) =>
                r.value === null ? null : Number(r.value),
              )}
            />
          </Card>
          <Records
            title="逐行计算"
            rows={rows}
            rowKey="observation_id"
            columns={[
              { title: "可得时间", dataIndex: "at" },
              {
                title: "状态",
                dataIndex: "status",
                render: (v) => <Status value={v} />,
              },
              { title: "因子值", dataIndex: "value" },
              { title: "说明", dataIndex: "reason" },
            ]}
          />
          <Card title="研究说明">
            <Form
              layout="vertical"
              onFinish={async (v) => {
                setBusy(true);
                try {
                  await mutate(
                    `/api/factor-revisions/${q.data!.revision_id}/annotations`,
                    { description: v.description },
                  );
                  revisions.refresh();
                  message.success("说明已追加");
                } catch (e) {
                  message.error((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <Form.Item
                name="description"
                label="追加研究说明"
                rules={[{ required: true, max: 2000 }]}
              >
                <Input.TextArea rows={3} />
              </Form.Item>
              <Button htmlType="submit" loading={busy}>
                保存说明
              </Button>
            </Form>
            {revisions.data
              ?.find((r) => r.revision_id === q.data!.revision_id)
              ?.annotations.map((r) => (
                <p key={r.at}>
                  {r.description}
                  <small className="muted"> · {r.at}</small>
                </p>
              ))}
          </Card>
          <Evidence value={q.data} />
        </>
      )}
    </>
  );
}
export function Version() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/strategy-versions/${id}`));
  const candidates = useData(query("/api/strategy-candidates"));
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Heading
        title={q.data?.name || "固定策略版本"}
        description="发布候选与生产部署、执行授权分别记录。"
      />
      <Failure error={q.error} />
      {q.data && (
        <>
          <Card title="研究证据">
            <Fields
              value={{
                版本: id,
                策略: q.data.document.configuration.config.strategy.strategy_id,
                研究结果数: q.data.document.evidence.length,
              }}
            />
            <p>
              <Button
                type="primary"
                loading={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    const result = await mutate(
                      `/api/strategy-versions/${id}/publish`,
                      {},
                    );
                    download(result, `candidate-${result.candidate_id}.json`);
                    candidates.refresh();
                  } catch (e) {
                    message.error((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                发布并下载固定候选
              </Button>
            </p>
            {candidates.data
              ?.filter((c) => c.version_id === id)
              .map((c) => (
                <p key={c.candidate_id}>
                  已发布：
                  <Identity value={c.candidate_id} />{" "}
                  <Tag>
                    {c.production_eligible ? "正式代码材料" : "开发材料"}
                  </Tag>
                  <Button
                    onClick={() =>
                      download(c, `candidate-${c.candidate_id}.json`)
                    }
                  >
                    下载
                  </Button>
                </p>
              ))}
          </Card>
          <Evidence value={q.data.document} />
        </>
      )}
    </>
  );
}
