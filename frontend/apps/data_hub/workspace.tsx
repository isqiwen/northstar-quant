"use client";
import type { ReactNode } from "react";
import { query } from "./api/client";
import {
  DashboardOutlined,
  DatabaseOutlined,
  FolderOpenOutlined,
  CloudUploadOutlined,
} from "@ant-design/icons";
import { Shell, Providers } from "../../shared/shell";
import { DatasetDetail, DatasetList } from "../../shared/datasets";
import { Heading } from "../../shared/ui";
import { DataHome, Sources, Import, SourceDetail, Attempt } from "./pages";

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
          { key: "/import", label: "导入数据", icon: <CloudUploadOutlined /> },
        ]}
      >
        {children}
      </Shell>
    </Providers>
  );
}
