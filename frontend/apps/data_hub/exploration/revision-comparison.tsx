"use client";
import { Alert, App, Button, Modal, Space, Table } from "antd";
import { useState } from "react";
import { mutate } from "../api/client";
import type { RevisionComparison } from "../api/generated";
type Row = Record<string, unknown>;
const kinds: Record<string, string> = {
  added: "新增",
  removed: "删除",
  changed: "修改",
};
export function RevisionCompare({ selected }: { selected: string[] }) {
  const { message } = App.useApp();
  const [result, setResult] = useState<RevisionComparison>();
  const [busy, setBusy] = useState(false);
  async function load(before: string, after: string, offset: number) {
    setBusy(true);
    try {
      setResult(
        await mutate("/api/explorer/compare", {
          before_id: before,
          after_id: after,
          offset,
        }),
      );
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Space>
        <Button
          disabled={selected.length !== 2}
          loading={busy}
          onClick={() => void load(selected[0], selected[1], 0)}
        >
          比较所选版本
        </Button>
        <span>依次选择基准版本、对照版本；仅比较同一请求分片。</span>
      </Space>
      <Modal
        title="固定版本修订差异"
        open={!!result}
        onCancel={() => setResult(undefined)}
        footer={null}
        width={1100}
      >
        {result && (
          <>
            <p>
              基准：{String(result.before.receipt_id)} → 对照：
              {String(result.after.receipt_id)}
            </p>
            <p>
              新增 {String(result.counts.added)} 行，删除{" "}
              {String(result.counts.removed)} 行，修改{" "}
              {String(result.counts.changed)} 行，未变{" "}
              {String(result.counts.unchanged)} 行
            </p>
            <p>
              原文{result.source_changed ? "不同" : "相同"}；处理规则
              {result.rules_changed ? "不同" : "相同"}。比较身份：
              {result.comparison_id}
            </p>
            <Alert type="info" message={result.note} />
            <Table<Row>
              size="small"
              loading={busy}
              dataSource={result.changes}
              rowKey={(_, i) => String(i)}
              scroll={{ x: 900 }}
              pagination={{
                current: result.offset / 100 + 1,
                pageSize: 100,
                total: result.total,
                showSizeChanger: false,
                onChange: (p) =>
                  void load(
                    String(result.before.receipt_id),
                    String(result.after.receipt_id),
                    (p - 1) * 100,
                  ),
              }}
              columns={[
                {
                  title: "记录身份",
                  render: (_, r) => JSON.stringify(r.identity),
                },
                { title: "变化", render: (_, r) => kinds[String(r.kind)] },
                { title: "字段", dataIndex: "field" },
                {
                  title: "基准值",
                  render: (_, r) =>
                    !r.before_present
                      ? "字段不存在"
                      : r.before == null
                        ? "空值"
                        : String(r.before),
                },
                {
                  title: "对照值",
                  render: (_, r) =>
                    !r.after_present
                      ? "字段不存在"
                      : r.after == null
                        ? "空值"
                        : String(r.after),
                },
                {
                  title: "展示",
                  render: (_, r) => (r.preview ? "长文本预览" : "完整值"),
                },
              ]}
            />
          </>
        )}
      </Modal>
    </>
  );
}
