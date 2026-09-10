// Generated from live.proto. Do not edit.
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type InstanceRecord = {
  instance_id: string;
  environment: "BACKTEST" | "SANDBOX" | "LIVE";
  broker_profile: string;
};
export type InstanceCatalog = {
  instances: (InstanceRecord)[];
  production_available?: boolean;
};
export type AccountCatchupRequest = {
  baseline_id: string;
  request_id: string;
  through_sequence: number;
};
export type AccountProgress = {
  status: string;
  [key: string]: unknown;
};
export type ArchiveAttempt = {
  attempt_id: string;
  parameters: Record<string, JsonValue>;
  snapshot_id: string | null;
  source_id: string;
  status: string;
  [key: string]: unknown;
};
export type ArchiveDataset = {
  availability_basis: string;
  availability_note: string;
  bar_count: number;
  content_hash: string;
  exchange: string;
  import_specs: (ImportSpecification)[];
  limitations: (string)[];
  live_runtime: Record<string, JsonValue>;
  processing_provenance?: Record<string, JsonValue> | null;
  product: string;
  published_at: string;
  quality: Record<string, JsonValue>;
  semantics: Record<string, JsonValue>;
  session_close: string;
  session_open: string;
  snapshot_id: string;
  source_reference: string;
  sources: (Record<string, JsonValue>)[];
  symbol: string;
  trading_days: (string)[];
  settlements: (Record<string, JsonValue>)[];
};
export type ArchiveReprocessRequest = {
  request_id: string;
  spec: Record<string, JsonValue>;
};
export type ArchiveRequest = {
  allow_download: boolean;
  request_id: string;
  session_close: string;
  session_open: string;
  through_sequence: number;
};
export type ArchiveSource = {
  allow_download: boolean;
  filename: string;
  source_id: string;
  [key: string]: unknown;
};
export type BaselineCheckRequest = {
  baseline_id: string;
  query_batch_id: string;
  request_id: string;
};
export type BaselineContext = {
  baseline?: BaselineRecord | null;
  [key: string]: unknown;
};
export type BaselineRecord = {
  baseline_id: string;
  [key: string]: unknown;
};
export type BaselineRequest = {
  request_id: string;
  source_batch_id: string;
};
export type BrokerStatus = {
  connection: string;
  credentials: Record<string, JsonValue>;
  execution: Record<string, boolean>;
  profiles: (Record<string, JsonValue>)[];
  sdk: Record<string, JsonValue>;
  [key: string]: unknown;
};
export type BrowserSession = {
  csrf: string;
};
export type BudgetContext = {
  budgets: (Record<string, JsonValue>)[];
  live_runtime?: Record<string, JsonValue> | null;
  order_checks: (CheckRecord)[];
};
export type CheckRecord = {
  check_id: string;
  [key: string]: unknown;
};
export type CommandRecord = {
  request_id: string;
  status: string;
  [key: string]: unknown;
};
export type CompletedMinute = {
  close: string;
  completed_at: string;
  [key: string]: unknown;
};
export type ControlRequest = {
  action: "PAUSE" | "RESUME" | "STOP";
  request_id: string;
};
export type Diagnostics = {
  database: Record<string, JsonValue>;
  duration_ms: number;
  observed_at: string;
  scope: string;
  source_filesystem: Record<string, JsonValue>;
  status: string;
  [key: string]: unknown;
};
export type FundsContext = {
  entries?: (Record<string, JsonValue>)[];
  [key: string]: unknown;
};
export type FundsEntry = {
  entry_id: string;
  [key: string]: unknown;
};
export type FundsEntryRequest = {
  baseline_id: string;
  request_id: string;
  source_batch_id: string;
};
export type HttpError = {
  detail: string;
  rejection_id?: string | null;
  request_id?: string | null;
  runtime_id?: string | null;
  status?: string | null;
  url?: string | null;
};
export type ImportSpecification = {
  session_kind: "DAY" | "NIGHT";
  availability_basis: string;
  availability_note: string;
  currency: string;
  exchange: string;
  multiplier: string;
  price_tick: string;
  product: string;
  quantity_unit: string;
  session_close: string;
  session_open: string;
  source_name: string;
  source_reference: string;
  symbol: string;
  timezone: string;
  trading_day: string;
};
export type LedgerContext = {
  baseline?: BaselineRecord | null;
  baseline_id?: string | null;
  checks?: (CheckRecord)[];
  entries?: (PositionEntry)[];
  [key: string]: unknown;
};
export type LiveConfiguration = {
  config: Record<string, JsonValue>;
  configuration_id: string;
  name: string;
  [key: string]: unknown;
};
export type MaterialRequest = {
  candidate: Record<string, JsonValue>;
  request_id: string;
};
export type OpeningBudget = {
  budget_id: string;
  [key: string]: unknown;
};
export type OpeningBudgetRequest = {
  limit_price: string;
  order_check_id: string;
  request_id: string;
  sequence: number;
};
export type OrderCheckRequest = {
  position_check_id: string;
  request_id: string;
};
export type PositionCheckRequest = {
  entry_id: string;
  query_batch_id: string;
  request_id: string;
};
export type PositionEntry = {
  entry_id: string;
  [key: string]: unknown;
};
export type PositionEntryRequest = {
  baseline_id: string;
  request_id: string;
  source_batch_id: string;
};
export type QueryRecord = {
  batch_id: string;
  instrument: string;
  status: string;
  [key: string]: unknown;
};
export type QueryRequest = {
  instrument: string;
  request_id: string;
};
export type Readiness = {
  status: "ready";
};
export type RuntimeStatus = {
  instance_id?: string | null;
  environment?: "BACKTEST" | "SANDBOX" | "LIVE" | null;
  broker_profile?: string | null;
  cancel_sending: boolean;
  control_available?: boolean;
  observed_at: string;
  order_sending: boolean;
  pid: number;
  protocol: string;
  release: string;
  runtime_id: string;
  started_at: string;
  status: string;
};
export type StrategyMaterial = {
  candidate_id: string;
  [key: string]: unknown;
};
export type StreamAccountProgress = {
  status: string;
  through_sequence?: number | null;
  [key: string]: unknown;
};
export type StreamArchive = {
  source_id: string;
  [key: string]: unknown;
};
export type StreamBinding = {
  request: StreamBindingRequest;
  [key: string]: unknown;
};
export type StreamBindingRequest = {
  query_batch_id: string;
  [key: string]: unknown;
};
export type StreamControl = {
  stream_id: string;
  [key: string]: unknown;
};
export type StreamDecision = {
  sequence: number;
  [key: string]: unknown;
};
export type StreamDetail = {
  account_progress: StreamAccountProgress;
  archives: (Record<string, JsonValue>)[];
  binding: StreamBinding;
  connection: string;
  cursor: number;
  paused: boolean;
  received: number;
  steps: (StreamStep)[];
  stream_id: string;
  [key: string]: unknown;
};
export type StreamEvent = {
  committed_at: string;
  event: Record<string, JsonValue>;
};
export type StreamPositionsRequest = {
  baseline_id: string;
  request_id: string;
  through_sequence: number;
};
export type StreamRequest = {
  allow_retention: boolean;
  configuration_id: string;
  duration_seconds: number;
  query_batch_id: string;
  request_id: string;
  use_basis: string;
};
export type StreamStep = {
  committed_at: string;
  result: StreamStepResult;
  sequence: number;
};
export type StreamStepResult = {
  bar: CompletedMinute | null;
  intent: Record<string, JsonValue> | null;
  [key: string]: unknown;
};
export type StreamSummary = {
  stream_id: string;
  [key: string]: unknown;
};
export type GetApiBrokerQueriesResponse = (QueryRecord)[];
export type GetApiConfigurationsResponse = (LiveConfiguration)[];
export type GetApiStrategyMaterialsResponse = (StrategyMaterial)[];
export type GetApiStreamsResponse = (StreamSummary)[];
export type GetApiStreamsStreamIdEventsResponse = (StreamEvent)[];
export type Empty = {
};
export type Error = {
  detail?: string;
  status?: string;
  request_id?: string;
  runtime_id?: string;
  url?: string;
  rejection_id?: string;
};
