"use client";
import type { ReactNode } from "react";
import { fetchQuery } from "../../shared/data";
import { query } from "./api/client";
import {
  DashboardOutlined,
  ApiOutlined,
  LineChartOutlined,
  SafetyCertificateOutlined,
  DeploymentUnitOutlined,
} from "@ant-design/icons";
import { Providers, Shell } from "../../shared/shell";
import { DatasetDetail } from "../../shared/datasets";
import { Heading } from "../../shared/ui";
import { InstanceProvider } from "./instances";
import { RuntimeProvider } from "./runtime";
import { Overview, Diagnostics, Materials, LiveRecord } from "./overview";
import { Broker, BrokerDetail } from "./broker";
import { Streams, Stream } from "./streams";
import { ArchiveSource, ArchiveAttempt } from "./archive";

export default function Workspace({ children }: { children: ReactNode }) {
  return (
    <Providers name="Live">
      <Shell
        name="Live"
        lookupCommand={(id) => fetchQuery(query(`/api/live/commands/${id}`))}
        subtitle="独立内核 · 确认事实"
        items={[
          { key: "/", label: "运行概览", icon: <DashboardOutlined /> },
          { key: "/broker", label: "连接与账户", icon: <ApiOutlined /> },
          { key: "/streams", label: "持续行情", icon: <LineChartOutlined /> },
          {
            key: "/materials",
            label: "固定策略材料",
            icon: <DeploymentUnitOutlined />,
          },
          {
            key: "/live",
            label: "运行诊断",
            icon: <SafetyCertificateOutlined />,
          },
        ]}
      >
        <InstanceProvider>
          <RuntimeProvider>{children}</RuntimeProvider>
        </InstanceProvider>
      </Shell>
    </Providers>
  );
}
