"use client";
import { StorageAlert } from "./storage-alert";
import { ContractReview } from "./contract-review";
import { SyncContracts } from "./sync-contracts";

import {
  App,
  Button,
  Card,
  Collapse,
  Form,
  Input,
  Modal,
  Progress,
  Space,
  Drawer,
  Tag,
  Descriptions,
} from "antd";
import { useEffect, useState } from "react";
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
  VALIDATED: "响应已校验",
  SPLIT: "请求已拆分",
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
  const [diagnostics, setDiagnostics] = useState<string>();
  function showRequests(scope: string) {
    setFilter({ dataset: "", status: "", page: 1 });
    setDiagnostics(scope);
  }
  useEffect(() => {
    const parameters = new URLSearchParams(window.location.search);
    const contract = parameters.get("contract");
    if (contract) setDiagnostics(contract);
    const id = parameters.get("request");
    if (id)
      void fetchQuery(query(`/api/sync/jobs/${id}`))
        .then(setDetail)
        .catch((e) => message.error((e as Error).message));
  }, [message]);
  const origin = detail?.origin as Row | undefined;
  const source = detail?.reprocess_source as Row | undefined;
  const data = current.error ? undefined : current.data;
  const config = data?.settings;
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
          ? "自动同步已启用，后台按已结束合约采集完整生命周期"
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
        description="交易所 → 品种 → 合约。核心接纳通过后按合约发布，行情和结算资料归入合约详情。"
      />
      <Failure error={current.error} />
      <StorageAlert capacity={config?.source_capacity} />
      {config?.error && (
        <Card>
          <Tag color="red">同步暂停</Tag>
          {String(config.error)}
        </Card>
      )}
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
        <Button onClick={() => showRequests("")}>采集服务诊断</Button>
      </Space>
      <div>
        {(["contracts"] as const).map((key) => {
          const lane = data?.lanes?.find((row) => row.lane === key);
          const total = lane?.total ?? 0;
          const done = lane?.validated ?? 0;
          return (
            <Card key={key} title="合约接纳与发布进度">
              <p>
                {lane && lane.start <= lane.end
                  ? `${lane.start} → ${lane.end}`
                  : "等待已结束合约规划"}
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
                <Tag>待采集 / 采集中：{lane?.running ?? 0} 个合约</Tag>
                <Tag>待完整性核验：{lane?.waiting ?? 0} 个合约</Tag>
                <Tag color={lane?.blocked ? "red" : "default"}>
                  已拒绝：{lane?.blocked ?? 0} 个合约
                </Tag>
              </Space>
              <p>最早未完成区间：{lane?.oldest_pending ?? "暂无"}</p>
              <p className="muted">
                核心数据通过即可发布；辅助缺失计入完整度与质量，不拒绝整个合约。
              </p>
            </Card>
          );
        })}
      </div>
      <SyncContracts onReview={setReviewScope} onRequests={showRequests} />
      <Collapse
        items={[
          {
            key: "settings",
            label: "采集设置与目录信息",
            children: (
              <>
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
                        {data?.token_configured
                          ? "已配置（不回显）"
                          : "尚未配置"}
                      </Tag>
                    </p>
                    <Form
                      form={form}
                      layout="vertical"
                      onFinish={async (values: { token: string }) => {
                        setBusy(true);
                        try {
                          await mutate("/api/sync/token", {
                            token: values.token,
                          });
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

                    <p className="muted">
                      从 2015-01-01 起查找历史合约，按最后交易日从早到晚下载。
                      先取得完整交易日历，再规划下载；跨起点合约保留上市以来的历史。
                      跨越起点的合约保留上市日起的完整数据。最后交易日和最后交割日均已结束才可下载，日期不明则等待核实。
                    </p>
                    <p>
                      {planned
                        ? "本轮目录规划完成"
                        : "等待目录或正在逐合约规划"}
                    </p>
                  </Card>
                </div>
              </>
            ),
          },
        ]}
      />
      <ContractReview
        scope={reviewScope}
        onClose={() => setReviewScope(undefined)}
      />
      <Drawer
        title={diagnostics ? `${diagnostics} · 请求诊断` : "采集服务诊断"}
        open={diagnostics !== undefined}
        onClose={() => setDiagnostics(undefined)}
        size="large"
      >
        {diagnostics !== undefined && (
          <SyncJobs
            ownerScope={diagnostics}
            datasets={data?.datasets ?? []}
            filter={filter}
            onFilter={setFilter}
            onDetail={setDetail}
          />
        )}
      </Drawer>
      <Modal
        title="内部请求与响应证据"
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
                  children: labels[String(detail.status)] || "固定响应版本",
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
