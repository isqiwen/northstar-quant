// Generated from data_hub.proto. Do not edit.
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type AdmissionRejection = {
  reason: string;
  rejection_id: string;
  [key: string]: unknown;
};
export type BrowserSession = {
  csrf: string;
};
export type DatasetDetails = {
  availability_basis: string;
  availability_note: string;
  bar_count: number;
  content_hash: string;
  exchange: string;
  import_spec: ImportSpecification;
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
  trading_day: string;
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
  trading_day: string;
};
export type HttpError = {
  detail: string;
  rejection_id?: string | null;
  request_id?: string | null;
  runtime_id?: string | null;
  status?: string | null;
  url?: string | null;
};
export type ImportRequest = {
  allow_download: boolean;
  allow_retention: boolean;
  content_base64: string;
  filename: string;
  input_kind: string;
  request_id: string;
  source_name: string;
  spec: Record<string, JsonValue>;
  transformation_note: string | null;
  upstream_source_id: string | null;
  use_basis: string;
};
export type ImportSpecification = {
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
export type ProcessingAttempt = {
  attempt_id: string;
  created_at: string;
  error: string | null;
  parameters: Record<string, JsonValue>;
  snapshot_id: string | null;
  source_id: string;
  stage: string;
  status: string;
  [key: string]: unknown;
};
export type Readiness = {
  status: "ready";
};
export type ReprocessRequest = {
  request_id: string;
  spec: Record<string, JsonValue>;
};
export type SourceRecord = {
  allow_download: boolean;
  allow_retention: boolean;
  byte_count: number;
  content_hash: string;
  filename: string;
  input_kind: string;
  received_at: string;
  source_id: string;
  source_name: string;
  [key: string]: unknown;
};
export type GetApiAttemptsResponse = (ProcessingAttempt)[];
export type GetApiDatasetsResponse = (DatasetSummary)[];
export type GetApiRejectionsResponse = (AdmissionRejection)[];
export type GetApiSourcesResponse = (SourceRecord)[];
export type ProcessingQueueStatus = {
  observed_at: string;
  total: number;
  pending: number;
  running: number;
  published: number;
  failed: number;
  oldest_pending_id?: string | null;
  oldest_pending_at?: string | null;
  oldest_pending_seconds?: number | null;
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
