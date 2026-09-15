// Generated from data_hub.proto. Do not edit.
import type * as messages from "./generated";
import { mutate as send, registerProtocol } from "../../../shared/api";
import type { Query } from "../../../shared/data";
import protocol from "./protocol.json";
import codec from "./codec";
registerProtocol(protocol, codec);
export type GetPath = `/api/explorer/compactions/${string}` | `/api/catalog/snapshots/${string}` | `/api/datasets/${string}/lineage` | `/api/publications/${string}` | `/api/sync/receipts/${string}` | `/api/sync/jobs/${string}` | `/api/datasets/${string}` | `/api/attempts/${string}` | `/api/explorer/compactions` | `/api/sources/${string}` | `/api/processing/status` | `/api/browser-session` | `/api/publications` | `/api/rejections` | `/api/explorer` | `/api/attempts` | `/api/datasets` | `/api/sources` | `/api/sync`;
export type GetResponse<P> = P extends `/api/explorer/compactions/${string}` ? messages.Compaction :
P extends `/api/catalog/snapshots/${string}` ? messages.CatalogSnapshot :
P extends `/api/datasets/${string}/lineage` ? messages.DatasetLineage :
P extends `/api/publications/${string}` ? messages.PublicationManifest :
P extends `/api/sync/receipts/${string}` ? messages.SyncEvidence :
P extends `/api/sync/jobs/${string}` ? messages.SyncEvidence :
P extends `/api/datasets/${string}` ? messages.DatasetDetails :
P extends `/api/attempts/${string}` ? messages.ProcessingAttempt :
P extends `/api/explorer/compactions` ? messages.CompactionList :
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
export type CommandPath = `/api/explorer/compactions/${string}/export` | `/api/explorer/compactions/${string}/query` | `/api/catalog/snapshots/${string}/query` | `/api/research-inputs/assemble` | `/api/sync/contracts/review` | `/api/explorer/compactions` | `/api/sync/contracts/query` | `/api/explorer/instrument` | `/api/explorer/available` | `/api/explorer/contracts` | `/api/explorer/versions` | `/api/explorer/coverage` | `/api/explorer/compare` | `/api/series/versions` | `/api/research-inputs` | `/api/explorer/export` | `/api/explorer/select` | `/api/sync/jobs/query` | `/api/explorer/query` | `/api/explorer/chart` | `/api/sync/reprocess` | `/api/explorer/open` | `/api/sync/settings` | `/api/series/query` | `/api/series/retry` | `/api/sync/token` | `/api/logout` | `/api/setup` | `/api/login`;
export type CommandResponse<P> = P extends `/api/explorer/compactions/${string}/export` ? messages.ExplorerRows :
P extends `/api/explorer/compactions/${string}/query` ? messages.ExplorerRows :
P extends `/api/catalog/snapshots/${string}/query` ? messages.CatalogRows :
P extends `/api/research-inputs/assemble` ? messages.DatasetDetails :
P extends `/api/sync/contracts/review` ? messages.ContractReview :
P extends `/api/explorer/compactions` ? messages.Compaction :
P extends `/api/sync/contracts/query` ? messages.CollectionPage :
P extends `/api/explorer/instrument` ? messages.ExplorerInstrument :
P extends `/api/explorer/available` ? messages.ExplorerList :
P extends `/api/explorer/contracts` ? messages.ExplorerList :
P extends `/api/explorer/versions` ? messages.ExplorerList :
P extends `/api/explorer/coverage` ? messages.ExplorerCoverage :
P extends `/api/explorer/compare` ? messages.RevisionComparison :
P extends `/api/series/versions` ? messages.SeriesRows :
P extends `/api/research-inputs` ? messages.ProcessingAttempt :
P extends `/api/explorer/export` ? messages.ExplorerRows :
P extends `/api/explorer/select` ? messages.ExplorerRows :
P extends `/api/sync/jobs/query` ? messages.SyncJobPage :
P extends `/api/explorer/query` ? messages.ExplorerRows :
P extends `/api/explorer/chart` ? messages.ExplorerRows :
P extends `/api/sync/reprocess` ? messages.SyncEvidence :
P extends `/api/explorer/open` ? messages.ExplorerRows :
P extends `/api/sync/settings` ? messages.SyncStatus :
P extends `/api/series/query` ? messages.SeriesRows :
P extends `/api/series/retry` ? messages.SeriesRetry :
P extends `/api/sync/token` ? messages.SyncStatus :
P extends `/api/logout` ? messages.BrowserSession :
P extends `/api/setup` ? messages.BrowserSession :
P extends `/api/login` ? messages.BrowserSession : never;
export type CommandBody<P> = P extends `/api/explorer/compactions/${string}/export` ? messages.CompactionPage :
P extends `/api/explorer/compactions/${string}/query` ? messages.CompactionPage :
P extends `/api/catalog/snapshots/${string}/query` ? messages.CatalogQuery :
P extends `/api/research-inputs/assemble` ? messages.AssembleResearchRequest :
P extends `/api/sync/contracts/review` ? messages.ContractReviewRequest :
P extends `/api/explorer/compactions` ? messages.CompactionRequest :
P extends `/api/sync/contracts/query` ? messages.CollectionQuery :
P extends `/api/explorer/instrument` ? messages.InstrumentSelection :
P extends `/api/explorer/available` ? messages.AvailableSearch :
P extends `/api/explorer/contracts` ? messages.ContractSearch :
P extends `/api/explorer/versions` ? messages.ExplorerRange :
P extends `/api/explorer/coverage` ? messages.ExplorerRange :
P extends `/api/explorer/compare` ? messages.RevisionRequest :
P extends `/api/series/versions` ? messages.SeriesVersions :
P extends `/api/research-inputs` ? messages.PrepareResearchRequest :
P extends `/api/explorer/export` ? messages.ExplorerQuery :
P extends `/api/explorer/select` ? messages.InstrumentOpen :
P extends `/api/sync/jobs/query` ? messages.SyncJobQuery :
P extends `/api/explorer/query` ? messages.ExplorerQuery :
P extends `/api/explorer/chart` ? messages.ChartQuery :
P extends `/api/sync/reprocess` ? messages.SyncReprocessRequest :
P extends `/api/explorer/open` ? messages.PublishedSelection :
P extends `/api/sync/settings` ? messages.SyncSettingsRequest :
P extends `/api/series/query` ? messages.SeriesQuery :
P extends `/api/series/retry` ? messages.SeriesIdentity :
P extends `/api/sync/token` ? messages.SyncTokenRequest :
P extends `/api/logout` ? messages.Empty :
P extends `/api/setup` ? messages.LoginRequest :
P extends `/api/login` ? messages.LoginRequest : never;
export function query<P extends GetPath>(path: P | null): Query<GetResponse<P>> | null {return path === null ? null : {path};}
export function mutate<P extends CommandPath>(path: P, body: CommandBody<NoInfer<P>>, runtime?: string): Promise<CommandResponse<P>> {return send(path, body, runtime);}
