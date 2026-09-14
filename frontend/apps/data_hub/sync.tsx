"use client";
import { StorageAlert } from "./storage-alert";
import { ContractReview } from "./contract-review";

import {
  App,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Progress,
  Space,
  Table,
  Tag,
  Descriptions,
} from "antd";
import { useEffect, useRef, useState } from "react";
import { query, mutate } from "./api/client";
import { useData, fetchQuery } from "../../shared/data";
import { Evidence, Failure, Heading } from "../../shared/ui";

import { SyncJobs, type JobFilter } from "./sync-jobs";

import { QualityIssues } from "./quality-issues";

type Row = Record<string, unknown>;
const labels: Record<string, string> = {
  PENDING: "待同步",
  RUNNING: "处理中",
  WAITING: "等待重试或源端发布",
  BLOCKED: "需处理",
  VALIDATED: "已校验并发布",
  SPLIT: "已重新分片",
};

export function TushareSync() {
  const { message } = App.useApp();
  const current = useData(query("/api/sync"), 3000);
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  const [reviewScope, setReviewScope] = useState<string>();
  const [detail, setDetail] = useState<Row>();
  const [filter, setFilter] = useState<JobFilter>({
    dataset: "",
    status: "",
    page: 1,
  });
  const jobsSection = useRef<HTMLDivElement>(null);
  function showJobs(dataset: string, status: string) {
    setFilter({ dataset, status, page: 1 });
    jobsSection.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("request");
    if (id)
      void fetchQuery(query(`/api/sync/jobs/${id}`))
        .then(setDetail)
        .catch((e) => message.error((e as Error).message));
  }, [message]);
  const origin = detail?.origin as Row | undefined;
  const source = detail?.reprocess_source as Row | undefined;
  const data = current.error ? undefined : current.data;
  const config = data?.settings;
  const groups = data?.progress ?? [];
  const targets = (config?.targets ?? []) as Row[];
  const catalogErrors = (config?.catalog_errors ?? []) as Row[];
  const planned =
    !!config?.catalog_ready &&
    !!config?.planned_at &&
    data?.unplanned_contracts === 0;
  async function enabled(value: boolean) {
    setBusy(true);
    try {
      await mutate("/api/sync/settings", {
        revision: Number(config?.revision),
        enabled: value,
      });
      current.refresh();
      message.success(
        value
          ? "自动同步已启用，后台按已退市合约采集完整生命周期"
          : "已暂停；当前请求完成后停止领取新任务",
      );
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Heading
        title="Tushare 自动同步"
        description="只下载已退市真实合约，核验上市至退市全部必需数据。完整合约对应一个发布包。"
      />
      <Failure error={current.error} />
      <StorageAlert capacity={config?.source_capacity} />
      {config?.error && (
        <Card>
          <Tag color="red">同步暂停</Tag>
          {String(config.error)}
        </Card>
      )}
      {!!catalogErrors.length && (
        <Card title="目录范围待核查">
          {catalogErrors.map((r) => (
            <p key={String(r.ts_code)}>
              {String(r.ts_code)}：{String(r.planning_error)}
            </p>
          ))}
        </Card>
      )}
      <div className="grid-two">
        <Card title="数据服务凭据">
          <p>
            Token：
            <Tag color={data?.token_configured ? "green" : "default"}>
              {data?.token_configured ? "已配置（不回显）" : "尚未配置"}
            </Tag>
          </p>
          <Form
            form={form}
            layout="vertical"
            onFinish={async (values: { token: string }) => {
              setBusy(true);
              try {
                await mutate("/api/sync/token", { token: values.token });
                form.resetFields();
                current.refresh();
                message.success("Token 已保存到后端私有凭据目录");
              } catch (e) {
                message.error((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <Form.Item
              name="token"
              label="Tushare token"
              rules={[{ required: true, min: 16, max: 512 }]}
            >
              <Input.Password
                autoComplete="new-password"
                placeholder="输入或更换 token"
              />
            </Form.Item>
            <Button htmlType="submit" loading={busy}>
              保存 token
            </Button>
          </Form>
        </Card>
        <Card title="自动同步">
          <p>
            目标交易日：
            {targets.length
              ? targets
                  .map((r) => `${r.exchange} ${r.target_trading_day}`)
                  .join(" · ")
              : "等待交易日历"}
          </p>
          <Space wrap>
            <Tag color={config?.enabled ? "blue" : "default"}>
              {config?.enabled ? "已启用" : "已暂停"}
            </Tag>
            <Button
              type="primary"
              disabled={!data?.token_configured || !config}
              loading={busy}
              onClick={() => enabled(true)}
            >
              {config?.enabled ? "重试异常任务" : "开始同步全部数据"}
            </Button>
            <Button
              disabled={!config?.enabled}
              loading={busy}
              onClick={() => enabled(false)}
            >
              暂停
            </Button>
            <Button onClick={current.refresh}>刷新</Button>
          </Space>
          <p className="muted">
            从 2012-01-01 起查找历史合约，按最后交易日从早到晚下载。
            跨越起点的合约保留上市日起的完整数据。最后交易日和最后交割日均已结束才可下载，日期不明则等待核实。
          </p>
          <p>{planned ? "本轮目录规划完成" : "等待目录或正在逐合约规划"}</p>
        </Card>
      </div>
      <div className="grid-2">
        {(["contracts"] as const).map((key) => {
          const lane = data?.lanes?.find((row) => row.lane === key);
          const total = lane?.total ?? 0;
          const done = lane?.validated ?? 0;
          return (
            <Card key={key} title="完整合约发布进度">
              <p>
                {lane && lane.start <= lane.end
                  ? `${lane.start} → ${lane.end}`
                  : "等待已退市合约规划"}
              </p>
              <p>
                已发布 {done.toLocaleString()} / 已规划 {total.toLocaleString()}{" "}
                个合约
              </p>
              <Progress
                percent={total ? Math.round((done / total) * 1000) / 10 : 0}
                status={
                  planned && total > 0 && done === total ? "success" : "active"
                }
              />
              <Space wrap>
                <Tag>处理中：{lane?.running ?? 0}</Tag>
                <Tag>等待重试：{lane?.waiting ?? 0}</Tag>
                <Tag color={lane?.blocked ? "red" : "default"}>
                  需处理：{lane?.blocked ?? 0}
                </Tag>
              </Space>
              <p>最早未完成区间：{lane?.oldest_pending ?? "暂无"}</p>
              <p className="muted">
                只有全部必需数据完整通过才计为发布；待核验和异常合约不计完成。
              </p>
            </Card>
          );
        })}
      </div>
      <Card title="合约下载与验收">
        <Table<Row>
          rowKey="scope"
          size="small"
          dataSource={(config?.contracts ?? []) as Row[]}
          columns={[
            { title: "交易所", dataIndex: "exchange" },
            { title: "品种", dataIndex: "product" },
            {
              title: "合约",
              render: (_, r) => String(r.display_name || r.scope),
            },
            { title: "上市", dataIndex: "start_date" },
            { title: "最后交易日", dataIndex: "last_trade_date" },
            { title: "最后交割日", dataIndex: "last_delivery_date" },
            { title: "生命周期", dataIndex: "lifecycle_reason" },
            {
              title: "采集 / 发布",
              render: (_, r) =>
                (
                  ({
                    COLLECTING: "采集中",
                    VERIFYING: "待核验",
                    REJECTED: "已拒绝",
                    PUBLISHED: "已发布",
                  }) as Record<string, string>
                )[String(r.status)] ?? String(r.status),
            },
            {
              title: "",
              render: (_, r) => (
                <Button
                  type="link"
                  onClick={() => setReviewScope(String(r.scope))}
                >
                  整体验收
                </Button>
              ),
            },
          ]}
        />
      </Card>
      <ContractReview
        scope={reviewScope}
        onClose={() => setReviewScope(undefined)}
      />
      <Card title="数据范围与进度">
        <p>
          全部交易所 → 品种 → 已退市真实合约，覆盖上市至退市；1、5、15、30、60
          分钟、日/周/月线及期货相关历史资料。按目录发现新退市合约；日期缺失不猜测，空响应不当作完成。
        </p>
        <Table
          pagination={false}
          rowKey="key"
          dataSource={data?.datasets ?? []}
          columns={[
            { title: "数据", dataIndex: "label" },
            {
              title: "内部请求进度",
              render: (_, row) => (
                <Space wrap>
                  {groups
                    .filter((g) => g.dataset === row.key)
                    .map((g) => (
                      <button
                        type="button"
                        key={String(g.status)}
                        aria-label={`${row.label} ${labels[String(g.status)]} ${g.windows} 条，查看任务`}
                        onClick={() =>
                          showJobs(String(row.key), String(g.status))
                        }
                        style={{
                          border: 0,
                          background: "transparent",
                          padding: 0,
                          cursor: "pointer",
                        }}
                      >
                        <Tag
                          color={
                            g.status === "BLOCKED"
                              ? "red"
                              : g.status === "VALIDATED"
                                ? "green"
                                : "default"
                          }
                        >
                          {labels[String(g.status)]}: {String(g.windows)}
                        </Tag>
                      </button>
                    ))}
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <div ref={jobsSection}>
        <SyncJobs
          datasets={data?.datasets ?? []}
          filter={filter}
          onFilter={setFilter}
          onDetail={setDetail}
        />
      </div>
      <Modal
        title="同步记录与固定数据"
        open={!!detail}
        onCancel={() => setDetail(undefined)}
        footer={null}
        width={1000}
      >
        {detail && (
          <>
            <Descriptions
              column={1}
              items={[
                {
                  key: "request",
                  label: "记录身份",
                  children: String(detail.request_id),
                },
                {
                  key: "scope",
                  label: "合约 / 范围",
                  children: String(detail.scope || "—"),
                },
                {
                  key: "range",
                  label: "时间区间",
                  children: `${detail.start_at || "目录"} — ${detail.end_at || ""}`,
                },
                {
                  key: "origin",
                  label: "已发现的数据起点",
                  children: origin?.first_observed
                    ? `${origin.first_observed}（最早有效响应；更早范围仍需核查）`
                    : "探测中；覆盖完整生命周期，空响应不作为起点证据",
                },
                {
                  key: "status",
                  label: "状态",
                  children: labels[String(detail.status)] || "固定发布版本",
                },
                {
                  key: "reason",
                  label: "原因",
                  children: String(detail.error || "无已记录异常"),
                },
              ]}
            />
            {source && (
              <Space direction="vertical">
                <p>
                  使用当前规则重处理最新留存响应，不重新下载。同步暂停时任务保留排队；页面关闭不影响执行。
                </p>
                <Button
                  loading={busy}
                  disabled={
                    detail.status === "RUNNING" ||
                    detail.status === "SPLIT" ||
                    !!detail.source_generation
                  }
                  onClick={async () => {
                    setBusy(true);
                    try {
                      setDetail(
                        await mutate("/api/sync/reprocess", {
                          request_id: String(detail.request_id),
                          source_generation: String(source.generation),
                        }),
                      );
                      current.refresh();
                      message.success("已排队重处理留存响应");
                    } catch (e) {
                      message.error((e as Error).message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  重处理已留存响应
                </Button>
              </Space>
            )}
            <QualityIssues attempts={(detail.attempts_detail ?? []) as Row[]} />
            <Evidence value={detail} />
          </>
        )}
      </Modal>
    </>
  );
}
