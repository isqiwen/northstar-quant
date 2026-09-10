"use client";
import { useState, type ReactNode } from "react";
import {
  App,
  Button,
  ConfigProvider,
  Layout,
  Menu,
  Tag,
  Typography,
} from "antd";
import zhCN from "antd/locale/zh_CN";
import {
  ApiOutlined,
  AppstoreOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from "@ant-design/icons";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { PendingNotice } from "./ui";
import "./theme.css";
import { AuthGate, Logout } from "./auth";
export function Shell({
  name,
  subtitle,
  items,
  children,
  lookupCommand,
}: {
  name: string;
  subtitle: string;
  items: { key: string; label: string; icon: ReactNode }[];
  children: ReactNode;
  lookupCommand?: (id: string) => Promise<unknown>;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const pathname = usePathname();
  const active =
    [...items]
      .reverse()
      .find((i) => i.key !== "/" && pathname.startsWith(i.key))?.key || "/";
  return (
    <Layout className="workspace">
      <Layout.Sider
        theme="light"
        width={238}
        collapsed={collapsed}
        breakpoint="lg"
        onBreakpoint={setCollapsed}
      >
        <Link href="/" className="brand">
          <span className="brand-symbol">N</span>
          {!collapsed && (
            <span>
              NORTHSTAR<small>{name}</small>
            </span>
          )}
        </Link>
        <div className="nav-caption">{!collapsed ? "工作空间" : "·"}</div>
        <Menu
          mode="inline"
          selectedKeys={[active]}
          items={items.map((i) => ({
            ...i,
            label: <Link href={i.key}>{i.label}</Link>,
          }))}
        />
        {!collapsed && (
          <div className="sidebar-footer">
            <ApiOutlined /> 独立应用 · {name}
            <small>{subtitle}</small>
          </div>
        )}
      </Layout.Sider>
      <Layout>
        <Layout.Header className="topbar">
          <SpaceHeader
            collapsed={collapsed}
            toggle={() => setCollapsed(!collapsed)}
          />
          <div>
            <Tag bordered={false}>{name}</Tag>
            <Logout />
            {items.find((item) => item.key === active)?.label}
          </div>
        </Layout.Header>
        <Layout.Content className="content">
          <PendingNotice lookup={lookupCommand} />
          {children}
        </Layout.Content>
        <Layout.Footer>
          Northstar {name} · {subtitle}
        </Layout.Footer>
      </Layout>
    </Layout>
  );
}
function SpaceHeader({
  collapsed,
  toggle,
}: {
  collapsed: boolean;
  toggle: () => void;
}) {
  return (
    <div className="topbar-label">
      <Button
        type="text"
        aria-label="切换导航"
        icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
        onClick={toggle}
      />
      <AppstoreOutlined />
      <Typography.Text>量化工作空间</Typography.Text>
    </div>
  );
}
export function Providers({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#315bea",
          borderRadius: 9,
          fontFamily:
            'Inter, -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',
          colorBgLayout: "#f5f7fb",
        },
        components: {
          Layout: { headerBg: "#ffffff", siderBg: "#ffffff" },
          Table: { headerBg: "#f8faff" },
        },
      }}
    >
      <App>
        <AuthGate>{children}</AuthGate>
      </App>
    </ConfigProvider>
  );
}
