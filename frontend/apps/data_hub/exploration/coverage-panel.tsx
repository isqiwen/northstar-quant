"use client";
import { useState } from "react";
import { Alert, Button, Card, Space, Table, Tag } from "antd";
import Link from "next/link";
import type { ExplorerCoverage, ExplorerRange } from "../api/generated";
import { states, scopeUrl } from "./states";
type Row = Record<string, unknown>;
export function CoveragePanel({
  coverage,
  linkRange,
}: {
  coverage: ExplorerCoverage;
  linkRange: ExplorerRange;
}) {
  const [day, setDay] = useState("");
  const jobs = coverage.jobs.filter(
    (j) => !day || (String(j.start_at) <= day && String(j.end_at) >= day),
  );
  return (
    <>
      <Alert
        type="info"
        showIcon
        message="覆盖依据"
        description={coverage.note}
      />
      <Card title="自然日期覆盖 · 点击日期查看任务">
        <div className="coverage-days">
          {coverage.days.map((d) => {
            const [label, color] = states[String(d.state)] || [
              String(d.state),
              "default",
            ];
            return (
              <button
                type="button"
                className={day === d.date ? "active" : ""}
                key={String(d.date)}
                onClick={() => setDay(String(d.date))}
              >
                <strong>{String(d.date)}</strong>
                <Tag color={color}>{label}</Tag>
                <small>
                  {d.calendar_open == null
                    ? "日历待核对"
                    : d.calendar_open
                      ? "日历开市"
                      : "日历休市"}
                </small>
              </button>
            );
          })}
        </div>
      </Card>
      <Card
        title={day ? `${day} · 对应任务与异常` : "范围内同步任务"}
        extra={
          day && (
            <Space>
              <Button onClick={() => setDay("")}>查看全部</Button>
              <Link
                href={scopeUrl("/browse", {
                  ...linkRange,
                  start: day,
                  end: day,
                })}
              >
                查看当日数据
              </Link>
            </Space>
          )
        }
      >
        <Table<Row>
          rowKey="request_id"
          dataSource={jobs}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: "开始", dataIndex: "start_at" },
            { title: "结束", dataIndex: "end_at" },
            {
              title: "状态",
              render: (_, r) => (
                <Tag color={states[String(r.status)]?.[1]}>
                  {states[String(r.status)]?.[0] || String(r.status)}
                </Tag>
              ),
            },
            { title: "原因", dataIndex: "error" },
            {
              title: "证据",
              render: (_, r) => (
                <Link href={`/sync?request=${r.request_id}`}>查看同步任务</Link>
              ),
            },
          ]}
        />
      </Card>
    </>
  );
}
