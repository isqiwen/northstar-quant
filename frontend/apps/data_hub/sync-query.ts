import { ApiError, sessionToken } from "../../shared/api";
import { decodeResponse, encodeRequest } from "../../shared/protobuf";
import type {
  CollectionPage,
  CollectionQuery,
  SyncJobPage,
  SyncJobQuery,
} from "./api/generated";
import "./api/client";

/** Read-only POST: filtering never creates or acknowledges an operator command. */
export function querySyncJobs(body: SyncJobQuery, signal?: AbortSignal) {
  return readPage<SyncJobPage>("/api/sync/jobs/query", body, signal);
}
export function queryCollections(body: CollectionQuery, signal?: AbortSignal) {
  return readPage<CollectionPage>("/api/sync/contracts/query", body, signal);
}
async function readPage<T>(
  path: string,
  body: SyncJobQuery | CollectionQuery,
  signal?: AbortSignal,
): Promise<T> {
  const csrf = await sessionToken();
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    signal: signal
      ? AbortSignal.any([signal, AbortSignal.timeout(15000)])
      : AbortSignal.timeout(15000),
    headers: {
      "Content-Type": "application/protobuf",
      "X-Northstar-CSRF": csrf,
    },
    body: encodeRequest("POST", path, body) as BodyInit,
  });
  const value = await decodeResponse("POST", path, response);
  if (!response.ok)
    throw new ApiError(
      String((value as { detail?: string }).detail || "无法查询同步任务"),
      response.status,
    );
  return value as T;
}
