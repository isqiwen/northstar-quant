// Generated from live.proto. Do not edit.
import type * as messages from "./generated";
import { mutate as send, registerProtocol } from "../../../shared/api";
import type { Query } from "../../../shared/data";
import protocol from "./protocol.json";
import codec from "./codec";
registerProtocol(protocol, codec);
export type GetPath = `/api/broker/queries/${string}/baseline-context` | `/api/broker/queries/${string}/ledger-context` | `/api/streams/${string}/decisions/${string}` | `/api/broker/queries/${string}/funds-context` | `/api/streams/${string}/opening-budgets` | `/api/streams/${string}/authorizations` | `/api/broker/opening-budgets/${string}` | `/api/broker/position-entries/${string}` | `/api/broker/baseline-checks/${string}` | `/api/broker/position-checks/${string}` | `/api/broker/funds-entries/${string}` | `/api/broker/order-checks/${string}` | `/api/authorizations/${string}` | `/api/live/commands/${string}` | `/api/streams/${string}/events` | `/api/broker/queries/${string}` | `/api/datasets/${string}` | `/api/attempts/${string}` | `/api/sources/${string}` | `/api/streams/${string}` | `/api/strategy-materials` | `/api/orders/${string}` | `/api/live/diagnostics` | `/api/browser-session` | `/api/live/instances` | `/api/broker/queries` | `/api/configurations` | `/api/broker/status` | `/api/live/status` | `/api/streams` | `/api/orders`;
export type GetResponse<P> = P extends `/api/broker/queries/${string}/baseline-context` ? messages.BaselineContext :
P extends `/api/broker/queries/${string}/ledger-context` ? messages.LedgerContext :
P extends `/api/streams/${string}/decisions/${string}` ? messages.StreamDecision :
P extends `/api/broker/queries/${string}/funds-context` ? messages.FundsContext :
P extends `/api/streams/${string}/opening-budgets` ? messages.BudgetContext :
P extends `/api/streams/${string}/authorizations` ? messages.ConsentPage :
P extends `/api/broker/opening-budgets/${string}` ? messages.OpeningBudget :
P extends `/api/broker/position-entries/${string}` ? messages.PositionEntry :
P extends `/api/broker/baseline-checks/${string}` ? messages.CheckRecord :
P extends `/api/broker/position-checks/${string}` ? messages.CheckRecord :
P extends `/api/broker/funds-entries/${string}` ? messages.FundsEntry :
P extends `/api/broker/order-checks/${string}` ? messages.CheckRecord :
P extends `/api/authorizations/${string}` ? messages.ExecutionConsent :
P extends `/api/live/commands/${string}` ? messages.CommandRecord :
P extends `/api/streams/${string}/events` ? messages.GetApiStreamsStreamIdEventsResponse :
P extends `/api/broker/queries/${string}` ? messages.QueryRecord :
P extends `/api/datasets/${string}` ? messages.ArchiveDataset :
P extends `/api/attempts/${string}` ? messages.ArchiveAttempt :
P extends `/api/sources/${string}` ? messages.ArchiveSource :
P extends `/api/streams/${string}` ? messages.StreamDetail :
P extends `/api/strategy-materials` ? messages.GetApiStrategyMaterialsResponse :
P extends `/api/orders/${string}` ? messages.LocalOrderDetail :
P extends `/api/live/diagnostics` ? messages.Diagnostics :
P extends `/api/browser-session` ? messages.BrowserSession :
P extends `/api/live/instances` ? messages.InstanceCatalog :
P extends `/api/broker/queries` ? messages.GetApiBrokerQueriesResponse :
P extends `/api/configurations` ? messages.GetApiConfigurationsResponse :
P extends `/api/broker/status` ? messages.BrokerStatus :
P extends `/api/live/status` ? messages.RuntimeStatus :
P extends `/api/streams` ? messages.GetApiStreamsResponse :
P extends `/api/orders` ? messages.LocalOrderPage : never;
export type CommandPath = `/api/streams/${string}/position-entries` | `/api/streams/${string}/account-catchup` | `/api/streams/${string}/opening-budgets` | `/api/streams/${string}/authorizations` | `/api/authorizations/${string}/revoke` | `/api/sources/${string}/reprocess` | `/api/streams/${string}/archive` | `/api/streams/${string}/control` | `/api/broker/position-entries` | `/api/broker/baseline-checks` | `/api/broker/position-checks` | `/api/broker/funds-entries` | `/api/broker/order-checks` | `/api/strategy-materials` | `/api/broker/baselines` | `/api/broker/queries` | `/api/streams` | `/api/logout` | `/api/setup` | `/api/login`;
export type CommandResponse<P> = P extends `/api/streams/${string}/position-entries` ? messages.PositionEntry :
P extends `/api/streams/${string}/account-catchup` ? messages.AccountProgress :
P extends `/api/streams/${string}/opening-budgets` ? messages.OpeningBudget :
P extends `/api/streams/${string}/authorizations` ? messages.ExecutionConsent :
P extends `/api/authorizations/${string}/revoke` ? messages.ExecutionConsent :
P extends `/api/sources/${string}/reprocess` ? messages.ArchiveAttempt :
P extends `/api/streams/${string}/archive` ? messages.StreamArchive :
P extends `/api/streams/${string}/control` ? messages.StreamControl :
P extends `/api/broker/position-entries` ? messages.PositionEntry :
P extends `/api/broker/baseline-checks` ? messages.CheckRecord :
P extends `/api/broker/position-checks` ? messages.CheckRecord :
P extends `/api/broker/funds-entries` ? messages.FundsEntry :
P extends `/api/broker/order-checks` ? messages.CheckRecord :
P extends `/api/strategy-materials` ? messages.StrategyMaterial :
P extends `/api/broker/baselines` ? messages.BaselineRecord :
P extends `/api/broker/queries` ? messages.QueryRecord :
P extends `/api/streams` ? messages.StreamSummary :
P extends `/api/logout` ? messages.BrowserSession :
P extends `/api/setup` ? messages.BrowserSession :
P extends `/api/login` ? messages.BrowserSession : never;
export type CommandBody<P> = P extends `/api/streams/${string}/position-entries` ? messages.StreamPositionsRequest :
P extends `/api/streams/${string}/account-catchup` ? messages.AccountCatchupRequest :
P extends `/api/streams/${string}/opening-budgets` ? messages.OpeningBudgetRequest :
P extends `/api/streams/${string}/authorizations` ? messages.ConsentRequest :
P extends `/api/authorizations/${string}/revoke` ? messages.RevokeConsent :
P extends `/api/sources/${string}/reprocess` ? messages.ArchiveReprocessRequest :
P extends `/api/streams/${string}/archive` ? messages.ArchiveRequest :
P extends `/api/streams/${string}/control` ? messages.ControlRequest :
P extends `/api/broker/position-entries` ? messages.PositionEntryRequest :
P extends `/api/broker/baseline-checks` ? messages.BaselineCheckRequest :
P extends `/api/broker/position-checks` ? messages.PositionCheckRequest :
P extends `/api/broker/funds-entries` ? messages.FundsEntryRequest :
P extends `/api/broker/order-checks` ? messages.OrderCheckRequest :
P extends `/api/strategy-materials` ? messages.MaterialRequest :
P extends `/api/broker/baselines` ? messages.BaselineRequest :
P extends `/api/broker/queries` ? messages.QueryRequest :
P extends `/api/streams` ? messages.StreamRequest :
P extends `/api/logout` ? messages.Empty :
P extends `/api/setup` ? messages.LoginRequest :
P extends `/api/login` ? messages.LoginRequest : never;
export function query<P extends GetPath>(path: P | null): Query<GetResponse<P>> | null {return path === null ? null : {path};}
export function mutate<P extends CommandPath>(path: P, body: CommandBody<NoInfer<P>>, runtime: string): Promise<CommandResponse<P>> {return send(path, body, runtime);}
