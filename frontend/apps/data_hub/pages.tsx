"use client";
import { query } from "./api/client";
import { Button, Card, Tabs } from "antd";
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
export { DataOverview as DataHome } from "./exploration/home";
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
        </>
      )}
    </>
  );
}
