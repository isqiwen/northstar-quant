// Generated from research.proto. Do not edit.
import type * as messages from "./generated";
import { mutate as send, registerProtocol } from "../../../shared/api";
import type { Query } from "../../../shared/data";
import protocol from "./protocol.json";
import codec from "./codec";
registerProtocol(protocol, codec);
export type GetPath = `/api/strategy-definitions/${string}` | `/api/datasets/${string}/lineage` | `/api/factor-definitions/${string}` | `/api/strategy-versions/${string}` | `/api/factor-runs/${string}` | `/api/configuration-defaults` | `/api/datasets/${string}` | `/api/strategy-candidates` | `/api/paper/${string}` | `/api/research-attempts` | `/api/strategy-versions` | `/api/factor-revisions` | `/api/tasks/${string}` | `/api/browser-session` | `/api/configurations` | `/api/runs/${string}` | `/api/factor-runs` | `/api/datasets` | `/api/catalog` | `/api/tasks` | `/api/paper` | `/api/runs`;
export type GetResponse<P> = P extends `/api/strategy-definitions/${string}` ? messages.StrategyDescription :
P extends `/api/datasets/${string}/lineage` ? messages.DatasetLineage :
P extends `/api/factor-definitions/${string}` ? messages.FactorDescription :
P extends `/api/strategy-versions/${string}` ? messages.StrategyVersion :
P extends `/api/factor-runs/${string}` ? messages.FactorRun :
P extends `/api/configuration-defaults` ? messages.ResearchConfiguration :
P extends `/api/datasets/${string}` ? messages.DatasetDetails :
P extends `/api/strategy-candidates` ? messages.GetApiStrategyCandidatesResponse :
P extends `/api/paper/${string}` ? messages.PaperSession :
P extends `/api/research-attempts` ? messages.GetApiResearchAttemptsResponse :
P extends `/api/strategy-versions` ? messages.GetApiStrategyVersionsResponse :
P extends `/api/factor-revisions` ? messages.GetApiFactorRevisionsResponse :
P extends `/api/tasks/${string}` ? messages.ResearchTask :
P extends `/api/browser-session` ? messages.BrowserSession :
P extends `/api/configurations` ? messages.GetApiConfigurationsResponse :
P extends `/api/runs/${string}` ? messages.RunDetail :
P extends `/api/factor-runs` ? messages.GetApiFactorRunsResponse :
P extends `/api/datasets` ? messages.GetApiDatasetsResponse :
P extends `/api/catalog` ? messages.Catalog :
P extends `/api/tasks` ? messages.TaskList :
P extends `/api/paper` ? messages.GetApiPaperResponse :
P extends `/api/runs` ? messages.GetApiRunsResponse : never;
export type CommandPath = `/api/factor-revisions/${string}/annotations` | `/api/strategy-versions/${string}/publish` | `/api/paper/${string}/advance` | `/api/tasks/${string}/control` | `/api/strategy-versions` | `/api/factor-revisions` | `/api/run-comparisons` | `/api/configurations` | `/api/factor-runs` | `/api/tasks` | `/api/paper`;
export type CommandResponse<P> = P extends `/api/factor-revisions/${string}/annotations` ? messages.RevisionCreated :
P extends `/api/strategy-versions/${string}/publish` ? messages.StrategyCandidate :
P extends `/api/paper/${string}/advance` ? messages.PaperAdvanced :
P extends `/api/tasks/${string}/control` ? messages.ResearchTask :
P extends `/api/strategy-versions` ? messages.VersionCreated :
P extends `/api/factor-revisions` ? messages.RevisionCreated :
P extends `/api/run-comparisons` ? messages.PostApiRunComparisonsResponse :
P extends `/api/configurations` ? messages.SavedConfiguration :
P extends `/api/factor-runs` ? messages.FactorRun :
P extends `/api/tasks` ? messages.ResearchTask :
P extends `/api/paper` ? messages.PaperSession : never;
export type CommandBody<P> = P extends `/api/factor-revisions/${string}/annotations` ? messages.AnnotationRequest :
P extends `/api/strategy-versions/${string}/publish` ? messages.PublishRequest :
P extends `/api/paper/${string}/advance` ? messages.AdvanceRequest :
P extends `/api/tasks/${string}/control` ? messages.TaskControl :
P extends `/api/strategy-versions` ? messages.StrategyVersionRequest :
P extends `/api/factor-revisions` ? messages.FactorRevisionRequest :
P extends `/api/run-comparisons` ? messages.ComparisonRequest :
P extends `/api/configurations` ? messages.ConfigurationRequest :
P extends `/api/factor-runs` ? messages.FactorRunRequest :
P extends `/api/tasks` ? messages.TaskRequest :
P extends `/api/paper` ? messages.PaperRequest : never;
export function query<P extends GetPath>(path: P | null): Query<GetResponse<P>> | null {return path === null ? null : {path};}
export function mutate<P extends CommandPath>(path: P, body: CommandBody<NoInfer<P>>, runtime?: string): Promise<CommandResponse<P>> {return send(path, body, runtime);}
