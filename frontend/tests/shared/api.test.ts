import { beforeEach, describe, expect, it, vi } from "vitest";
import protocol from "../../apps/live/api/protocol.json";
import codec from "../../apps/live/api/codec";
const bytes = {
  session: [10, 5, 116, 111, 107, 101, 110],
  budget: [
    10, 6, 98, 117, 100, 103, 101, 116, 194, 62, 20, 10, 6, 115, 116, 97, 116,
    117, 115, 18, 10, 26, 8, 82, 69, 67, 79, 82, 68, 69, 68,
  ],
  unknown: [
    10, 1, 115, 194, 62, 19, 10, 6, 115, 116, 97, 116, 117, 115, 18, 9, 26, 7,
    85, 78, 75, 78, 79, 87, 78,
  ],
  forbidden: [10, 9, 70, 111, 114, 98, 105, 100, 100, 101, 110],
};
const response = (key: keyof typeof bytes, status = 200) =>
  new Response(new Uint8Array(bytes[key]), {
    status,
    headers: { "content-type": "application/protobuf" },
  });
let memory = new Map<string, string>();
beforeEach(async () => {
  vi.resetModules();
  memory = new Map();
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => memory.get(k) || null,
    setItem: (k: string, v: string) => memory.set(k, v),
    removeItem: (k: string) => memory.delete(k),
  });
  vi.stubGlobal("window", { dispatchEvent: vi.fn() });
  (await import("../../shared/api")).registerProtocol(protocol, codec);
});
describe("fixed Protobuf commands", () => {
  it("preserves exact money and runtime identity without duplicate sends", async () => {
    let release: (r: Response) => void = () => {};
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response("session"))
      .mockImplementationOnce(
        () => new Promise<Response>((r) => (release = r)),
      );
    vi.stubGlobal("fetch", fetch);
    const api = await import("../../shared/api");
    const body = {
      request_id: "saved-command",
      limit_price: "1234567890.123456789",
      sequence: 7,
      order_check_id: "check",
    };
    const first = api.mutate(
      "/api/streams/s/opening-budgets",
      body,
      "runtime-a",
    );
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    await expect(
      api.mutate("/api/streams/s/opening-budgets", body, "runtime-b"),
    ).rejects.toThrow("未确认");
    const init = fetch.mock.calls[1][1];
    expect(new TextDecoder().decode(init.body)).toContain(body.limit_price);
    expect(init.headers).toMatchObject({
      "Content-Type": "application/protobuf",
      "X-Live-Runtime-Id": "runtime-a",
      "X-Northstar-CSRF": "token",
    });
    release(response("budget"));
    await expect(first).resolves.toMatchObject({
      budget_id: "budget",
      status: "RECORDED",
    });
    expect(api.pendingCommand()).toBeNull();
  });
  it("retains unknown commands across reload without reconnect or retry", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response("session"))
      .mockRejectedValueOnce(new TypeError("network lost"));
    vi.stubGlobal("fetch", fetch);
    let api = await import("../../shared/api");
    await expect(
      api.mutate(
        "/api/streams/s/control",
        { request_id: "fixed", action: "STOP" },
        "runtime",
      ),
    ).rejects.toThrow("未知");
    vi.resetModules();
    api = await import("../../shared/api");
    expect(api.pendingCommand()).toMatchObject({
      id: "fixed",
      runtime: "runtime",
      status: "UNKNOWN",
      body: { action: "STOP" },
    });
    await expect(
      api.mutate(
        "/api/streams/s/control",
        { request_id: "new", action: "STOP" },
        "runtime",
      ),
    ).rejects.toThrow("未确认");
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it.each(["html", "unknown", "unavailable"])(
    "locks %s acknowledgements",
    async (kind) => {
      const result =
        kind === "unknown"
          ? response("unknown")
          : new Response("gateway", {
              status: kind === "unavailable" ? 503 : 200,
            });
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValueOnce(response("session"))
          .mockResolvedValueOnce(result),
      );
      const api = await import("../../shared/api");
      await expect(
        api.mutate(
          "/api/streams/s/control",
          { request_id: "fixed", action: "STOP" },
          "runtime",
        ),
      ).rejects.toThrow();
      expect(api.pendingCommand()?.status).toBe("UNKNOWN");
    },
  );
  it("rejects denied commands without replay", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response("session"))
      .mockResolvedValueOnce(response("forbidden", 403));
    vi.stubGlobal("fetch", fetch);
    const api = await import("../../shared/api");
    await expect(
      api.mutate(
        "/api/streams/s/control",
        { request_id: "fixed", action: "STOP" },
        "runtime",
      ),
    ).rejects.toThrow("Forbidden");
    expect(api.pendingCommand()).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

it("preserves explicit false and zero without inventing omitted authority", async () => {
  const { decodeResponse } = await import("../../shared/protobuf");
  const payload = await decodeResponse(
    "GET",
    "/api/live/status",
    new Response(
      new Uint8Array([
        8, 0, 26, 20, 50, 48, 50, 54, 45, 48, 57, 45, 48, 56, 84, 48, 48, 58,
        48, 48, 58, 48, 48, 90, 32, 0, 40, 0, 50, 1, 50, 58, 0, 66, 9, 115, 121,
        110, 116, 104, 101, 116, 105, 99, 74, 20, 50, 48, 50, 54, 45, 48, 57,
        45, 48, 56, 84, 48, 48, 58, 48, 48, 58, 48, 48, 90, 82, 9, 65, 86, 65,
        73, 76, 65, 66, 76, 69,
      ]),
      { headers: { "content-type": "application/protobuf" } },
    ),
  );
  expect(payload).toMatchObject({
    order_sending: false,
    cancel_sending: false,
    pid: 0,
    release: "",
  });
  expect(payload).not.toHaveProperty("control_available");
  await expect(
    decodeResponse(
      "GET",
      "/api/live/status",
      new Response(
        new Uint8Array([
          8, 0, 26, 20, 50, 48, 50, 54, 45, 48, 57, 45, 48, 56, 84, 48, 48, 58,
          48, 48, 58, 48, 48, 90, 32, 0, 40, 128, 128, 128, 128, 128, 128, 128,
          16, 50, 1, 50, 58, 0, 66, 9, 115, 121, 110, 116, 104, 101, 116, 105,
          99, 74, 20, 50, 48, 50, 54, 45, 48, 57, 45, 48, 56, 84, 48, 48, 58,
          48, 48, 58, 48, 48, 90, 82, 9, 65, 86, 65, 73, 76, 65, 66, 76, 69,
        ]),
        { headers: { "content-type": "application/protobuf" } },
      ),
    ),
  ).rejects.toThrow("精确表示");
});
