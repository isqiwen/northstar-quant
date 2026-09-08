"use client";
import { query, mutate } from "./api/client";
import { useState } from "react";
import {
  App,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  Spin,
  Tabs,
  Upload,
} from "antd";
import { CloudUploadOutlined } from "@ant-design/icons";
import Link from "next/link";
import { useRouter, useParams } from "next/navigation";
import { useData } from "../../shared/data";
import { datasetColumns } from "../../shared/datasets";
import {
  Evidence,
  Failure,
  Fields,
  Heading,
  Identity,
  Records,
  Status,
} from "../../shared/ui";
import { SourceFields } from "../../shared/source-form";
export function DataHome() {
  const ds = useData(query("/api/datasets"));
  const sources = useData(query("/api/sources"));
  const attempts = useData(query("/api/attempts"), 5000);
  return (
    <>
      <Heading
        title="数据管理中心"
        description="保留来源原文，检查数据质量，发布固定快照。"
        actions={
          <Link href="/import">
            <Button type="primary" icon={<CloudUploadOutlined />}>
              导入数据
            </Button>
          </Link>
        }
      />
      <Failure error={ds.error || sources.error || attempts.error} />
      <Card>
        <Fields
          value={{
            已发布快照: ds.data?.length ?? "—",
            归档来源: sources.data?.length ?? "—",
            处理尝试: attempts.data?.length ?? "—",
          }}
        />
      </Card>
      <Records
        title="已发布数据"
        rows={ds.data}
        loading={ds.loading}
        columns={datasetColumns}
        rowKey="snapshot_id"
      />
      <Records
        title="最近处理"
        rows={attempts.data}
        rowKey="attempt_id"
        columns={attemptColumns}
      />
    </>
  );
}
const attemptColumns = [
  {
    title: "处理尝试",
    dataIndex: "attempt_id",
    render: (v: string) => <Identity value={v} to={`/attempts/${v}`} />,
  },
  {
    title: "状态",
    dataIndex: "status",
    render: (v: string) => <Status value={v} />,
  },
  { title: "创建时间", dataIndex: "created_at" },
  {
    title: "来源",
    dataIndex: "source_id",
    render: (v: string) => <Identity value={v} to={`/sources/${v}`} />,
  },
];
export function Sources() {
  const q = useData(query("/api/sources"));
  const attempts = useData(query("/api/attempts"));
  const rejected = useData(query("/api/rejections"));
  return (
    <>
      <Heading
        title="来源与处理"
        description="原文、处理尝试与拒绝记录分别保留。"
      />
      <Failure error={q.error || attempts.error || rejected.error} />
      <Tabs
        items={[
          {
            key: "sources",
            label: "来源归档",
            children: (
              <Records
                title="来源"
                rowKey="source_id"
                rows={q.data}
                columns={[
                  { title: "文件", dataIndex: "filename" },
                  { title: "来源", dataIndex: "source_name" },
                  {
                    title: "来源身份",
                    dataIndex: "source_id",
                    render: (v) => <Identity value={v} to={`/sources/${v}`} />,
                  },
                  {
                    title: "原文状态",
                    dataIndex: "file_status",
                    render: (v) => <Status value={v} />,
                  },
                ]}
              />
            ),
          },
          {
            key: "attempts",
            label: "处理尝试",
            children: (
              <Records
                rowKey="attempt_id"
                rows={attempts.data}
                columns={attemptColumns}
              />
            ),
          },
          {
            key: "rejected",
            label: "接收拒绝",
            children: (
              <Records
                rowKey="rejection_id"
                rows={rejected.data}
                columns={[
                  {
                    title: "拒绝记录",
                    dataIndex: "rejection_id",
                    render: (v) => <Identity value={v} />,
                  },
                  { title: "原因", dataIndex: "reason" },
                  { title: "时间", dataIndex: "created_at" },
                ]}
              />
            ),
          },
        ]}
      />
    </>
  );
}
export function Import() {
  const [file, setFile] = useState<File>();
  const [busy, setBusy] = useState(false);
  const { message } = App.useApp();
  const navigate = useRouter().push;
  return (
    <>
      <Heading
        title="导入行情原文"
        description="上传 CSV，固定来源与时间语义。权限检查和发布由数据业务执行。"
      />
      <Card title="选择文件">
        <Upload.Dragger
          accept=".csv"
          maxCount={1}
          beforeUpload={(f) => {
            if (f.size > 5 * 1024 * 1024) {
              message.error("原文件最多 5 MiB");
              return Upload.LIST_IGNORE;
            }
            setFile(f);
            return false;
          }}
          onRemove={() => setFile(undefined)}
        >
          <p className="ant-upload-drag-icon">
            <CloudUploadOutlined />
          </p>
          <p>点击或拖入 CSV 文件</p>
          <p className="muted">最多 5 MiB · 原文字节与处理结果分别归档</p>
        </Upload.Dragger>
      </Card>
      <Form
        layout="vertical"
        initialValues={{
          spec: { timezone: "Asia/Shanghai", currency: "CNY" },
          allow_retention: false,
          allow_download: false,
        }}
        onFinish={async (v) => {
          if (!file) {
            message.error("请选择 CSV 文件");
            return;
          }
          setBusy(true);
          try {
            const bytes = new Uint8Array(await file.arrayBuffer());
            let binary = "";
            for (const byte of bytes) binary += String.fromCharCode(byte);
            const result = await mutate("/api/import", {
              ...v,
              filename: file.name,
              content_base64: btoa(binary),
              source_name: v.spec.source_name,
              input_kind: "RECEIVED_CSV",
              upstream_source_id: null,
              transformation_note: null,
              request_id: crypto.randomUUID(),
            });
            navigate(`/attempts/${result.attempt_id}`);
          } catch (e) {
            message.error((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Card title="合约、来源与时间口径">
          <SourceFields />
        </Card>
        <Card title="使用与保留许可">
          <Form.Item
            name="use_basis"
            label="用途与留存依据"
            rules={[{ required: true }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="allow_retention" valuePropName="checked">
            <Checkbox>确认有权留存并用于研究和备份</Checkbox>
          </Form.Item>
          <Form.Item name="allow_download" valuePropName="checked">
            <Checkbox>允许本机下载</Checkbox>
          </Form.Item>
        </Card>
        <Button type="primary" htmlType="submit" loading={busy}>
          接收并排队检查
        </Button>
      </Form>
    </>
  );
}
export function SourceDetail() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/sources/${id}`));
  return (
    <>
      <Heading
        title={q.data?.filename || "来源详情"}
        description="原文留存、许可与处理过程均可追溯。"
      />
      <Failure error={q.error} />
      {q.data && (
        <>
          <Card>
            <Fields
              value={{
                文件: q.data.filename,
                来源: q.data.source_name,
                原文状态: q.data.file_status,
                来源身份: id,
              }}
            />
            <p>
              <Button
                href={`/api/sources/${id}/download`}
                disabled={!q.data.allow_download}
              >
                下载归档原文
              </Button>
            </p>
          </Card>
          <Evidence value={q.data} />
        </>
      )}
    </>
  );
}
export function Attempt() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/attempts/${id}`), 1500);
  const { message } = App.useApp();
  const navigate = useRouter().push;
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Heading
        title="处理尝试"
        description="接收后由独立执行器加工，关闭页面不停止任务。排队、成功与失败会自动刷新；中断后请明确重新处理。"
      />
      <Failure error={q.error} />
      {q.data && (
        <>
          <Card>
            <Fields
              value={{
                状态: q.data.status,
                快照: q.data.snapshot_id,
                来源: q.data.source_id,
                原因: q.data.error || q.data.reason,
              }}
            />
            <p>
              <Link href={`/sources/${q.data.source_id}`}>查看原文来源</Link>
              {q.data.snapshot_id && (
                <>
                  {" "}
                  ·{" "}
                  <Link href={`/datasets/${q.data.snapshot_id}`}>
                    查看已发布快照
                  </Link>
                </>
              )}
            </p>
          </Card>
          <Evidence value={q.data} />
          <Card title="用相同原文重新处理">
            <Form
              key={id}
              layout="vertical"
              initialValues={{
                spec:
                  q.data.spec ||
                  q.data.parameters?.spec ||
                  q.data.parameters ||
                  {},
              }}
              onFinish={async (v) => {
                setBusy(true);
                try {
                  const r = await mutate(
                    `/api/sources/${q.data!.source_id}/reprocess`,
                    { spec: v.spec, request_id: crypto.randomUUID() },
                  );
                  navigate(`/attempts/${r.attempt_id}`);
                } catch (e) {
                  message.error((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <SourceFields />
              <Button htmlType="submit" loading={busy}>
                创建新处理尝试
              </Button>
            </Form>
          </Card>
        </>
      )}
    </>
  );
}
