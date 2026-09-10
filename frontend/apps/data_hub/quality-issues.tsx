"use client";
import { Card, Table } from "antd";

type Row = Record<string, unknown>;
export function QualityIssues({ attempts }: { attempts: Row[] }) {
  return attempts
    .filter((a) => a.quality)
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
                title: "冲突原文行",
                render: (_, r) => String(r.related_row_number ?? "—"),
              },
            ]}
          />
        </Card>
      );
    });
}
