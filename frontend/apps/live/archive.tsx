"use client";
import { query, mutate } from "./api/client";
import { useState } from "react";
import { App, Button, Card, Form } from "antd";
import Link from "next/link";
import { useRouter, useParams } from "next/navigation";
import { useData } from "../../shared/data";
import { SourceFields } from "../../shared/source-form";
import { Evidence, Failure, Fields, Heading } from "../../shared/ui";
import { useRuntime } from "./runtime";
export function ArchiveSource() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/sources/${id}`));
  return (
    <>
      <Heading
        title="Live 来源原文"
        description="从内核读取已保存来源，管理端不访问文件系统。"
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
export function ArchiveAttempt() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/attempts/${id}`));
  const runtime = useRuntime();
  const [busy, setBusy] = useState(false);
  const navigate = useRouter().push;
  const { message } = App.useApp();
  return (
    <>
      <Heading
        title="Live 归档尝试"
        description="固定前缀归档结果；重处理保持同一来源和权限。"
      />
      <Failure error={q.error} />
      {q.data && (
        <>
          <Card>
            <Fields
              value={{
                状态: q.data.status,
                来源: q.data.source_id,
                快照: q.data.snapshot_id,
              }}
            />
            <p>
              <Link href={`/sources/${q.data.source_id}`}>查看原文来源</Link>
              {q.data.snapshot_id && (
                <>
                  {" "}
                  ·{" "}
                  <Link href={`/datasets/${q.data.snapshot_id}`}>
                    查看固定快照
                  </Link>
                </>
              )}
            </p>
          </Card>
          <Evidence value={q.data} />
          <Card title="重新处理归档">
            <Form
              key={id}
              layout="vertical"
              initialValues={{
                spec: q.data.parameters?.spec || q.data.parameters,
              }}
              onFinish={async (v) => {
                if (!runtime.canControl || !runtime.id) return;
                setBusy(true);
                try {
                  const result = await mutate(
                    `/api/sources/${q.data!.source_id}/reprocess`,
                    { spec: v.spec, request_id: crypto.randomUUID() },
                    runtime.id,
                  );
                  navigate(`/attempts/${result.attempt_id}`);
                } catch (e) {
                  message.error((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <SourceFields />
              <Button
                htmlType="submit"
                disabled={!runtime.canControl}
                loading={busy}
              >
                创建新归档处理
              </Button>
            </Form>
          </Card>
        </>
      )}
    </>
  );
}
