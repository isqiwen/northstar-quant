"use client";
import {
  Alert,
  Button,
  Card,
  Col,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
} from "antd";
import Link from "next/link";
import { useData } from "../../../shared/data";
import { Failure, Heading } from "../../../shared/ui";
import { query } from "../api/client";
export function DataOverview() {
  const catalog = useData(query("/api/explorer"), 10000);
  const sync = useData(query("/api/sync"), 5000);
  const available = !!catalog.data && !catalog.error && !catalog.loading;
  const datasets = catalog.error ? [] : catalog.data?.datasets || [];
  const count = (field: string) =>
    datasets.reduce((n, r) => n + Number(r[field] || 0), 0);
  return (
    <>
      <Heading
        title="期货数据工作台"
        description="Tushare 历史数据 · 从覆盖概况到精确记录"
        actions={
          <Link href="/browse">
            <Button type="primary">浏览行情数据</Button>
          </Link>
        }
      />
      <Failure error={catalog.error || sync.error} />
      <Row gutter={[16, 16]}>
        {[
          [
            "已发现合约",
            (catalog.data?.products || []).reduce(
              (n, r) => n + Number(r.contracts),
              0,
            ),
          ],
          ["已校验分片", count("validated")],
          ["等待发布或重试", count("waiting")],
          ["异常分片", count("blocked")],
        ].map(([title, value]) => (
          <Col xs={12} xl={6} key={title}>
            <Card>
              <Statistic title={title} value={available ? value : "—"} />
            </Card>
          </Col>
        ))}
      </Row>
      <Card className="explorer-status">
        <Space wrap>
          <Tag color={sync.data?.settings.enabled ? "blue" : "default"}>
            {sync.error || sync.loading
              ? "同步状态暂不可用"
              : sync.data?.settings.enabled
                ? "后台自动同步已启用"
                : "自动同步尚未启用或已暂停"}
          </Tag>
          <span>浏览器关闭不影响同步进程。</span>
          <Link href="/sync">管理 token、进度和异常</Link>
        </Space>
      </Card>
      {!!sync.data?.settings.error && (
        <Alert
          type="warning"
          showIcon
          message={String(sync.data.settings.error)}
        />
      )}
      <Card title="数据集概况">
        <Table
          rowKey="key"
          dataSource={datasets}
          loading={catalog.loading}
          pagination={false}
          columns={[
            { title: "数据集", dataIndex: "label" },
            {
              title: "已规划分片",
              dataIndex: "windows",
              render: (v) => v ?? 0,
            },
            { title: "已校验", dataIndex: "validated", render: (v) => v ?? 0 },
            {
              title: "待处理异常",
              dataIndex: "blocked",
              render: (v) => (v ? <Tag color="red">{String(v)}</Tag> : "0"),
            },
            {
              title: "最近核查",
              dataIndex: "checked_at",
              render: (v) => v || "尚无记录",
            },
            {
              title: "查看",
              render: (_, r) =>
                r.browsable ? (
                  <Link href={`/browse?dataset=${r.key}`}>浏览数据</Link>
                ) : (
                  <Link href="/sync">同步资料</Link>
                ),
            },
          ]}
        />
        <p className="muted">
          已校验数量不是全市场完整率。行情、交易日历、结算参数分别同步，权限不足与未发布区间会保留待办。
        </p>
      </Card>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="覆盖与质量">
            <p>
              区分未下载、等待发布、响应校验、日线覆盖验证与异常；从日期定位到对应任务。
            </p>
            <Link href="/quality">
              <Button>检查覆盖</Button>
            </Link>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="版本与来源">
            <p>
              后台补数和修订保留新版本，已固定的查询与研究输入不会随更新而改变。
            </p>
            <Link href="/versions">
              <Button>查看固定版本</Button>
            </Link>
          </Card>
        </Col>
      </Row>
    </>
  );
}
