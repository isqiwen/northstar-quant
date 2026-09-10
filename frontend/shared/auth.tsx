"use client";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  useRef,
  type ReactNode,
} from "react";
import { Alert, Button, Card, Form, Input, Spin, Typography } from "antd";
import {
  AUTH_REQUIRED,
  browserSession,
  login,
  logout,
  type BrowserSession,
} from "./api";

const Auth = createContext<(() => Promise<void>) | null>(null);
export function Logout() {
  const exit = useContext(Auth);
  return exit ? (
    <Button type="text" onClick={() => void exit()}>
      退出登录
    </Button>
  ) : null;
}
export function AuthGate({ children }: { children: ReactNode }) {
  const generation = useRef(0);
  const [current, setCurrent] = useState<BrowserSession | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  useEffect(() => {
    let active = true;
    const expired = () => {
      generation.current += 1;
      setCurrent(null);
      setChecking(false);
    };
    window.addEventListener(AUTH_REQUIRED, expired);
    const check = () => {
      const version = generation.current;
      return browserSession()
        .then((value) => {
          if (active && version === generation.current) {
            setCurrent(value);
            setError("");
          }
        })
        .catch((e) => {
          if (active && version === generation.current)
            setError(String(e.message));
        })
        .finally(() => {
          if (active && version === generation.current) setChecking(false);
        });
    };
    void check();
    const timer = setInterval(() => void check(), 30_000);
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener(AUTH_REQUIRED, expired);
    };
  }, []);
  async function enter(values: { password: string }) {
    generation.current += 1;
    setBusy(true);
    setError("");
    try {
      setCurrent(await login(values.password));
    } catch (e) {
      setError(e instanceof Error ? e.message : "登录失败");
    } finally {
      form.resetFields();
      setBusy(false);
    }
  }
  async function exit() {
    generation.current += 1;
    try {
      await logout();
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "退出失败");
    }
  }
  if (checking)
    return (
      <div style={{ padding: 80, textAlign: "center" }}>
        <Spin tip="检查登录状态" />
      </div>
    );
  if (current?.authenticated)
    return (
      <Auth.Provider value={exit}>
        {error && <Alert type="error" message={error} />}
        {children}
      </Auth.Provider>
    );
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 24,
      }}
    >
      <Card style={{ width: "100%", maxWidth: 400 }}>
        <Typography.Title level={3}>登录 Northstar</Typography.Title>
        <Typography.Paragraph type="secondary">
          使用当前应用的工作台密码。
        </Typography.Paragraph>
        {error && (
          <Alert type="error" message={error} style={{ marginBottom: 20 }} />
        )}
        <Form form={form} layout="vertical" onFinish={enter}>
          <Form.Item
            name="password"
            label="工作台密码"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password autoComplete="current-password" maxLength={1024} />
          </Form.Item>
          <Button htmlType="submit" type="primary" loading={busy} block>
            登录
          </Button>
        </Form>
      </Card>
    </main>
  );
}
