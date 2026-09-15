"use client";
import { Card, Table } from "antd";

type Row = Record<string, unknown>;
export function QualityIssues({ attempts }: { attempts: Row[] }) {
  return attempts
    .filter((a) => (a.quality as Row | undefined)?.issue_count != null)
    .map((attempt) => {
      const report = attempt.quality as Row;
      return (
        <Card
          key={String(attempt.generation)}
          size="small"
          title={`质量问题 · ${attempt.started_at}`}
        >
          <p>
            规则：{String(report.rule)}；问题数：{String(report.issue_count)}
          </p>
          <p>
            {String(report.policy)}
            {report.truncated ? "（明细已截断）" : ""}
          </p>
          {attempt.source_release_reason ? (
            <p>
              原始文件已清理：{String(attempt.source_release_reason)}
              。下表为当次校验记录，不是完整原文。
            </p>
          ) : null}
          <Table<Row>
            size="small"
            dataSource={(report.issues ?? []) as Row[]}
            rowKey={(_, i) => String(i)}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            columns={[
              {
                title: "原文行号",
                render: (_, r) =>
                  r.row_number == null ? "整个响应" : String(r.row_number),
              },
              {
                title: "字段",
                render: (_, r) =>
                  ((r.fields ?? []) as string[]).join("、") || "响应",
              },
              { title: "原因", dataIndex: "reason" },
              {
                title: "源响应值（校验前）",
                render: (_, r) => {
                  const values = r.observed as Row | undefined;
                  return values && Object.keys(values).length
                    ? Object.entries(values).map(([key, value]) => (
                        <div key={key}>
                          {key}: {value == null ? "空值" : String(value)}
                        </div>
                      ))
                    : "该次报告未保存字段值，需核对原文或重新下载";
                },
              },
              {
                title: "冲突原文行",
                render: (_, r) => String(r.related_row_number ?? "—"),
              },
            ]}
          />
        </Card>
      );
    });
}
