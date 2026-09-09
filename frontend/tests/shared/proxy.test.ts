import { afterEach, expect, test } from "vitest";
import { createServer } from "node:http";
import { NextRequest } from "next/server";
import { forward } from "../../shared/proxy";

afterEach(() => {
  delete process.env.NORTHSTAR_API_URL;
});

test("LAN requests retain Host, cookie, CSRF and protobuf bytes upstream", async () => {
  const received: unknown[] = [];
  const server = createServer((request, response) => {
    received.push(request.headers);
    response.setHeader("content-type", "application/x-protobuf");
    response.end(Buffer.from([8, 1]));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address() as { port: number };
    process.env.NORTHSTAR_API_URL = `http://127.0.0.1:${address.port}`;
    for (const host of [
      "core.local:18082",
      "research.local:18084",
      "quant.wangqiwen.me:18080",
      "192.168.50.10:18082",
      "198.51.100.10:18082",
      "[2001:db8::10]:18082",
    ]) {
      const request = new NextRequest(`http://${host}/api/change`, {
        method: "POST",
        headers: {
          host,
          origin: `http://${host}`,
          cookie: "session=test",
          "x-northstar-csrf": "csrf",
          "content-type": "application/x-protobuf",
        },
        body: new Uint8Array([8, 1]),
      });
      const response = await forward(
        request,
        ["research.local", "core.local", "quant.wangqiwen.me"],
        true,
      );
      expect(response.status).toBe(200);
      expect(new Uint8Array(await response.arrayBuffer())).toEqual(
        new Uint8Array([8, 1]),
      );
      expect(received.at(-1)).toMatchObject({
        host,
        origin: `http://${host}`,
        cookie: "session=test",
        "x-northstar-csrf": "csrf",
      });
      expect((await forward(request)).status).toBe(403); // Unconfigured shared policies remain loopback-only.
    }
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("LAN allowance never permits foreign origins or arbitrary hostnames", async () => {
  const cases: Record<string, string>[] = [
    { host: "evil.example:18082" },
    { host: "999.999.999.999:18082" },
    { host: "core.local:18082", origin: "http://evil.example" },
    { host: "core.local:18082", "sec-fetch-site": "cross-site" },
    { host: "core.local:18082", forwarded: "host=core.local" },
    { host: "core.local:99999" },
  ];
  for (const headers of cases) {
    const request = new NextRequest("http://core.local:18082/api/change", {
      method: "POST",
      headers,
    });
    expect((await forward(request, ["core.local"], true)).status).toBe(403);
  }
});
