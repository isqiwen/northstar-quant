import { beforeEach, expect, it, vi } from "vitest";
vi.mock("../../shared/protobuf", () => ({
  registerProtocol: vi.fn(),
  encodeRequest: () => new Uint8Array([1]),
  decodeResponse: async (_method: string, path: string, response: Response) =>
    path === "/api/browser-session"
      ? { authenticated: true, csrf: "csrf" }
      : response.ok
        ? { total: 0, offset: 0, limit: 10, items: [] }
        : { detail: "query failed" },
}));
beforeEach(() => vi.resetModules());
it("polling neither displays a submitted command nor changes an existing uncertain command", async () => {
  const pending = JSON.stringify({
    id: "real-command",
    path: "/api/control",
    status: "UNKNOWN",
  });
  const storage = {
    getItem: vi.fn(() => pending),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  };
  vi.stubGlobal("sessionStorage", storage);
  const dispatchEvent = vi.fn();
  vi.stubGlobal("window", { dispatchEvent });
  const fetch = vi.fn().mockResolvedValue(new Response());
  vi.stubGlobal("fetch", fetch);
  const { querySyncJobs } = await import("../../apps/data_hub/sync-job-query");
  const body = { dataset: "daily", status: "BLOCKED", offset: 0, limit: 10 };
  await querySyncJobs(body);
  await querySyncJobs(body);
  fetch.mockResolvedValueOnce(new Response(null, { status: 503 }));
  await expect(querySyncJobs(body)).rejects.toThrow("query failed");
  expect(storage.setItem).not.toHaveBeenCalled();
  expect(storage.removeItem).not.toHaveBeenCalled();
  expect(dispatchEvent).not.toHaveBeenCalled();
  expect(fetch.mock.calls[1][1].headers["X-Northstar-CSRF"]).toBe("csrf");
});
