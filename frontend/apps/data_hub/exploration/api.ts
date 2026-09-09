import "../api/client";
import type { CommandBody, CommandResponse, CommandPath } from "../api/client";
import { sessionToken } from "../../../shared/api";
import { encodeRequest, decodeResponse } from "../../../shared/protobuf";

// Idempotent reads use POST for bounded filter messages; they are not trading commands.
export async function explore<
  P extends Extract<CommandPath, `/api/explorer/${string}`>,
>(path: P, body: CommandBody<P>): Promise<CommandResponse<P>> {
  const csrf = await sessionToken();
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    signal: AbortSignal.timeout(30000),
    headers: {
      "Content-Type": "application/protobuf",
      "X-Northstar-CSRF": csrf,
    },
    body: new Uint8Array(encodeRequest("POST", path, body)),
  });
  const result = await decodeResponse("POST", path, response);
  if (!response.ok)
    throw new Error(
      String((result as { detail?: string }).detail || "数据查询失败"),
    );
  return result as CommandResponse<P>;
}
