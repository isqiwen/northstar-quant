// Generated from research.proto. Do not edit.
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type AdvanceRequest = {
  request_id: string;
};
export type Annotation = {
  at: string;
  description: string;
};
export type AnnotationRequest = {
  description: string;
};
export type BrowserSession = {
  csrf: string;
};
export type Catalog = {
  factors: (FactorSummary)[];
  strategies: (StrategySummary)[];
};
export type Comparison = {
  bar_count: number;
  decision_count: number;
  ending_cash: string;
  ending_equity: string;
  ending_position_lots: number;
  fill_count: number;
  initial_cash: string;
  max_drawdown: string;
  max_drawdown_fraction: string;
  realized_pnl: string;
  run_id: string;
  strategy: string;
  total_fees: string;
  total_return: string;
  unrealized_pnl: string;
};
export type ComparisonRequest = {
  run_ids: (string)[];
};
export type ConfigurationRequest = {
  config: ResearchConfigurationInput;
  name: string;
};
export type DatasetDetails = {
  availability_basis: string;
  availability_note: string;
  bar_count: number;
  content_hash: string;
  exchange: string;
  import_specs: (ImportSpecification)[];
  limitations: (string)[];
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
};
export type DatasetLineage = {
  attempts: (Record<string, JsonValue>)[];
  snapshot_id: string;
  sources: (Record<string, JsonValue>)[];
  usages: (Record<string, JsonValue>)[];
};
export type DatasetSummary = {
  bar_count: number;
  content_hash: string;
  exchange: string;
  product: string;
  published_at: string;
  session_close: string;
  session_open: string;
  snapshot_id: string;
  symbol: string;
  trading_days: (string)[];
};
export type EquityPoint = {
  at: string;
  equity: string;
  observation_id: string;
  [key: string]: unknown;
};
export type FactorBinding = {
  code_revision: string;
  factor_id: string;
  parameters: Record<string, JsonValue>;
  revision: string;
};
export type FactorDescription = {
  capabilities: (string)[];
  category: string;
  description: string;
  factor_id: string;
  limitations: string;
  name: string;
  parameters: (ParameterDescription)[];
  revision: string;
};
export type FactorInput = {
  factor_id: string;
  parameters?: Record<string, JsonValue>;
};
export type FactorResult = {
  evaluation: Record<string, JsonValue>;
  inputs: Record<string, JsonValue>;
  values: (FactorValue)[];
};
export type FactorRevision = {
  annotations: (Annotation)[];
  binding: FactorBinding;
  revision_id: string;
};
export type FactorRevisionRequest = {
  factor_id: string;
  parameters: Record<string, JsonValue>;
};
export type FactorRun = {
  attempt_id: string;
  code_revision: string;
  completed_at: string | null;
  created_at: string;
  error: string | null;
  result: FactorResult | null;
  revision_id: string;
  snapshot_id: string;
  status: string;
  [key: string]: unknown;
};
export type FactorRunRequest = {
  revision_id: string;
  snapshot_id: string;
};
export type FactorSummary = {
  capabilities: (string)[];
  category: string;
  description: string;
  factor_id: string;
  limitations: string;
  name: string;
};
export type FactorValue = {
  at: string;
  observation_id: string;
  reason: string;
  status: string;
  value: string | null;
  [key: string]: unknown;
};
export type FixedFactor = {
  code_revision: string;
  factor_id: string;
  parameters?: Record<string, JsonValue>;
  revision: string;
};
export type FixedStrategy = {
  code_revision: string;
  factor_bindings: Record<string, FixedFactor>;
  parameters: Record<string, JsonValue>;
  revision: string;
  strategy_id: string;
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
export type PaperAdvanced = {
  sequence: number;
  session_id: string;
  step: Record<string, JsonValue>;
  url: string;
  [key: string]: unknown;
};
export type PaperRequest = {
  configuration_id: string;
  request_id: string;
  snapshot_id: string;
};
export type PaperSession = {
  configuration: SavedConfiguration;
  cursor: number;
  session_id: string;
  status: string;
  summary: ResearchSummary;
  [key: string]: unknown;
};
export type ParameterDescription = {
  default: string | number;
  label: string;
  maximum: string | number;
  minimum: string | number;
  name: string;
  unit: string;
};
export type PublishRequest = {
};
export type Readiness = {
  status: "ready";
};
export type ResearchAttempt = {
  attempt_id: string;
  created_at: string;
  error: string | null;
  run_id: string | null;
  status: string;
  [key: string]: unknown;
};
export type ResearchConfiguration = {
  risk: RiskInput;
  simulation: SimulationInput;
  strategy: FixedStrategy;
};
export type ResearchConfigurationInput = {
  risk?: RiskInput;
  simulation?: SimulationInput;
  strategy?: JsonValue | null;
};
export type ResearchResultDocument = {
  data: Record<string, JsonValue> | null;
  decisions: (Record<string, JsonValue>)[];
  equity_curve: (EquityPoint)[];
  fills: (Record<string, JsonValue>)[];
  summary: ResearchSummary;
  [key: string]: unknown;
};
export type ResearchSummary = {
  bar_count: number;
  decision_count: number;
  ending_cash: string;
  ending_equity: string;
  ending_position_lots: number;
  fill_count: number;
  initial_cash: string;
  max_drawdown: string;
  max_drawdown_fraction: string;
  realized_pnl: string;
  total_fees: string;
  total_return: string;
  unrealized_pnl: string;
};
export type RevisionCreated = {
  revision_id: string;
};
export type RiskInput = {
  initial_margin_fraction?: string;
  max_adverse_price_move_fraction?: string;
  max_gross_notional?: string;
  max_lots?: number;
  max_margin_fraction?: string;
};
export type RunDetail = {
  code_revision: string;
  committed_code: boolean;
  config: ResearchConfiguration;
  created_at: string;
  result: ResearchResultDocument;
  run_id: string;
  snapshot: SnapshotReference;
  [key: string]: unknown;
};
export type RunSummary = {
  code_revision: string;
  committed_code: boolean;
  config: ResearchConfiguration;
  created_at: string;
  market: Record<string, JsonValue>;
  run_id: string;
  snapshot: SnapshotReference;
  summary: ResearchSummary;
  [key: string]: unknown;
};
export type SavedConfiguration = {
  config: ResearchConfiguration;
  configuration_id: string;
  created_at: string;
  name: string;
  risk_hash: string;
  strategy_hash: string;
};
export type SimulationInput = {
  fee_per_lot?: string;
  initial_cash?: string;
  slippage_ticks?: number;
};
export type SnapshotReference = {
  content_hash: string;
  id: string;
  [key: string]: unknown;
};
export type StrategyCandidate = {
  candidate_id: string;
  document: VersionDocument;
  format: number;
  production_eligible: boolean;
  version_id: string;
};
export type StrategyDescription = {
  category: string;
  description: string;
  factor_slots: Record<string, string>;
  name: string;
  parameters: (ParameterDescription)[];
  revision: string;
  strategy_id: string;
};
export type StrategyInput = {
  factor_bindings?: Record<string, FactorInput> | null;
  parameters?: Record<string, JsonValue>;
  strategy_id?: string;
};
export type StrategySummary = {
  category: string;
  description: string;
  name: string;
  strategy_id: string;
};
export type StrategyVersion = {
  created_at: string;
  document: VersionDocument;
  name: string;
  version_id: string;
};
export type StrategyVersionRequest = {
  configuration_id: string;
  name: string;
  run_ids: (string)[];
};
export type VersionCreated = {
  version_id: string;
};
export type VersionDocument = {
  code_revision: string;
  configuration: SavedConfiguration;
  evidence: (RunDetail)[];
  validation: Record<string, JsonValue>;
};
export type GetApiConfigurationsResponse = (SavedConfiguration)[];
export type GetApiDatasetsResponse = (DatasetSummary)[];
export type GetApiFactorRevisionsResponse = (FactorRevision)[];
export type GetApiFactorRunsResponse = (FactorRun)[];
export type GetApiPaperResponse = (PaperSession)[];
export type GetApiResearchAttemptsResponse = (ResearchAttempt)[];
export type PostApiRunComparisonsResponse = (Comparison)[];
export type GetApiRunsResponse = (RunSummary)[];
export type GetApiStrategyCandidatesResponse = (StrategyCandidate)[];
export type GetApiStrategyVersionsResponse = (StrategyVersion)[];
export type ResearchTask = {
  task_id: string;
  snapshot_id: string;
  snapshot_hash: string;
  code_revision: string;
  created_at: string;
  status: string;
  completed: number;
  total: number;
  run_id?: string | null;
  reason: string;
  config: Record<string, JsonValue>;
  attempts: (Record<string, JsonValue>)[];
  [key: string]: unknown;
};
export type TaskRequest = {
  request_id: string;
  snapshot_id: string;
  config: ResearchConfigurationInput;
};
export type TaskControl = {
  action: string;
};
export type TaskList = (ResearchTask)[];
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
