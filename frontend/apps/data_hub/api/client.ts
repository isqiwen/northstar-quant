// Generated from data_hub.proto. Do not edit.
import type * as messages from "./generated";
import { mutate as send, registerProtocol } from "../../../shared/api";
import type { Query } from "../../../shared/data";
import protocol from "./protocol.json";
import codec from "./codec";
registerProtocol(protocol, codec);
export type GetPath = `/api/datasets/${string}/lineage` | `/api/publications/${string}` | `/api/sync/receipts/${string}` | `/api/sync/jobs/${string}` | `/api/datasets/${string}` | `/api/attempts/${string}` | `/api/sources/${string}` | `/api/processing/status` | `/api/browser-session` | `/api/publications` | `/api/rejections` | `/api/explorer` | `/api/attempts` | `/api/datasets` | `/api/sources` | `/api/sync`;
export type GetResponse<P> = P extends `/api/datasets/${string}/lineage` ? messages.DatasetLineage :
P extends `/api/publications/${string}` ? messages.PublicationManifest :
P extends `/api/sync/receipts/${string}` ? messages.SyncEvidence :
P extends `/api/sync/jobs/${string}` ? messages.SyncEvidence :
P extends `/api/datasets/${string}` ? messages.DatasetDetails :
P extends `/api/attempts/${string}` ? messages.ProcessingAttempt :
P extends `/api/sources/${string}` ? messages.SourceRecord :
P extends `/api/processing/status` ? messages.ProcessingQueueStatus :
P extends `/api/browser-session` ? messages.BrowserSession :
P extends `/api/publications` ? messages.PublicationCatalog :
P extends `/api/rejections` ? messages.GetApiRejectionsResponse :
P extends `/api/explorer` ? messages.ExplorerCatalog :
P extends `/api/attempts` ? messages.GetApiAttemptsResponse :
P extends `/api/datasets` ? messages.GetApiDatasetsResponse :
P extends `/api/sources` ? messages.GetApiSourcesResponse :
P extends `/api/sync` ? messages.SyncStatus : never;
export type CommandPath = `/api/explorer/contracts` | `/api/explorer/versions` | `/api/explorer/coverage` | `/api/explorer/compare` | `/api/explorer/export` | `/api/explorer/query` | `/api/sync/reprocess` | `/api/sync/settings` | `/api/sync/token` | `/api/logout` | `/api/login`;
export type CommandResponse<P> = P extends `/api/explorer/contracts` ? messages.ExplorerList :
P extends `/api/explorer/versions` ? messages.ExplorerList :
P extends `/api/explorer/coverage` ? messages.ExplorerCoverage :
P extends `/api/explorer/compare` ? messages.RevisionComparison :
P extends `/api/explorer/export` ? messages.ExplorerRows :
P extends `/api/explorer/query` ? messages.ExplorerRows :
P extends `/api/sync/reprocess` ? messages.SyncEvidence :
P extends `/api/sync/settings` ? messages.SyncStatus :
P extends `/api/sync/token` ? messages.SyncStatus :
P extends `/api/logout` ? messages.BrowserSession :
P extends `/api/login` ? messages.BrowserSession : never;
export type CommandBody<P> = P extends `/api/explorer/contracts` ? messages.ContractSearch :
P extends `/api/explorer/versions` ? messages.ExplorerRange :
P extends `/api/explorer/coverage` ? messages.ExplorerRange :
P extends `/api/explorer/compare` ? messages.RevisionRequest :
P extends `/api/explorer/export` ? messages.ExplorerQuery :
P extends `/api/explorer/query` ? messages.ExplorerQuery :
P extends `/api/sync/reprocess` ? messages.SyncReprocessRequest :
P extends `/api/sync/settings` ? messages.SyncSettingsRequest :
P extends `/api/sync/token` ? messages.SyncTokenRequest :
P extends `/api/logout` ? messages.Empty :
P extends `/api/login` ? messages.LoginRequest : never;
export function query<P extends GetPath>(path: P | null): Query<GetResponse<P>> | null {return path === null ? null : {path};}
export function mutate<P extends CommandPath>(path: P, body: CommandBody<NoInfer<P>>, runtime?: string): Promise<CommandResponse<P>> {return send(path, body, runtime);}
