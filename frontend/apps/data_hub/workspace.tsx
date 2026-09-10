"use client";
import "./api/client";
import type { ReactNode } from "react";
import {
  DashboardOutlined,
  DatabaseOutlined,
  LineChartOutlined,
  CheckCircleOutlined,
  FolderOpenOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import { Shell, Providers } from "../../shared/shell";

export default function Workspace({ children }: { children: ReactNode }) {
  return (
    <Providers name="Data Hub">
      <Shell
        name="Data Hub"
        subtitle="来源证据 · 固定快照"
        items={[
          { key: "/", label: "数据概览", icon: <DashboardOutlined /> },
          { key: "/browse", label: "数据浏览", icon: <LineChartOutlined /> },
          {
            key: "/quality",
            label: "覆盖与质量",
            icon: <CheckCircleOutlined />,
          },
          { key: "/versions", label: "版本与来源", icon: <DatabaseOutlined /> },
          {
            key: "/sources",
            label: "来源与处理",
            icon: <FolderOpenOutlined />,
          },
          { key: "/sync", label: "历史同步", icon: <SyncOutlined /> },
        ]}
      >
        {children}
      </Shell>
    </Providers>
  );
}
