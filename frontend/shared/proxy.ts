/** Forward only this application's API; never act as an arbitrary URL proxy. */
import { NextRequest } from "next/server";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
const MAX_BODY = 8 * 1024 * 1024;
export async function forward(request: NextRequest): Promise<Response> {
  const authority = request.headers.get("host") || "";
  if (!/^(127\.0\.0\.1|localhost):[0-9]+$/.test(authority))
    return new Response("仅接受本机访问", { status: 403 });
  const origin = request.headers.get("origin");
  if (
    origin &&
    origin !== `http://${authority}` &&
    origin !== `https://${authority}`
  )
    return new Response("仅接受同源操作", { status: 403 });
  if ([...request.headers.keys()].some((key) => key === "forwarded"))
    return new Response("不接受代理转发头", { status: 403 });
  if (
    ![null, "same-origin", "none"].includes(
      request.headers.get("sec-fetch-site"),
    )
  )
    return new Response("仅接受同源操作", { status: 403 });
  const backend = process.env.NORTHSTAR_API_URL;
  if (!backend) return new Response("尚未配置所属 API 服务", { status: 503 });
  const destination = new URL(backend);
  destination.pathname = request.nextUrl.pathname;
  destination.search = request.nextUrl.search;
  const headers = new Headers();
  for (const key of [
    "content-type",
    "cookie",
    "x-northstar-csrf",
    "x-live-runtime-id",
    "accept",
  ]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }
  // The backend receives the already-checked public authority; no forwarded trust.
  headers.set("host", authority);
  if (origin) headers.set("origin", origin);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);
  try {
    let body: Uint8Array | undefined;
    if (request.method !== "GET" && request.method !== "HEAD") {
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      if (reader)
        while (true) {
          const item = await reader.read();
          if (item.done) break;
          size += item.value.length;
          if (size > MAX_BODY) {
            await reader.cancel();
            return new Response("请求过大", { status: 413 });
          }
          chunks.push(item.value);
        }
      body = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        body.set(chunk, offset);
        offset += chunk.length;
      }
    }
    const response = await new Promise<{
      status: number;
      headers: Headers;
      body: Uint8Array;
    }>((resolve, reject) => {
      const connect =
        destination.protocol === "https:" ? httpsRequest : httpRequest;
      const upstream = connect(
        destination,
        {
          method: request.method,
          headers: Object.fromEntries(headers),
          signal: controller.signal,
        },
        (incoming) => {
          const chunks: Buffer[] = [];
          let size = 0;
          incoming.on("data", (chunk: Buffer) => {
            size += chunk.length;
            if (size > 128 * 1024 * 1024) {
              upstream.destroy(new Error("API response too large"));
              return;
            }
            chunks.push(chunk);
          });
          incoming.on("error", reject);
          incoming.on("end", () => {
            const received = new Headers();
            for (const [key, value] of Object.entries(incoming.headers)) {
              if (Array.isArray(value))
                for (const item of value) received.append(key, item);
              else if (value !== undefined) received.set(key, value);
            }
            resolve({
              status: incoming.statusCode || 502,
              headers: received,
              body: new Uint8Array(Buffer.concat(chunks)),
            });
          });
        },
      );
      upstream.on("error", reject);
      upstream.end(body);
    });
    const outgoing = new Headers();
    for (const key of [
      "content-type",
      "set-cookie",
      "content-disposition",
      "x-content-type-options",
    ]) {
      const value = response.headers.get(key);
      if (value) outgoing.set(key, value);
    }
    outgoing.set("cache-control", "no-store");
    // Drain under the same deadline so stalled upstream bodies remain bounded in time.
    return new Response(response.body as BodyInit, {
      status: response.status,
      headers: outgoing,
    });
  } catch {
    // Non-protobuf failure is deliberately UNKNOWN to the command client. Never retry.
    return new Response("后端暂时不可用；操作结果需要核查", { status: 503 });
  } finally {
    clearTimeout(timeout);
  }
}
