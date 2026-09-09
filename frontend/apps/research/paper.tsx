"use client";
import { requestId as newRequestId } from "../../shared/api";
import { query, mutate } from "./api/client";
import { useState } from "react";
import { App, Button, Card, Form, Progress } from "antd";
import { useRouter, useParams } from "next/navigation";
import { useData } from "../../shared/data";
import { ConfigurationSelect, SnapshotSelect } from "../../shared/forms";
import {
  Evidence,
  Failure,
  Fields,
  Heading,
  Identity,
  Records,
  Status,
} from "../../shared/ui";
export function Paper() {
  const ds = useData(query("/api/datasets"));
  const cfg = useData(query("/api/configurations"));
  const q = useData(query("/api/paper"));
  const navigate = useRouter().push;
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Heading
        title="文件 Paper"
        description="基于固定文件的内部模拟，与柜台仿真及实盘分别解释。"
      />
      <Failure error={q.error} />
      <Card title="创建暂停会话">
        <Form
          layout="vertical"
          onFinish={async (v) => {
            setBusy(true);
            try {
              const result = await mutate("/api/paper", {
                ...v,
                request_id: newRequestId(),
              });
              navigate(`/paper/${result.session_id}`);
            } catch (e) {
              message.error((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="grid-two">
            <SnapshotSelect rows={ds.data || []} />
            <ConfigurationSelect rows={cfg.data || []} />
          </div>
          <Button type="primary" htmlType="submit" loading={busy}>
            创建文件 Paper
          </Button>
        </Form>
      </Card>
      <Records
        title="会话记录"
        rows={q.data}
        rowKey="session_id"
        columns={[
          {
            title: "会话",
            dataIndex: "session_id",
            render: (v) => <Identity value={v} to={`/paper/${v}`} />,
          },
          {
            title: "状态",
            dataIndex: "status",
            render: (v) => <Status value={v} />,
          },
          { title: "进度", dataIndex: "cursor" },
          { title: "配置", dataIndex: "configuration", render: (v) => v?.name },
        ]}
      />
    </>
  );
}
export function PaperDetail() {
  const { id } = useParams<{ id: string }>();
  const q = useData(query(`/api/paper/${id}`));
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Heading
        title="Paper 会话"
        description="每次推进使用一个固定命令身份，页面重启不会推进或重建账户。"
      />
      <Failure error={q.error} />
      {q.data && (
        <>
          <Card>
            <Fields
              value={{
                状态: q.data.status,
                已处理: q.data.cursor,
                权益: q.data.summary?.ending_equity,
                持仓: q.data.summary?.ending_position_lots,
              }}
            />
            <p>
              <Button
                type="primary"
                disabled={!!q.error || q.data.status === "COMPLETED"}
                loading={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await mutate(`/api/paper/${id}/advance`, {
                      request_id: newRequestId(),
                    });
                    q.refresh();
                  } catch (e) {
                    message.error((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                推进下一条观察
              </Button>
            </p>
          </Card>
          <Evidence value={q.data} title="固定配置、账户与步骤证据" />
        </>
      )}
    </>
  );
}
