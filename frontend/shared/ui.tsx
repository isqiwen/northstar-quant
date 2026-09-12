"use client";
import { useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Input,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import Link from "next/link";
import { acknowledge, pendingCommand, read, type RecordValue } from "./api";
export const short = (v: unknown) =>
  String(v ?? "—").length > 24
    ? String(v).slice(0, 12) + "…"
    : String(v ?? "—");
export const show = (v: unknown): string =>
  v === null || v === undefined
    ? "—"
    : typeof v === "object"
      ? JSON.stringify(v)
      : String(v);
export function Identity({ value, to }: { value: unknown; to?: string }) {
  return (
    <Typography.Text code copyable={{ text: String(value) }}>
      {to ? (
        <Link href={to} title={String(value)}>
          {short(value)}
        </Link>
      ) : (
        <span title={String(value)}>{short(value)}</span>
      )}
    </Typography.Text>
  );
}
export function Status({ value }: { value: unknown }) {
  const s = String(value ?? "UNKNOWN");
  return (
    <Tag
      color={
        /SUCCEEDED|PUBLISHED|READY|AVAILABLE|OK|COMPLETE/.test(s)
          ? "green"
          : /FAIL|REJECT|UNKNOWN|UNAVAILABLE|STOP/.test(s)
            ? "red"
            : "gold"
      }
    >
      {s}
    </Tag>
  );
}
export function Heading({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <Typography.Title level={2}>{title}</Typography.Title>
        <Typography.Paragraph type="secondary">
          {description}
        </Typography.Paragraph>
      </div>
      <Space wrap>{actions}</Space>
    </div>
  );
}
export function Evidence({
  value,
  title = "完整证据与固定输入",
}: {
  value: unknown;
  title?: string;
}) {
  return (
    <Collapse
      className="evidence"
      items={[
        {
          key: "evidence",
          label: title,
          children: <pre>{JSON.stringify(value, null, 2)}</pre>,
        },
      ]}
    />
  );
}
export function Failure({ error }: { error?: Error }) {
  return error ? (
    <Alert
      type="error"
      showIcon
      title="数据不可用"
      description={error.message}
    />
  ) : null;
}
export function Records({
  rows = [],
  columns,
  loading = false,
  rowKey,
  title,
}: {
  rows?: RecordValue[];
  columns: ColumnsType<RecordValue>;
  loading?: boolean;
  rowKey: string;
  title?: string;
}) {
  const [filter, setFilter] = useState("");
  return (
    <Card
      title={title}
      extra={
        <Input.Search
          aria-label={`${title || "记录"}筛选`}
          placeholder="搜索当前记录"
          allowClear
          onChange={(e) => setFilter(e.target.value)}
          style={{ width: 220 }}
        />
      }
    >
      <Table<RecordValue>
        size="middle"
        rowKey={rowKey}
        loading={loading}
        columns={columns}
        dataSource={rows.filter((r) =>
          JSON.stringify(r).toLowerCase().includes(filter.toLowerCase()),
        )}
        pagination={{ pageSize: 8, showSizeChanger: true }}
        scroll={{ x: 650 }}
        locale={{ emptyText: <Empty description="暂无记录" /> }}
      />
    </Card>
  );
}
export function Fields({ value }: { value: RecordValue }) {
  return (
    <div className="facts">
      {Object.entries(value).map(([k, v]) => (
        <div key={k}>
          <span>{k}</span>
          <strong>{show(v)}</strong>
        </div>
      ))}
    </div>
  );
}
export function PendingNotice({
  lookup,
}: {
  lookup?: (id: string) => Promise<unknown>;
}) {
  const [pending, setPending] =
    useState<ReturnType<typeof pendingCommand>>(null);
  const [result, setResult] = useState<unknown>();
  const [error, setError] = useState<Error>();
  useEffect(() => {
    const update = () => setPending(pendingCommand());
    update();
    window.addEventListener("command-change", update);
    return () => window.removeEventListener("command-change", update);
  }, []);
  if (!pending) return null;
  return (
    <Card className="pending">
      <Alert
        showIcon
        type="warning"
        title={
          pending.status === "SENDING" ? "操作已提交，等待确认" : "操作结果未知"
        }
        description="刷新页面不会重新提交。核查固定记录后才能发起其他操作。"
      />
      <p>
        命令身份：
        <Identity value={pending.id} />
      </p>
      <Space>
        {pending.runtime && lookup && (
          <Button
            onClick={() => lookup(pending.id).then(setResult).catch(setError)}
          >
            查询固定命令
          </Button>
        )}
        <Button
          disabled={pending.status === "SENDING"}
          onClick={() => {
            acknowledge();
            setResult(undefined);
          }}
        >
          已核查记录，解除本页操作锁
        </Button>
      </Space>
      <Failure error={error} />
      {result !== undefined && <Evidence value={result} title="命令查询结果" />}
    </Card>
  );
}
