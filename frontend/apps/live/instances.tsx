"use client";
import { useEffect, useState, type ReactNode } from "react";
import { Alert, Select, Space, Tag } from "antd";
import { useData } from "../../shared/data";
import { pendingCommand, selectLiveInstance } from "../../shared/api";
import { query } from "./api/client";

const labels: Record<string, string> = {
  simnow_trading: "SIM · SimNow 第一套",
  simnow_dev: "SIM · SimNow 开发环境",
  production: "实盘",
};
export function InstanceProvider({ children }: { children: ReactNode }) {
  const catalog = useData(query("/api/live/instances"));
  const [selected, setSelected] = useState<string>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    if (!catalog.data) return;
    const saved =
      pendingCommand()?.instance ||
      sessionStorage.getItem("northstar.live.instance");
    const id = saved || catalog.data.instances[0]?.instance_id;
    if (!id || !catalog.data.instances.some((i) => i.instance_id === id)) {
      setError("原实例未配置，请先恢复其配置或核查原实例状态。");
      return;
    }
    selectLiveInstance(id);
    setSelected(id);
  }, [catalog.data]);
  if (catalog.error)
    return <Alert type="error" title={catalog.error.message} />;
  const current = catalog.data?.instances.find(
    (i) => i.instance_id === selected,
  );
  return (
    <>
      <Space style={{ marginBottom: 20 }}>
        <span>运行实例</span>
        <Select
          aria-label="运行实例"
          value={selected}
          style={{ minWidth: 280 }}
          options={catalog.data?.instances.map((i) => ({
            value: i.instance_id,
            label: `${i.instance_id} · ${labels[i.environment]}`,
          }))}
          onChange={(id) => {
            try {
              selectLiveInstance(id);
              sessionStorage.setItem("northstar.live.instance", id);
              // A fresh page clears old record IDs, forms and runtime observations.
              window.location.assign("/");
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        />
        {current && <Tag color="blue">{labels[current.environment]}</Tag>}
        <Tag>实盘尚未开放</Tag>
      </Space>
      {error && <Alert type="error" title={error} />}
      {selected && <div key={selected}>{children}</div>}
    </>
  );
}
