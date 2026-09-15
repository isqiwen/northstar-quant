"use client";
import { App, Button, Card, Select, Space, Table, Tag } from "antd";
import { useEffect, useState } from "react";
import { mutate } from "./api/client";
import { exchangeName } from "./exploration/instrument-labels";

type Row = Record<string, unknown>;
export function SyncProducts({
  config,
  tokenConfigured,
  onSaved,
}: {
  config?: Row;
  tokenConfigured: boolean;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const saved = JSON.stringify(config?.selected_products ?? []);
  useEffect(() => {
    setSelected(JSON.parse(saved));
  }, [saved]);
  async function save(
    enabled: boolean,
    products: string[],
    retrySkipped = false,
  ) {
    setBusy(true);
    try {
      await mutate("/api/sync/settings", {
        revision: Number(config?.revision),
        enabled,
        products,
        retry_skipped: retrySkipped,
      });
      onSaved();
      message.success(
        !enabled
          ? "已暂停领取新任务"
          : products.length
            ? "已保存品种，后台先探查历史覆盖，再下载完整合约"
            : "正在更新品种目录；尚未选择品种，不下载行情",
      );
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const products = (config?.products ?? []) as Row[];
  const origins = (config?.origins ?? []) as Row[];
  return (
    <Card title="选择要下载的品种">
      <p>
        先更新目录，选择品种后开始。所有品种统一仅下载 2025-01-01 及之后上市、且已经结束的真实合约。各核心数据集先探查覆盖，再下载全生命周期；仅完整合约进入发布。
      </p>
      <Space wrap>
        <Select
          mode="multiple"
          aria-label="下载品种"
          placeholder="选择交易所 / 品种，可多选"
          style={{ minWidth: 360 }}
          value={selected}
          onChange={setSelected}
          optionFilterProp="label"
          options={products.map((p) => ({
            value: String(p.key),
            label: `${exchangeName(String(p.exchange))} · ${String(p.name || p.product).replace(/\d+$/, "")} (${p.product})`,
          }))}
        />
        <Button
          type="primary"
          loading={busy}
          disabled={!tokenConfigured || !config || !selected.length}
          onClick={() => save(true, selected)}
        >
          探查并下载所选品种
        </Button>
        <Button
          loading={busy}
          disabled={!tokenConfigured || !config}
          onClick={() => save(true, JSON.parse(saved))}
        >
          更新品种目录
        </Button>
        <Button
          loading={busy}
          disabled={!tokenConfigured || !config || !selected.length}
          onClick={() => save(true, selected, true)}
        >
          重新探查已跳过合约
        </Button>
        <Button
          loading={busy}
          disabled={!config?.enabled}
          onClick={() => save(false, JSON.parse(saved))}
        >
          暂停
        </Button>
        <Tag>
          {config?.enabled
            ? JSON.parse(saved).length
              ? "后台采集中"
              : "仅更新目录"
            : "已暂停"}
        </Tag>
      </Space>
      <p className="muted">
        已发现的最早记录不等于供应商永久覆盖起点。空响应、权限错误和记录异常分别处理，不用统一年份推断。
      </p>
      {!!origins.length && (
        <Table
          size="small"
          rowKey={(r) => `${r.exchange}:${r.product}:${r.dataset}`}
          dataSource={origins}
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: "交易所",
              dataIndex: "exchange",
              render: (v) => exchangeName(String(v)),
            },
            { title: "品种", dataIndex: "product" },
            {
              title: "数据周期 / 资料",
              dataIndex: "dataset",
              render: (v) =>
                (
                  ({
                    "1min": "1 分钟",
                    "5min": "5 分钟",
                    "15min": "15 分钟",
                    "30min": "30 分钟",
                    "60min": "60 分钟",
                    daily: "日线",
                    settlement: "每日结算参数",
                    limits: "涨跌停与最低保证金",
                    week: "周线",
                    month: "月线",
                    holdings: "持仓排名",
                    warehouse: "仓单日报",
                    weekly_detail: "品种交易周报",
                  }) as Record<string, string>
                )[String(v)] || String(v),
            },
            { title: "已发现最早有效记录", dataIndex: "first_observed" },
          ]}
        />
      )}
    </Card>
  );
}
