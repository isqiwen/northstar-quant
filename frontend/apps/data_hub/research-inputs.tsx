"use client";

import { Alert, Button, Card, Space, Table } from "antd";
import Link from "next/link";
import { useState } from "react";
import { useData } from "../../shared/data";
import { datasetColumns } from "../../shared/datasets";
import { Failure, Heading } from "../../shared/ui";
import { mutate, query } from "./api/client";
import type { DatasetDetails } from "./api/generated";

export function ResearchInputs() {
  const inputs = useData(query("/api/datasets"));
  const [selected, setSelected] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Error>();
  const [result, setResult] = useState<DatasetDetails>();

  async function assemble() {
    setPending(true);
    setError(undefined);
    setResult(undefined);
    try {
      const fixed = await mutate("/api/research-inputs/assemble", {
        snapshot_ids: selected,
      });
      setResult(fixed);
      setSelected([]);
      inputs.refresh();
    } catch (cause) {
      setError(cause as Error);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <Heading
        title="研究快照"
        description="选择同一合约、同一周期的固定时段，组成可重复读取的研究输入。"
      />
      <Failure error={inputs.error || error} />
      {result && (
        <Alert
          type="success"
          showIcon
          message="研究输入已固定"
          description={
            <Link href={`/datasets/${result.snapshot_id}`}>
              查看 {result.symbol} · {result.trading_days.join("、")} ·
              来源与条款
            </Link>
          }
        />
      )}
      <Card title="已发布数据">
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Alert
            type="info"
            showIcon
            message="装配保留已有来源、时间口径、结算和条款。跨日研究仍需完整的有效条款；缺失不会自动补齐。"
          />
          <Space>
            <Button
              type="primary"
              loading={pending}
              disabled={
                selected.length < 2 ||
                selected.length > 32 ||
                !!inputs.error ||
                inputs.loading
              }
              onClick={() => void assemble()}
            >
              装配研究输入（{selected.length}）
            </Button>
            <Button disabled={pending} onClick={inputs.refresh}>
              刷新
            </Button>
          </Space>
          <Table
            rowKey="snapshot_id"
            dataSource={inputs.data}
            columns={datasetColumns}
            loading={inputs.loading}
            rowSelection={{
              selectedRowKeys: selected,
              onChange: (keys) => setSelected(keys.map(String)),
              getCheckboxProps: (row) => ({
                disabled:
                  pending ||
                  (selected.length >= 32 &&
                    !selected.includes(row.snapshot_id)),
              }),
            }}
            scroll={{ x: "max-content" }}
          />
        </Space>
      </Card>
    </>
  );
}
