"use client";
import { useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Drawer,
  Input,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import { mutate } from "./api/client";
import type { SeriesRows } from "./api/generated";
import { Failure, Heading } from "../../shared/ui";
import { CatalogSnapshot } from "./catalog-snapshot";

const labels: Record<string, string> = {
  continuous: "连续日线",
  mapping: "连续合约映射",
  adjusted: "复权日线",
  index: "南华指数",
};
type Row = Record<string, unknown>;
export function ResearchSeries() {
  const { message } = App.useApp();
  const [filter, setFilter] = useState({ dataset: "", search: "", offset: 0 });
  const [history, setHistory] = useState<{
    dataset: string;
    scope: string;
    offset: number;
  }>();
  const [data, setData] = useState<SeriesRows>();
  const [versions, setVersions] = useState<SeriesRows>();
  const [error, setError] = useState<Error>();
  const [historyError, setHistoryError] = useState<Error>();
  const [snapshot, setSnapshot] = useState<string>();
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setData(undefined);
    setError(undefined);
    const poll = async () => {
      try {
        const value = await mutate("/api/series/query", filter);
        if (active) {
          setData(value);
          setError(undefined);
        }
      } catch (failure) {
        if (active) setError(failure as Error);
      } finally {
        if (active) timer = setTimeout(poll, 3000);
      }
    };
    timer = setTimeout(poll, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [filter]);
  useEffect(() => {
    let active = true;
    setVersions(undefined);
    setHistoryError(undefined);
    if (history)
      void mutate("/api/series/versions", history)
        .then((value) => {
          if (active) setVersions(value);
        })
        .catch((e: Error) => {
          if (active) setHistoryError(e);
        });
    return () => {
      active = false;
    };
  }, [history]);
  async function retry(row: Row) {
    try {
      await mutate("/api/series/retry", {
        dataset: String(row.dataset),
        scope: String(row.scope),
      });
      void message.success("已安排重新处理固定响应");
      setFilter((f) => ({ ...f }));
    } catch (e) {
      void message.error((e as Error).message);
    }
  }
  return (
    <>
      <Heading
        title="研究序列"
        description="连续、映射、复权和指数保留独立身份。查看已校验记录的固定区间版本；它们不计入完整合约，也不作为实际成交价。"
      />
      <Card>
        <Space wrap style={{ marginBottom: 16 }}>
          <Select
            aria-label="研究序列类型"
            style={{ width: 180 }}
            value={filter.dataset}
            options={[
              { value: "", label: "全部类型" },
              ...Object.entries(labels).map(([value, label]) => ({
                value,
                label,
              })),
            ]}
            onChange={(dataset) =>
              setFilter((f) => ({ ...f, dataset, offset: 0 }))
            }
          />
          <Input
            aria-label="搜索研究序列"
            placeholder="中文名称 / 序列代码"
            value={filter.search}
            onChange={(e) =>
              setFilter((f) => ({ ...f, search: e.target.value, offset: 0 }))
            }
            allowClear
          />
        </Space>
        <Failure error={error} />
        <Table<Row>
          rowKey={(r) => `${r.dataset}:${r.scope}`}
          dataSource={data?.rows || []}
          loading={!data && !error}
          locale={{ emptyText: "正在发现研究序列；请确认历史同步已启用。" }}
          pagination={{
            current: filter.offset / 20 + 1,
            pageSize: 20,
            total: data?.total || 0,
            showSizeChanger: false,
            onChange: (page) =>
              setFilter((f) => ({ ...f, offset: (page - 1) * 20 })),
          }}
          columns={[
            { title: "类型", render: (_, r) => labels[String(r.dataset)] },
            {
              title: "研究序列",
              render: (_, r) => (
                <>
                  <strong>{String(r.name)}</strong>
                  <div className="muted">{String(r.scope)}</div>
                </>
              ),
            },
            {
              title: "最新固定区间",
              render: (_, r) =>
                r.snapshot_id ? `${r.start_date} — ${r.end_date}` : "尚无发布",
            },
            {
              title: "采集与处理",
              render: (_, r) => (
                <Space wrap>
                  {Number(r.collecting) > 0 && <Tag color="blue">采集中</Tag>}
                  {Number(r.waiting) > 0 && <Tag>部分区间待补齐</Tag>}
                  {Number(r.blocked) > 0 && (
                    <Tag color="red">部分区间需处理</Tag>
                  )}
                  {!!r.snapshot_id && <Tag color="green">有已校验记录</Tag>}
                  {!!r.error && <span>{String(r.error)}</span>}
                </Space>
              ),
            },
            {
              title: "操作",
              render: (_, r) => (
                <Space wrap>
                  <Button
                    type="link"
                    disabled={!r.snapshot_id}
                    onClick={() => setSnapshot(String(r.snapshot_id))}
                  >
                    查看标准数据
                  </Button>
                  <Button
                    type="link"
                    disabled={!r.snapshot_id}
                    onClick={() =>
                      setHistory({
                        dataset: String(r.dataset),
                        scope: String(r.scope),
                        offset: 0,
                      })
                    }
                  >
                    历史版本
                  </Button>
                  {!!r.error && (
                    <Button onClick={() => void retry(r)}>重新处理</Button>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Drawer
        title="研究序列固定版本"
        open={!!history}
        onClose={() => setHistory(undefined)}
        size="large"
      >
        <Failure error={historyError} />
        <Table<Row>
          rowKey="snapshot_id"
          dataSource={versions?.rows || []}
          loading={!versions && !historyError}
          pagination={{
            current: (history?.offset || 0) / 20 + 1,
            pageSize: 20,
            total: versions?.total || 0,
            showSizeChanger: false,
            onChange: (page) =>
              setHistory((h) => (h ? { ...h, offset: (page - 1) * 20 } : h)),
          }}
          columns={[
            {
              title: "区间",
              render: (_, r) => `${r.start_date} — ${r.end_date}`,
            },
            { title: "固定时间", dataIndex: "created_at" },
            {
              title: "查看",
              render: (_, r) => (
                <Button onClick={() => setSnapshot(String(r.snapshot_id))}>
                  打开固定版本
                </Button>
              ),
            },
          ]}
        />
      </Drawer>
      <CatalogSnapshot id={snapshot} onClose={() => setSnapshot(undefined)} />
    </>
  );
}
