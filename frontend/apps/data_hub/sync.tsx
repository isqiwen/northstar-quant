"use client";

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
import { useEffect, useState } from "react";
import { query, mutate } from "./api/client";
import { useData, fetchQuery } from "../../shared/data";
import { Evidence, Failure, Heading } from "../../shared/ui";

type Row = Record<string, unknown>;
const labels: Record<string, string> = {
  PENDING: "待同步",
  RUNNING: "下载中",
  WAITING: "等待重试或源端发布",
  BLOCKED: "需处理",
  VALIDATED: "已校验并发布",
  SPLIT: "已拆成更小区间",
};

export function TushareSync() {
  const { message } = App.useApp();
  const current = useData(query("/api/sync"), 3000);
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<Row>();
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("request");
    if (id)
      void fetchQuery(query(`/api/sync/jobs/${id}`))
        .then(setDetail)
        .catch((e) => message.error((e as Error).message));
  }, [message]);
  const data = current.error ? undefined : current.data;
  const config = data?.settings;
  const groups = data?.progress ?? [];
  const targets = (config?.targets ?? []) as Row[];
  const catalogErrors = (config?.catalog_errors ?? []) as Row[];
  const total = groups
    .filter((r) => r.status !== "SPLIT")
    .reduce((n, r) => n + Number(r.windows), 0);
  const done = groups
    .filter((r) => r.status === "VALIDATED")
    .reduce((n, r) => n + Number(r.windows), 0);
  const planned =
    !!config?.catalog_ready &&
    !!config?.planned_at &&
    data?.unplanned_contracts === 0;
  const names = Object.fromEntries(
    (data?.datasets ?? []).map((r) => [String(r.key), String(r.label)]),
  );
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
          ? "自动同步已启用，后台继续下载全部历史并持续补齐"
          : "已暂停；当前分片完成后停止领取新任务",
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
        description="同步全部期货历史数据，自动补缺与复核。页面关闭不影响后台同步。"
      />
      <Failure error={current.error} />
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
          <p>
            {planned ? "本轮已规划" : "等待合约目录或正在规划历史区间"} · 已校验{" "}
            {done.toLocaleString()} / 已规划 {total.toLocaleString()} 个分片
          </p>
          <Progress
            percent={total ? Math.round((done / total) * 1000) / 10 : 0}
            status={
              done === total && planned && total > 0 ? "success" : "active"
            }
          />
          <p className="muted">
            进度按分片统计。下载记录通过校验后发布固定版本；空结果、限频和权限异常保留待办，不算完成。已校验响应不代表上游所有历史记录均无遗漏。
          </p>
        </Card>
      </div>
      <Card title="数据范围与进度">
        <p>
          全部交易所、全部品种和到期合约；1、5、15、30、60
          分钟、日/周/月线及期货相关历史资料。按交易所目录发现新增合约，周期性检查新增区间和历史缺口。
        </p>
        <Table
          pagination={false}
          rowKey="key"
          dataSource={data?.datasets ?? []}
          columns={[
            { title: "数据", dataIndex: "label" },
            {
              title: "分片进度",
              render: (_, row) => (
                <Space wrap>
                  {groups
                    .filter((g) => g.dataset === row.key)
                    .map((g) => (
                      <Tag
                        key={String(g.status)}
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
                    ))}
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Card title="最近任务与异常">
        <Table
          scroll={{ x: 1000 }}
          rowKey="request_id"
          dataSource={data?.jobs ?? []}
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: "数据",
              render: (_, r) => names[String(r.dataset)] ?? String(r.dataset),
            },
            { title: "合约 / 范围", dataIndex: "scope" },
            {
              title: "区间",
              render: (_, r) => `${r.start_at || "目录"} — ${r.end_at || ""}`,
            },
            {
              title: "状态",
              render: (_, r) => labels[String(r.status)] ?? String(r.status),
            },
            { title: "请求次数", dataIndex: "attempts" },
            { title: "原因", dataIndex: "error" },
            {
              title: "查看",
              render: (_, r) => (
                <Space>
                  <Button
                    size="small"
                    onClick={async () => {
                      try {
                        setDetail(
                          await fetchQuery(
                            query(`/api/sync/jobs/${r.request_id}`),
                          ),
                        );
                      } catch (e) {
                        message.error((e as Error).message);
                      }
                    }}
                  >
                    记录
                  </Button>
                  {!!r.receipt_id && (
                    <Button
                      size="small"
                      onClick={async () => {
                        try {
                          setDetail(
                            await fetchQuery(
                              query(`/api/sync/receipts/${r.receipt_id}`),
                            ),
                          );
                        } catch (e) {
                          message.error((e as Error).message);
                        }
                      }}
                    >
                      固定数据
                    </Button>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </Card>
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
            <Evidence value={detail} />
          </>
        )}
      </Modal>
    </>
  );
}
