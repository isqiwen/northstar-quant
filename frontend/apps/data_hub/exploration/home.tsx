"use client";
import { StorageAlert } from "../storage-alert";
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
  const contracts = sync.data?.lanes?.find((lane) => lane.lane === "contracts");
  return (
    <>
      <Heading
        title="期货数据工作台"
        description="交易所 → 品种 → 已退市合约 · 完整生命周期验收后发布"
        actions={
          <Link href="/browse">
            <Button type="primary">浏览行情数据</Button>
          </Link>
        }
      />
      <Failure error={catalog.error || sync.error} />
      <StorageAlert capacity={sync.data?.settings.source_capacity} />
      <Row gutter={[16, 16]}>
        {[
          ["已规划退市合约", contracts?.total ?? 0],
          ["已发布完整合约", contracts?.validated ?? 0],
          ["待核验合约", contracts?.waiting ?? 0],
          ["已拒绝合约", contracts?.blocked ?? 0],
        ].map(([title, value]) => (
          <Col xs={12} xl={6} key={title}>
            <Card>
              <Statistic
                title={title}
                value={available && sync.data && !sync.error ? value : "—"}
              />
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
      <Card title="内部采集诊断">
        <Table
          rowKey="key"
          dataSource={datasets}
          loading={catalog.loading}
          pagination={false}
          columns={[
            { title: "数据集", dataIndex: "label" },
            {
              title: "内部请求数",
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
          内部请求通过校验不等于合约完整。只有全部必需数据完整、适用性明确的已退市合约才进入正式浏览库。
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
