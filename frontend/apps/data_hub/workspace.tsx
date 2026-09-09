"use client";
import type { ReactNode } from "react";
import {
  DashboardOutlined,
  DatabaseOutlined,
  FolderOpenOutlined,
  CloudUploadOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import { Shell, Providers } from "../../shared/shell";

export default function Workspace({ children }: { children: ReactNode }) {
  return (
    <Providers>
      <Shell
        name="Data Hub"
        subtitle="来源证据 · 固定快照"
        items={[
          { key: "/", label: "数据概览", icon: <DashboardOutlined /> },
          { key: "/datasets", label: "数据快照", icon: <DatabaseOutlined /> },
          {
            key: "/sources",
            label: "来源与处理",
            icon: <FolderOpenOutlined />,
          },
          { key: "/sync", label: "历史同步", icon: <SyncOutlined /> },
          { key: "/import", label: "导入数据", icon: <CloudUploadOutlined /> },
        ]}
      >
        {children}
      </Shell>
    </Providers>
  );
}
