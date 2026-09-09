// Generated from data_hub.proto. Do not edit.
import type * as messages from "./generated";
import { mutate as send, registerProtocol } from "../../../shared/api";
import type { Query } from "../../../shared/data";
import protocol from "./protocol.json";
import codec from "./codec";
registerProtocol(protocol, codec);
export type GetPath = `/api/datasets/${string}/lineage` | `/api/datasets/${string}` | `/api/attempts/${string}` | `/api/sources/${string}` | `/api/processing/status` | `/api/browser-session` | `/api/rejections` | `/api/attempts` | `/api/datasets` | `/api/sources`;
export type GetResponse<P> = P extends `/api/datasets/${string}/lineage` ? messages.DatasetLineage :
P extends `/api/datasets/${string}` ? messages.DatasetDetails :
P extends `/api/attempts/${string}` ? messages.ProcessingAttempt :
P extends `/api/sources/${string}` ? messages.SourceRecord :
P extends `/api/processing/status` ? messages.ProcessingQueueStatus :
P extends `/api/browser-session` ? messages.BrowserSession :
P extends `/api/rejections` ? messages.GetApiRejectionsResponse :
P extends `/api/attempts` ? messages.GetApiAttemptsResponse :
P extends `/api/datasets` ? messages.GetApiDatasetsResponse :
P extends `/api/sources` ? messages.GetApiSourcesResponse : never;
export type CommandPath = `/api/sources/${string}/reprocess` | `/api/import`;
export type CommandResponse<P> = P extends `/api/sources/${string}/reprocess` ? messages.ProcessingAttempt :
P extends `/api/import` ? messages.ProcessingAttempt : never;
export type CommandBody<P> = P extends `/api/sources/${string}/reprocess` ? messages.ReprocessRequest :
P extends `/api/import` ? messages.ImportRequest : never;
export function query<P extends GetPath>(path: P | null): Query<GetResponse<P>> | null {return path === null ? null : {path};}
export function mutate<P extends CommandPath>(path: P, body: CommandBody<NoInfer<P>>, runtime?: string): Promise<CommandResponse<P>> {return send(path, body, runtime);}
