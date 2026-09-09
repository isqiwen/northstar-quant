"use client";
import {
  query,
  mutate,
  type CommandPath,
  type CommandBody,
  type GetResponse,
  type CommandResponse,
} from "./api/client";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Select,
  Checkbox,
} from "antd";
import { useData } from "../../shared/data";
import { pendingCommand, requestId } from "../../shared/api";
import { Evidence, Failure, Identity } from "../../shared/ui";
const Runtime = createContext<{
  id?: string;
  canControl: boolean;
  available: boolean;
  data?: GetResponse<"/api/live/status">;
  refresh: () => void;
}>({ canControl: false, available: false, refresh: () => {} });
export const useRuntime = () => useContext(Runtime);
export function RuntimeProvider({ children }: { children: ReactNode }) {
  const q = useData(query("/api/live/status"), 3000);
  const [pending, setPending] =
    useState<ReturnType<typeof pendingCommand>>(null);
  useEffect(() => {
    const update = () => setPending(pendingCommand());
    update();
    window.addEventListener("command-change", update);
    return () => window.removeEventListener("command-change", update);
  }, []);
  const [id, setId] = useState<string>();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const age = q.data ? now - Date.parse(q.data.observed_at) : Infinity;
  const fresh = Number.isFinite(age) && age >= -1000 && age <= 10_000;
  useEffect(() => {
    if (!id && q.data && !q.error) setId(q.data.runtime_id);
  }, [q.data, id, q.error]);
  const changed = !!id && !!q.data && id !== q.data.runtime_id;
  return (
    <Runtime.Provider
      value={{
        id,
        available: !!q.data && !q.error && !q.loading && fresh,
        canControl:
          !!id && !q.error && !changed && !q.loading && !pending && fresh,
        data: q.data,
        refresh: q.refresh,
      }}
    >
      {(q.error || (q.data && !fresh)) && (
        <Alert
          type="error"
          showIcon
          title="Live 内核不可用"
          description="显示的历史观察不能确认当前状态，控制已禁用。管理页面不会创建替代内核。"
        />
      )}
      {changed && (
        <Alert
          type="warning"
          showIcon
          title="Live 内核已更换，原页面控制已禁用"
          action={
            <Button onClick={() => setId(q.data!.runtime_id)}>
              绑定当前内核
            </Button>
          }
        />
      )}{" "}
      {children}
    </Runtime.Provider>
  );
}
export type CommandField<P extends CommandPath> = {
  name: Extract<keyof CommandBody<P>, string>;
  label: string;
  kind?: "integer" | "check" | "select";
  options?: { label: string; value: string | number }[];
  initial?: unknown;
};
export function Action<P extends CommandPath>({
  title,
  path,
  fields = [],
  fixed,
  onDone,
  disabled = false,
}: {
  title: string;
  path: P;
  fields?: CommandField<NoInfer<P>>[];
  fixed?: Partial<CommandBody<NoInfer<P>>>;
  onDone?: (v: CommandResponse<NoInfer<P>>) => void;
  disabled?: boolean;
}) {
  const runtime = useRuntime();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CommandResponse<P>>();
  const { message } = App.useApp();
  return (
    <Card title={title}>
      <Form
        name={`${path}:${title}`}
        layout="vertical"
        onFinish={async (v) => {
          if (!runtime.canControl || !runtime.id || disabled) return;
          setBusy(true);
          try {
            const result = await mutate(
              path,
              {
                ...v,
                ...fixed,
                request_id: requestId(),
              } as CommandBody<P>,
              runtime.id,
            );
            setResult(result);
            message.success("内核已确认操作");
            onDone?.(result);
          } catch (e) {
            message.error((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="grid-two">
          {fields.map((f) => (
            <Form.Item<Record<string, unknown>>
              key={f.name}
              name={f.name}
              label={f.kind === "check" ? undefined : f.label}
              initialValue={
                f.initial ?? (f.kind === "check" ? false : undefined)
              }
              valuePropName={f.kind === "check" ? "checked" : "value"}
              rules={f.kind === "check" ? [] : [{ required: true }]}
            >
              {f.kind === "select" ? (
                <Select
                  options={f.options}
                  showSearch
                  optionFilterProp="label"
                />
              ) : f.kind === "integer" ? (
                <InputNumber precision={0} style={{ width: "100%" }} />
              ) : f.kind === "check" ? (
                <Checkbox>{f.label}</Checkbox>
              ) : (
                <Input />
              )}
            </Form.Item>
          ))}
        </div>
        <Button
          htmlType="submit"
          disabled={!runtime.canControl || disabled}
          loading={busy}
        >
          {title}
        </Button>
      </Form>
      {result && <Evidence value={result} title="内核确认结果" />}
    </Card>
  );
}
