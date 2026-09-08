"use client";
import type { ReactNode } from "react";
import { query } from "./api/client";
import {
  DashboardOutlined,
  ExperimentOutlined,
  DatabaseOutlined,
  PlayCircleOutlined,
} from "@ant-design/icons";
import { Providers, Shell } from "../../shared/shell";
import { DatasetDetail, DatasetList } from "../../shared/datasets";
import { Heading } from "../../shared/ui";
import { Catalog, Configuration, Factor, FactorRun, Version } from "./catalog";
import { ResearchHome, Report } from "./runs";
import { Paper, PaperDetail } from "./paper";

export default function Workspace({ children }: { children: ReactNode }) {
  return (
    <Providers>
      <Shell
        name="Research"
        subtitle="固定输入 · 可复核研究"
        items={[
          { key: "/", label: "研究概览", icon: <DashboardOutlined /> },
          {
            key: "/catalog",
            label: "因子与策略",
            icon: <ExperimentOutlined />,
          },
          { key: "/datasets", label: "数据快照", icon: <DatabaseOutlined /> },
          { key: "/paper", label: "文件 Paper", icon: <PlayCircleOutlined /> },
        ]}
      >
        {children}
      </Shell>
    </Providers>
  );
}
