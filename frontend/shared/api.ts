import { decodeResponse, encodeRequest } from "./protobuf";
export { registerProtocol } from "./protobuf";
/** Browser access and fixed commands. A lost acknowledgement is never retried. */
export type RecordValue = Record<string, unknown>;
let instance: string | undefined;
export function selectLiveInstance(value: string) {
  const pending = pendingCommand();
  if (pending && pending.instance !== value)
    throw new Error("请先核查当前实例的未确认操作。");
  instance = value;
}
function instanceHeaders(value: string | undefined): Record<string, string> {
  return value ? { "X-Live-Instance-Id": value } : {};
}
export type BrowserSession = {
  setup_required: boolean;
  authenticated: boolean;
  csrf: string | null;
  operator: string | null;
  expires_at: string | null;
};
let session: Promise<string> | undefined;
export const AUTH_REQUIRED = "northstar-auth-required";
function requireLogin() {
  session = undefined;
  if (typeof window !== "undefined")
    window.dispatchEvent(new Event(AUTH_REQUIRED));
}
export async function browserSession(): Promise<BrowserSession> {
  const response = await fetch("/api/browser-session", {
    credentials: "same-origin",
    cache: "no-store",
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok)
    throw new Error(`无法检查登录状态（HTTP ${response.status}）`);
  const value = (await decodeResponse(
    "GET",
    "/api/browser-session",
    response,
  )) as BrowserSession;
  if (value.authenticated && value.csrf) session = Promise.resolve(value.csrf);
  else session = undefined;
  return value;
}
export async function login(
  password: string,
  username: string,
  setup = false,
): Promise<BrowserSession> {
  const path = setup ? "/api/setup" : "/api/login";
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    signal: AbortSignal.timeout(15_000),
    headers: { "Content-Type": "application/protobuf" },
    body: encodeRequest("POST", path, { password, username }) as BodyInit,
  });
  const value = await decodeResponse("POST", path, response);
  if (!response.ok)
    throw new Error(String((value as RecordValue).detail || "登录失败"));
  return browserSession();
}
export async function logout(): Promise<void> {
  const csrf = await sessionToken();
  const response = await fetch("/api/logout", {
    method: "POST",
    credentials: "same-origin",
    signal: AbortSignal.timeout(15_000),
    headers: {
      "Content-Type": "application/protobuf",
      "X-Northstar-CSRF": csrf,
    },
    body: encodeRequest("POST", "/api/logout", {}) as BodyInit,
  });
  if (!response.ok && response.status !== 401)
    throw new Error("退出未确认，请重试");
  requireLogin();
}
export function sessionToken(): Promise<string> {
  return (session ??= browserSession()
    .then((value) => {
      if (!value.authenticated || !value.csrf) {
        requireLogin();
        throw new Error("请登录工作台");
      }
      return value.csrf;
    })
    .catch((error) => {
      session = undefined;
      throw error;
    }));
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail: RecordValue = {},
  ) {
    super(message);
  }
}
export async function read<T = unknown>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  const target = instance;
  await sessionToken();
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: instanceHeaders(target),
    cache: "no-store",
    signal: signal
      ? AbortSignal.any([signal, AbortSignal.timeout(15_000)])
      : AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    if (response.status === 401) requireLogin();
    const value = (await decodeResponse("GET", path, response).catch(
      () => ({}),
    )) as RecordValue;
    throw new ApiError(
      String(value.detail || "暂时无法取得数据"),
      response.status,
      value,
    );
  }
  return (await decodeResponse("GET", path, response)) as T;
}
export type Pending = {
  path: string;
  runtime?: string;
  instance?: string;
  id: string;
  status: "SENDING" | "UNKNOWN";
};
const key = "northstar.pending-command";
let restored = false;
export function pendingCommand(): Pending | null {
  try {
    const stored: Pending | null = JSON.parse(
      sessionStorage.getItem(key) || "null",
    );
    const value: Pending | null = stored
      ? {
          id: stored.id,
          path: stored.path,
          runtime: stored.runtime,
          instance: stored.instance,
          status: stored.status,
        }
      : null;
    // Persist only the fields needed to inquire about a command, never request payloads.
    if (value) sessionStorage.setItem(key, JSON.stringify(value));
    if (!restored) {
      restored = true;
      if (value?.status === "SENDING") {
        value.status = "UNKNOWN";
        sessionStorage.setItem(key, JSON.stringify(value));
      }
    }
    return value;
  } catch {
    return null;
  }
}
export function acknowledge() {
  sessionStorage.removeItem(key);
  window.dispatchEvent(new Event("command-change"));
}
function retain(command: Pending) {
  sessionStorage.setItem(key, JSON.stringify(command));
  window.dispatchEvent(new Event("command-change"));
}
/** Cryptographic request identity also works on LAN HTTP (randomUUID requires HTTPS). */
export function requestId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
export async function mutate<T = unknown>(
  path: string,
  body: RecordValue,
  runtime?: string,
): Promise<T> {
  if (pendingCommand()) throw new Error("仍有未确认的操作，请先核查其结果。");
  const target = instance;
  const token = await sessionToken();
  if (pendingCommand()) throw new Error("已有操作提交中。");
  const id =
    typeof body.request_id === "string" ? body.request_id : requestId();
  const encoded = new Uint8Array(encodeRequest("POST", path, body));
  const command: Pending = {
    path,
    runtime,
    instance: target,
    id,
    status: "SENDING",
  };
  retain(command);
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      signal: AbortSignal.timeout(30_000),
      credentials: "same-origin",
      headers: {
        ...instanceHeaders(target),
        "Content-Type": "application/protobuf",
        "X-Northstar-CSRF": token,
        ...(runtime ? { "X-Live-Runtime-Id": runtime } : {}),
      },
      body: encoded,
    });
  } catch {
    retain({ ...command, status: "UNKNOWN" });
    throw new Error("连接中断，操作结果未知。请核查记录，系统不会自动重发。");
  }
  const value = (await decodeResponse("POST", path, response).catch(
    () => null,
  )) as RecordValue | null;
  if (response.ok && value !== null && value?.status !== "UNKNOWN") {
    acknowledge();
    return value as T;
  }
  if (response.status >= 500 || value === null || value?.status === "UNKNOWN") {
    retain({ ...command, status: "UNKNOWN" });
  } else {
    acknowledge();
    if (response.status === 403) session = undefined;
    if (response.status === 401) requireLogin();
  }
  throw new ApiError(
    String(value?.detail || "操作未得到确认"),
    response.status,
    value || {},
  );
}
export function download(value: unknown, name: string) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
