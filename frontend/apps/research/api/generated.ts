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
  settlements: (SettlementFact)[];
  terms: (FuturesTerms)[];
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
  close: string;
  cash: string;
  position_lots: number;
  realized_pnl: string;
  unrealized_pnl: string;
  total_fees: string;
  drawdown: string;
  drawdown_fraction: string;
  long_lots: number;
  short_lots: number;
  net_exposure: string;
  gross_exposure: string;
  settlement_pnl: string;
  trade_realized_pnl: string;
  terms_id?: string | null;
  margin_used?: string | null;
  available?: string | null;
  reserved_fee: string;
  reserved_margin: string;
  reserved_close_lots: number;
  available_after_reservations?: string | null;
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
  analysis: FactorAnalysis;
};
export type ForwardGroup = {
  group: number;
  samples: number;
  mean_forward_return: number | null;
};
export type ForwardDay = {
  trading_day: string;
  samples: number;
  spearman: number | null;
  mean_forward_return: number | null;
};
export type FactorHorizon = {
  bars: number;
  samples: number;
  excluded: Record<string, number>;
  status: string;
  pearson: number | null;
  spearman: number | null;
  group_change_fraction: number | null;
  groups: (ForwardGroup)[];
  days: (ForwardDay)[];
};
export type FactorAnalysis = {
  plan: string;
  numeric: string;
  horizons: (FactorHorizon)[];
  limitations: (string)[];
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
  evaluation: EvaluationResult;
  orders: (Record<string, JsonValue>)[];
  data: DatasetDetails | null;
  decisions: (Record<string, JsonValue>)[];
  equity_curve: (EquityPoint)[];
  fills: (Record<string, JsonValue>)[];
  summary: ResearchSummary;
  [key: string]: unknown;
  settlements: (Record<string, JsonValue>)[];
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
  max_volume_participation?: string;
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
  same_clean_revision: boolean;
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
export type ExperimentRequest = {
  request_id: string;
  hypothesis: string;
  train_snapshot: string;
  validation_snapshot: string;
  test_snapshot: string;
  configurations: (ResearchConfigurationInput)[];
};
export type LearningRecipeInput = {
  fast_bars: number;
  slow_bars: number;
  horizon_bars: number;
  penalties: (string)[];
  threshold: string;
  target_fraction: string;
};
export type LearningExperimentRequest = {
  request_id: string;
  hypothesis: string;
  train_snapshot: string;
  validation_snapshot: string;
  test_snapshot: string;
  configurations: (ResearchConfigurationInput)[];
  learning: LearningRecipeInput;
};
export type Experiment = {
  fitted: Record<string, JsonValue> | null;
  experiment_id: string;
  plan_id: string;
  created_at: string;
  status: string;
  plan: Record<string, JsonValue>;
  selection: Record<string, JsonValue> | null;
  trials: (Record<string, JsonValue>)[];
};
export type ExperimentList = (Experiment)[];
export type TaskList = (ResearchTask)[];
export type EvaluationPlan = {
  plan_id: string;
  revision: string;
  snapshot_id: string;
  content_hash: string;
  window: string;
  event_start: string | null;
  event_end: string | null;
  expected_bars: number | null;
  benchmark: string;
  annualization: string;
  risk_free_rate: string;
  sample_use: string;
};
export type EvaluationResult = {
  plan: EvaluationPlan;
  status: string;
  observed_bars: number;
  benchmark_ending_equity: string;
  benchmark_return: string;
  excess_return: string;
  annualized_return: string | null;
  sharpe: string | null;
  limitations: (string)[];
};
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
export type BrowserSession = {
  authenticated: boolean;
  csrf: string | null;
  operator: string | null;
  expires_at: string | null;
};
export type LoginRequest = {
  password: string;
};
export type ChargeRate = {
  by_money: string;
  by_volume: string;
};
export type FuturesTerms = {
  terms_id: string;
  contract_id: string;
  effective_from: string;
  effective_until: string;
  available_at: string;
  source_reference: string;
  open_fee: ChargeRate;
  close_today_fee: ChargeRate;
  close_yesterday_fee: ChargeRate;
  long_margin: ChargeRate;
  short_margin: ChargeRate;
  lower_limit: string;
  upper_limit: string;
  money_quantum: string;
  fee_rounding: string;
};
export type SettlementFact = {
  settlement_id: string;
  contract_id: string;
  trading_day: string;
  next_trading_day: string;
  settled_at: string;
  available_at: string;
  price: string;
  source_reference: string;
};
