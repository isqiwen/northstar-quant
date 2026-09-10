"use client";
import { requestId } from "../../shared/api";
import { query, mutate } from "./api/client";
import { useState } from "react";
import { App, Button, Card, Upload, Alert } from "antd";
import Link from "next/link";
import { useParams } from "next/navigation";
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
import { useRuntime } from "./runtime";
export function Overview() {
  const runtime = useRuntime();
  const streams = useData(query("/api/streams"), 5000);
  return (
    <>
      <Heading
        title="Live 运行概览"
        description="管理 Web 与交易内核独立运行，状态由内核提供。"
        actions={<Button onClick={runtime.refresh}>刷新观察</Button>}
      />
      <Card>
        <Fields
          value={{
            内核状态: runtime.available ? "AVAILABLE" : "UNAVAILABLE",
            运行身份: runtime.id,
            发单能力:
              runtime.data?.order_sending === false ? "未启用" : "未确认",
            撤单能力:
              runtime.data?.cancel_sending === false ? "未启用" : "未确认",
          }}
        />
      </Card>
      <Alert
        type="info"
        showIcon
        title="当前为无发送管理与影子观察"
        description="页面访问、材料接收和影子控制都不授予交易执行权限。"
      />
      <Records
        title="持续接收会话"
        rows={streams.data}
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
      <Evidence value={runtime.data} title="运行身份与最近观察" />
    </>
  );
}
export function Diagnostics() {
  const q = useData(query("/api/live/diagnostics"), 10000);
  return (
    <>
      <Heading
        title="运行诊断"
        description="数据库与来源存储的实际观察；健康状态不等于可交易。"
        actions={<Button onClick={q.refresh}>重新检查</Button>}
      />
      <Failure error={q.error} />
      {q.data && (
        <>
          <Card>
            <Fields
              value={{
                检查状态: q.data.status,
                数据库: q.data.database?.status,
                数据库盘: q.data.database?.disk_capacity,
                数据库盘空闲字节: q.data.database?.free_bytes,
                数据库字节: q.data.database?.database_bytes,
                WAL字节: q.data.database?.wal_bytes,
                来源盘: q.data.source_filesystem?.status,
                来源盘空闲字节: q.data.source_filesystem?.free_bytes,
                检查时间: q.data.observed_at,
              }}
            />
          </Card>
          <Evidence value={q.data} />
        </>
      )}
    </>
  );
}
export function Materials() {
  const q = useData(query("/api/strategy-materials"));
  const runtime = useRuntime();
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Heading
        title="固定策略材料"
        description="接收完整候选到 Live 本地，核验代码身份；不会启用生产实例。"
      />
      <Failure error={q.error} />
      <Card title="接收候选">
        <Upload
          accept=".json"
          showUploadList={false}
          disabled={!runtime.canControl || busy}
          beforeUpload={async (file) => {
            if (!runtime.canControl || !runtime.id) return false;
            setBusy(true);
            try {
              if (file.size > 4_000_000) throw new Error("候选超过 4 MB");
              const candidate = JSON.parse(await file.text());
              await mutate(
                "/api/strategy-materials",
                { candidate, request_id: requestId() },
                runtime.id,
              );
              q.refresh();
              message.success("候选已接收，未授予执行权限");
            } catch (e) {
              message.error((e as Error).message);
            } finally {
              setBusy(false);
            }
            return false;
          }}
        >
          <Button loading={busy} disabled={!runtime.canControl}>
            选择并核验候选文件
          </Button>
        </Upload>
      </Card>
      <Records
        title="本地材料"
        rowKey="candidate_id"
        rows={q.data}
        columns={[
          {
            title: "候选",
            dataIndex: "candidate_id",
            render: (v) => <Identity value={v} />,
          },
          {
            title: "状态",
            dataIndex: "status",
            render: (v) => <Status value={v} />,
          },
          {
            title: "执行授权",
            dataIndex: "execution_authorized",
            render: (v) => (v ? "已授权" : "未授权"),
          },
        ]}
      />
      <Evidence value={q.data} />
    </>
  );
}
export function LiveRecord({
  endpoint,
  title,
}: {
  endpoint:
    | "/api/live/commands"
    | "/api/broker/opening-budgets"
    | "/api/broker/funds-entries";
  title: string;
}) {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`${endpoint}/${id}`));
  return (
    <>
      <Heading title={title} description="固定记录只读核查，不重新提交操作。" />
      <Failure error={q.error} />
      {q.data && (
        <>
          <Card>
            <Fields value={{ 记录: id, 状态: q.data.status }} />
          </Card>
          <Evidence value={q.data} />
        </>
      )}
    </>
  );
}
