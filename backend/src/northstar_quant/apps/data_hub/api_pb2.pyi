from google.protobuf import struct_pb2 as _struct_pb2
from northstar_quant.web import api_options_pb2 as _api_options_pb2
from northstar_quant.web import common_pb2 as _common_pb2
from northstar_quant.web import auth_pb2 as _web_auth_pb2
from northstar_quant.accounting import protocol_pb2 as _accounting_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AdmissionRejection(_message.Message):
    __slots__ = ("reason", "rejection_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    REASON_FIELD_NUMBER: _ClassVar[int]
    REJECTION_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    reason: str
    rejection_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, reason: _Optional[str] = ..., rejection_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class DatasetDetails(_message.Message):
    __slots__ = ("availability_basis", "availability_note", "bar_count", "content_hash", "exchange", "import_specs", "limitations", "processing_provenance", "product", "published_at", "quality", "semantics", "session_close", "session_open", "snapshot_id", "source_reference", "sources", "symbol", "trading_days", "null_fields", "settlements", "terms")
    AVAILABILITY_BASIS_FIELD_NUMBER: _ClassVar[int]
    AVAILABILITY_NOTE_FIELD_NUMBER: _ClassVar[int]
    BAR_COUNT_FIELD_NUMBER: _ClassVar[int]
    CONTENT_HASH_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    IMPORT_SPECS_FIELD_NUMBER: _ClassVar[int]
    LIMITATIONS_FIELD_NUMBER: _ClassVar[int]
    PROCESSING_PROVENANCE_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_FIELD_NUMBER: _ClassVar[int]
    PUBLISHED_AT_FIELD_NUMBER: _ClassVar[int]
    QUALITY_FIELD_NUMBER: _ClassVar[int]
    SEMANTICS_FIELD_NUMBER: _ClassVar[int]
    SESSION_CLOSE_FIELD_NUMBER: _ClassVar[int]
    SESSION_OPEN_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REFERENCE_FIELD_NUMBER: _ClassVar[int]
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    TRADING_DAYS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    SETTLEMENTS_FIELD_NUMBER: _ClassVar[int]
    TERMS_FIELD_NUMBER: _ClassVar[int]
    availability_basis: str
    availability_note: str
    bar_count: int
    content_hash: str
    exchange: str
    import_specs: _containers.RepeatedCompositeFieldContainer[ImportSpecification]
    limitations: _containers.RepeatedScalarFieldContainer[str]
    processing_provenance: _struct_pb2.Struct
    product: str
    published_at: str
    quality: _struct_pb2.Struct
    semantics: _struct_pb2.Struct
    session_close: str
    session_open: str
    snapshot_id: str
    source_reference: str
    sources: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    symbol: str
    trading_days: _containers.RepeatedScalarFieldContainer[str]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    settlements: _containers.RepeatedCompositeFieldContainer[_accounting_pb2.SettlementFact]
    terms: _containers.RepeatedCompositeFieldContainer[_accounting_pb2.FuturesTerms]
    def __init__(self, availability_basis: _Optional[str] = ..., availability_note: _Optional[str] = ..., bar_count: _Optional[int] = ..., content_hash: _Optional[str] = ..., exchange: _Optional[str] = ..., import_specs: _Optional[_Iterable[_Union[ImportSpecification, _Mapping]]] = ..., limitations: _Optional[_Iterable[str]] = ..., processing_provenance: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., product: _Optional[str] = ..., published_at: _Optional[str] = ..., quality: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., semantics: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., session_close: _Optional[str] = ..., session_open: _Optional[str] = ..., snapshot_id: _Optional[str] = ..., source_reference: _Optional[str] = ..., sources: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., symbol: _Optional[str] = ..., trading_days: _Optional[_Iterable[str]] = ..., null_fields: _Optional[_Iterable[str]] = ..., settlements: _Optional[_Iterable[_Union[_accounting_pb2.SettlementFact, _Mapping]]] = ..., terms: _Optional[_Iterable[_Union[_accounting_pb2.FuturesTerms, _Mapping]]] = ...) -> None: ...

class DatasetLineage(_message.Message):
    __slots__ = ("attempts", "snapshot_id", "sources", "usages")
    ATTEMPTS_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    USAGES_FIELD_NUMBER: _ClassVar[int]
    attempts: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    snapshot_id: str
    sources: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    usages: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    def __init__(self, attempts: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., snapshot_id: _Optional[str] = ..., sources: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., usages: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ...) -> None: ...

class DatasetSummary(_message.Message):
    __slots__ = ("bar_count", "content_hash", "exchange", "product", "published_at", "session_close", "session_open", "snapshot_id", "symbol", "trading_days")
    BAR_COUNT_FIELD_NUMBER: _ClassVar[int]
    CONTENT_HASH_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_FIELD_NUMBER: _ClassVar[int]
    PUBLISHED_AT_FIELD_NUMBER: _ClassVar[int]
    SESSION_CLOSE_FIELD_NUMBER: _ClassVar[int]
    SESSION_OPEN_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    TRADING_DAYS_FIELD_NUMBER: _ClassVar[int]
    bar_count: int
    content_hash: str
    exchange: str
    product: str
    published_at: str
    session_close: str
    session_open: str
    snapshot_id: str
    symbol: str
    trading_days: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, bar_count: _Optional[int] = ..., content_hash: _Optional[str] = ..., exchange: _Optional[str] = ..., product: _Optional[str] = ..., published_at: _Optional[str] = ..., session_close: _Optional[str] = ..., session_open: _Optional[str] = ..., snapshot_id: _Optional[str] = ..., symbol: _Optional[str] = ..., trading_days: _Optional[_Iterable[str]] = ...) -> None: ...

class HttpError(_message.Message):
    __slots__ = ("detail", "rejection_id", "request_id", "runtime_id", "status", "url", "null_fields")
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    REJECTION_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    detail: str
    rejection_id: str
    request_id: str
    runtime_id: str
    status: str
    url: str
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, detail: _Optional[str] = ..., rejection_id: _Optional[str] = ..., request_id: _Optional[str] = ..., runtime_id: _Optional[str] = ..., status: _Optional[str] = ..., url: _Optional[str] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class ImportSpecification(_message.Message):
    __slots__ = ("interval", "session_kind", "availability_basis", "availability_note", "currency", "exchange", "multiplier", "price_tick", "product", "quantity_unit", "session_close", "session_open", "source_name", "source_reference", "symbol", "timezone", "trading_day")
    INTERVAL_FIELD_NUMBER: _ClassVar[int]
    SESSION_KIND_FIELD_NUMBER: _ClassVar[int]
    AVAILABILITY_BASIS_FIELD_NUMBER: _ClassVar[int]
    AVAILABILITY_NOTE_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    MULTIPLIER_FIELD_NUMBER: _ClassVar[int]
    PRICE_TICK_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_UNIT_FIELD_NUMBER: _ClassVar[int]
    SESSION_CLOSE_FIELD_NUMBER: _ClassVar[int]
    SESSION_OPEN_FIELD_NUMBER: _ClassVar[int]
    SOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REFERENCE_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    TIMEZONE_FIELD_NUMBER: _ClassVar[int]
    TRADING_DAY_FIELD_NUMBER: _ClassVar[int]
    interval: str
    session_kind: str
    availability_basis: str
    availability_note: str
    currency: str
    exchange: str
    multiplier: str
    price_tick: str
    product: str
    quantity_unit: str
    session_close: str
    session_open: str
    source_name: str
    source_reference: str
    symbol: str
    timezone: str
    trading_day: str
    def __init__(self, interval: _Optional[str] = ..., session_kind: _Optional[str] = ..., availability_basis: _Optional[str] = ..., availability_note: _Optional[str] = ..., currency: _Optional[str] = ..., exchange: _Optional[str] = ..., multiplier: _Optional[str] = ..., price_tick: _Optional[str] = ..., product: _Optional[str] = ..., quantity_unit: _Optional[str] = ..., session_close: _Optional[str] = ..., session_open: _Optional[str] = ..., source_name: _Optional[str] = ..., source_reference: _Optional[str] = ..., symbol: _Optional[str] = ..., timezone: _Optional[str] = ..., trading_day: _Optional[str] = ...) -> None: ...

class ProcessingAttempt(_message.Message):
    __slots__ = ("attempt_id", "created_at", "error", "parameters", "snapshot_id", "source_id", "stage", "status", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    ATTEMPT_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    STAGE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    attempt_id: str
    created_at: str
    error: str
    parameters: _struct_pb2.Struct
    snapshot_id: str
    source_id: str
    stage: str
    status: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, attempt_id: _Optional[str] = ..., created_at: _Optional[str] = ..., error: _Optional[str] = ..., parameters: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., snapshot_id: _Optional[str] = ..., source_id: _Optional[str] = ..., stage: _Optional[str] = ..., status: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class Readiness(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: str
    def __init__(self, status: _Optional[str] = ...) -> None: ...

class SourceRecord(_message.Message):
    __slots__ = ("allow_download", "allow_retention", "byte_count", "content_hash", "filename", "input_kind", "received_at", "source_id", "source_name", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    ALLOW_DOWNLOAD_FIELD_NUMBER: _ClassVar[int]
    ALLOW_RETENTION_FIELD_NUMBER: _ClassVar[int]
    BYTE_COUNT_FIELD_NUMBER: _ClassVar[int]
    CONTENT_HASH_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    INPUT_KIND_FIELD_NUMBER: _ClassVar[int]
    RECEIVED_AT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    allow_download: bool
    allow_retention: bool
    byte_count: int
    content_hash: str
    filename: str
    input_kind: str
    received_at: str
    source_id: str
    source_name: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, allow_download: _Optional[bool] = ..., allow_retention: _Optional[bool] = ..., byte_count: _Optional[int] = ..., content_hash: _Optional[str] = ..., filename: _Optional[str] = ..., input_kind: _Optional[str] = ..., received_at: _Optional[str] = ..., source_id: _Optional[str] = ..., source_name: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class GetApiAttemptsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[ProcessingAttempt]
    def __init__(self, items: _Optional[_Iterable[_Union[ProcessingAttempt, _Mapping]]] = ...) -> None: ...

class GetApiDatasetsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[DatasetSummary]
    def __init__(self, items: _Optional[_Iterable[_Union[DatasetSummary, _Mapping]]] = ...) -> None: ...

class GetApiRejectionsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[AdmissionRejection]
    def __init__(self, items: _Optional[_Iterable[_Union[AdmissionRejection, _Mapping]]] = ...) -> None: ...

class GetApiSourcesResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[SourceRecord]
    def __init__(self, items: _Optional[_Iterable[_Union[SourceRecord, _Mapping]]] = ...) -> None: ...

class ProcessingQueueStatus(_message.Message):
    __slots__ = ("observed_at", "total", "pending", "running", "published", "failed", "oldest_pending_id", "oldest_pending_at", "oldest_pending_seconds", "null_fields")
    OBSERVED_AT_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    PENDING_FIELD_NUMBER: _ClassVar[int]
    RUNNING_FIELD_NUMBER: _ClassVar[int]
    PUBLISHED_FIELD_NUMBER: _ClassVar[int]
    FAILED_FIELD_NUMBER: _ClassVar[int]
    OLDEST_PENDING_ID_FIELD_NUMBER: _ClassVar[int]
    OLDEST_PENDING_AT_FIELD_NUMBER: _ClassVar[int]
    OLDEST_PENDING_SECONDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    observed_at: str
    total: int
    pending: int
    running: int
    published: int
    failed: int
    oldest_pending_id: str
    oldest_pending_at: str
    oldest_pending_seconds: int
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, observed_at: _Optional[str] = ..., total: _Optional[int] = ..., pending: _Optional[int] = ..., running: _Optional[int] = ..., published: _Optional[int] = ..., failed: _Optional[int] = ..., oldest_pending_id: _Optional[str] = ..., oldest_pending_at: _Optional[str] = ..., oldest_pending_seconds: _Optional[int] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class ContractReviewRequest(_message.Message):
    __slots__ = ("scope",)
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    scope: str
    def __init__(self, scope: _Optional[str] = ...) -> None: ...

class ContractReview(_message.Message):
    __slots__ = ("completeness", "quality", "contract_type", "scope", "display_name", "exchange", "product", "listing_date", "last_trade_date", "required_end", "status", "admitted", "requirements", "reasons", "policy", "null_fields", "last_delivery_date", "first_delivery_date", "delivery_month", "lifecycle_status", "lifecycle_reason")
    COMPLETENESS_FIELD_NUMBER: _ClassVar[int]
    QUALITY_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_FIELD_NUMBER: _ClassVar[int]
    LISTING_DATE_FIELD_NUMBER: _ClassVar[int]
    LAST_TRADE_DATE_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_END_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ADMITTED_FIELD_NUMBER: _ClassVar[int]
    REQUIREMENTS_FIELD_NUMBER: _ClassVar[int]
    REASONS_FIELD_NUMBER: _ClassVar[int]
    POLICY_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    LAST_DELIVERY_DATE_FIELD_NUMBER: _ClassVar[int]
    FIRST_DELIVERY_DATE_FIELD_NUMBER: _ClassVar[int]
    DELIVERY_MONTH_FIELD_NUMBER: _ClassVar[int]
    LIFECYCLE_STATUS_FIELD_NUMBER: _ClassVar[int]
    LIFECYCLE_REASON_FIELD_NUMBER: _ClassVar[int]
    completeness: _struct_pb2.Struct
    quality: _struct_pb2.Struct
    contract_type: _struct_pb2.Struct
    scope: str
    display_name: str
    exchange: str
    product: str
    listing_date: str
    last_trade_date: str
    required_end: str
    status: str
    admitted: bool
    requirements: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    reasons: _containers.RepeatedScalarFieldContainer[str]
    policy: str
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    last_delivery_date: str
    first_delivery_date: str
    delivery_month: str
    lifecycle_status: str
    lifecycle_reason: str
    def __init__(self, completeness: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., quality: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., contract_type: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., scope: _Optional[str] = ..., display_name: _Optional[str] = ..., exchange: _Optional[str] = ..., product: _Optional[str] = ..., listing_date: _Optional[str] = ..., last_trade_date: _Optional[str] = ..., required_end: _Optional[str] = ..., status: _Optional[str] = ..., admitted: _Optional[bool] = ..., requirements: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., reasons: _Optional[_Iterable[str]] = ..., policy: _Optional[str] = ..., null_fields: _Optional[_Iterable[str]] = ..., last_delivery_date: _Optional[str] = ..., first_delivery_date: _Optional[str] = ..., delivery_month: _Optional[str] = ..., lifecycle_status: _Optional[str] = ..., lifecycle_reason: _Optional[str] = ...) -> None: ...

class SyncSettingsRequest(_message.Message):
    __slots__ = ("revision", "enabled", "products", "retry_skipped")
    REVISION_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    PRODUCTS_FIELD_NUMBER: _ClassVar[int]
    RETRY_SKIPPED_FIELD_NUMBER: _ClassVar[int]
    revision: int
    enabled: bool
    products: _containers.RepeatedScalarFieldContainer[str]
    retry_skipped: bool
    def __init__(self, revision: _Optional[int] = ..., enabled: _Optional[bool] = ..., products: _Optional[_Iterable[str]] = ..., retry_skipped: _Optional[bool] = ...) -> None: ...

class SyncReprocessRequest(_message.Message):
    __slots__ = ("request_id", "source_generation")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_GENERATION_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    source_generation: str
    def __init__(self, request_id: _Optional[str] = ..., source_generation: _Optional[str] = ...) -> None: ...

class SyncTokenRequest(_message.Message):
    __slots__ = ("token",)
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    token: str
    def __init__(self, token: _Optional[str] = ...) -> None: ...

class SyncLane(_message.Message):
    __slots__ = ("lane", "start", "end", "total", "validated", "waiting", "blocked", "running", "oldest_pending", "null_fields")
    LANE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    VALIDATED_FIELD_NUMBER: _ClassVar[int]
    WAITING_FIELD_NUMBER: _ClassVar[int]
    BLOCKED_FIELD_NUMBER: _ClassVar[int]
    RUNNING_FIELD_NUMBER: _ClassVar[int]
    OLDEST_PENDING_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    lane: str
    start: str
    end: str
    total: int
    validated: int
    waiting: int
    blocked: int
    running: int
    oldest_pending: str
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, lane: _Optional[str] = ..., start: _Optional[str] = ..., end: _Optional[str] = ..., total: _Optional[int] = ..., validated: _Optional[int] = ..., waiting: _Optional[int] = ..., blocked: _Optional[int] = ..., running: _Optional[int] = ..., oldest_pending: _Optional[str] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class SyncStatus(_message.Message):
    __slots__ = ("settings", "token_configured", "datasets", "progress", "jobs", "lanes", "unplanned_contracts")
    SETTINGS_FIELD_NUMBER: _ClassVar[int]
    TOKEN_CONFIGURED_FIELD_NUMBER: _ClassVar[int]
    DATASETS_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    JOBS_FIELD_NUMBER: _ClassVar[int]
    LANES_FIELD_NUMBER: _ClassVar[int]
    UNPLANNED_CONTRACTS_FIELD_NUMBER: _ClassVar[int]
    settings: _struct_pb2.Struct
    token_configured: bool
    datasets: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    progress: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    jobs: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    lanes: _containers.RepeatedCompositeFieldContainer[SyncLane]
    unplanned_contracts: int
    def __init__(self, settings: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., token_configured: _Optional[bool] = ..., datasets: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., progress: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., jobs: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., lanes: _Optional[_Iterable[_Union[SyncLane, _Mapping]]] = ..., unplanned_contracts: _Optional[int] = ...) -> None: ...

class CollectionQuery(_message.Message):
    __slots__ = ("exchange", "product", "search", "status", "offset", "limit")
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_FIELD_NUMBER: _ClassVar[int]
    SEARCH_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    exchange: str
    product: str
    search: str
    status: str
    offset: int
    limit: int
    def __init__(self, exchange: _Optional[str] = ..., product: _Optional[str] = ..., search: _Optional[str] = ..., status: _Optional[str] = ..., offset: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class CollectionPage(_message.Message):
    __slots__ = ("total", "offset", "limit", "items", "exchanges", "products")
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    EXCHANGES_FIELD_NUMBER: _ClassVar[int]
    PRODUCTS_FIELD_NUMBER: _ClassVar[int]
    total: int
    offset: int
    limit: int
    items: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    exchanges: _containers.RepeatedScalarFieldContainer[str]
    products: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, total: _Optional[int] = ..., offset: _Optional[int] = ..., limit: _Optional[int] = ..., items: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., exchanges: _Optional[_Iterable[str]] = ..., products: _Optional[_Iterable[str]] = ...) -> None: ...

class SyncJobQuery(_message.Message):
    __slots__ = ("owner_scope", "dataset", "status", "offset", "limit")
    OWNER_SCOPE_FIELD_NUMBER: _ClassVar[int]
    DATASET_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    owner_scope: str
    dataset: str
    status: str
    offset: int
    limit: int
    def __init__(self, owner_scope: _Optional[str] = ..., dataset: _Optional[str] = ..., status: _Optional[str] = ..., offset: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class SyncJobPage(_message.Message):
    __slots__ = ("total", "offset", "limit", "items")
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    total: int
    offset: int
    limit: int
    items: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    def __init__(self, total: _Optional[int] = ..., offset: _Optional[int] = ..., limit: _Optional[int] = ..., items: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ...) -> None: ...

class SyncEvidence(_message.Message):
    __slots__ = ("request_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, request_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class PublicationManifest(_message.Message):
    __slots__ = ("snapshot_id", "storage_id", "path", "sha256", "bytes", "files")
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    STORAGE_ID_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    BYTES_FIELD_NUMBER: _ClassVar[int]
    FILES_FIELD_NUMBER: _ClassVar[int]
    snapshot_id: str
    storage_id: str
    path: str
    sha256: str
    bytes: int
    files: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    def __init__(self, snapshot_id: _Optional[str] = ..., storage_id: _Optional[str] = ..., path: _Optional[str] = ..., sha256: _Optional[str] = ..., bytes: _Optional[int] = ..., files: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ...) -> None: ...

class PublicationCatalog(_message.Message):
    __slots__ = ("snapshots",)
    SNAPSHOTS_FIELD_NUMBER: _ClassVar[int]
    snapshots: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, snapshots: _Optional[_Iterable[str]] = ...) -> None: ...

class ExplorerCatalog(_message.Message):
    __slots__ = ("datasets", "exchanges", "products")
    DATASETS_FIELD_NUMBER: _ClassVar[int]
    EXCHANGES_FIELD_NUMBER: _ClassVar[int]
    PRODUCTS_FIELD_NUMBER: _ClassVar[int]
    datasets: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    exchanges: _containers.RepeatedScalarFieldContainer[str]
    products: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    def __init__(self, datasets: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., exchanges: _Optional[_Iterable[str]] = ..., products: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ...) -> None: ...

class ContractSearch(_message.Message):
    __slots__ = ("exchange", "product", "search", "offset")
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_FIELD_NUMBER: _ClassVar[int]
    SEARCH_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    exchange: str
    product: str
    search: str
    offset: int
    def __init__(self, exchange: _Optional[str] = ..., product: _Optional[str] = ..., search: _Optional[str] = ..., offset: _Optional[int] = ...) -> None: ...

class AvailableSearch(_message.Message):
    __slots__ = ("exchange", "product", "search", "offset", "dataset")
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_FIELD_NUMBER: _ClassVar[int]
    SEARCH_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    DATASET_FIELD_NUMBER: _ClassVar[int]
    exchange: str
    product: str
    search: str
    offset: int
    dataset: str
    def __init__(self, exchange: _Optional[str] = ..., product: _Optional[str] = ..., search: _Optional[str] = ..., offset: _Optional[int] = ..., dataset: _Optional[str] = ...) -> None: ...

class PublishedSelection(_message.Message):
    __slots__ = ("receipt_id",)
    RECEIPT_ID_FIELD_NUMBER: _ClassVar[int]
    receipt_id: str
    def __init__(self, receipt_id: _Optional[str] = ...) -> None: ...

class ExplorerList(_message.Message):
    __slots__ = ("rows", "total")
    ROWS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    rows: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    total: int
    def __init__(self, rows: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., total: _Optional[int] = ...) -> None: ...

class ExplorerRange(_message.Message):
    __slots__ = ("dataset", "scope", "start", "end", "offset")
    DATASET_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    dataset: str
    scope: str
    start: str
    end: str
    offset: int
    def __init__(self, dataset: _Optional[str] = ..., scope: _Optional[str] = ..., start: _Optional[str] = ..., end: _Optional[str] = ..., offset: _Optional[int] = ...) -> None: ...

class ExplorerQuery(_message.Message):
    __slots__ = ("dataset", "scope", "start", "end", "offset", "receipt_ids", "limit")
    DATASET_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_IDS_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    dataset: str
    scope: str
    start: str
    end: str
    offset: int
    receipt_ids: _containers.RepeatedScalarFieldContainer[str]
    limit: int
    def __init__(self, dataset: _Optional[str] = ..., scope: _Optional[str] = ..., start: _Optional[str] = ..., end: _Optional[str] = ..., offset: _Optional[int] = ..., receipt_ids: _Optional[_Iterable[str]] = ..., limit: _Optional[int] = ...) -> None: ...

class ChartQuery(_message.Message):
    __slots__ = ("dataset", "scope", "start", "end", "receipt_ids")
    DATASET_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_IDS_FIELD_NUMBER: _ClassVar[int]
    dataset: str
    scope: str
    start: str
    end: str
    receipt_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, dataset: _Optional[str] = ..., scope: _Optional[str] = ..., start: _Optional[str] = ..., end: _Optional[str] = ..., receipt_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class InstrumentOpen(_message.Message):
    __slots__ = ("scope", "dataset")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    DATASET_FIELD_NUMBER: _ClassVar[int]
    scope: str
    dataset: str
    def __init__(self, scope: _Optional[str] = ..., dataset: _Optional[str] = ...) -> None: ...

class InstrumentSelection(_message.Message):
    __slots__ = ("scope",)
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    scope: str
    def __init__(self, scope: _Optional[str] = ...) -> None: ...

class ExplorerInstrument(_message.Message):
    __slots__ = ("scope", "name", "exchange", "periods")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    PERIODS_FIELD_NUMBER: _ClassVar[int]
    scope: str
    name: str
    exchange: str
    periods: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, scope: _Optional[str] = ..., name: _Optional[str] = ..., exchange: _Optional[str] = ..., periods: _Optional[_Iterable[str]] = ...) -> None: ...

class ExplorerCoverage(_message.Message):
    __slots__ = ("days", "jobs", "note")
    DAYS_FIELD_NUMBER: _ClassVar[int]
    JOBS_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    days: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    jobs: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    note: str
    def __init__(self, days: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., jobs: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., note: _Optional[str] = ...) -> None: ...

class ExplorerRows(_message.Message):
    __slots__ = ("sources", "export_allowed", "dataset", "scope", "start", "end", "receipt_ids", "view_id", "rows", "total", "offset", "limit", "fields", "versions", "note", "scan")
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    EXPORT_ALLOWED_FIELD_NUMBER: _ClassVar[int]
    DATASET_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_IDS_FIELD_NUMBER: _ClassVar[int]
    VIEW_ID_FIELD_NUMBER: _ClassVar[int]
    ROWS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    VERSIONS_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    SCAN_FIELD_NUMBER: _ClassVar[int]
    sources: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    export_allowed: bool
    dataset: str
    scope: str
    start: str
    end: str
    receipt_ids: _containers.RepeatedScalarFieldContainer[str]
    view_id: str
    rows: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    total: int
    offset: int
    limit: int
    fields: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    versions: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    note: str
    scan: _struct_pb2.Struct
    def __init__(self, sources: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., export_allowed: _Optional[bool] = ..., dataset: _Optional[str] = ..., scope: _Optional[str] = ..., start: _Optional[str] = ..., end: _Optional[str] = ..., receipt_ids: _Optional[_Iterable[str]] = ..., view_id: _Optional[str] = ..., rows: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., total: _Optional[int] = ..., offset: _Optional[int] = ..., limit: _Optional[int] = ..., fields: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., versions: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., note: _Optional[str] = ..., scan: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class RevisionRequest(_message.Message):
    __slots__ = ("before_id", "after_id", "offset")
    BEFORE_ID_FIELD_NUMBER: _ClassVar[int]
    AFTER_ID_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    before_id: str
    after_id: str
    offset: int
    def __init__(self, before_id: _Optional[str] = ..., after_id: _Optional[str] = ..., offset: _Optional[int] = ...) -> None: ...

class RevisionComparison(_message.Message):
    __slots__ = ("comparison_id", "rule", "before", "after", "source_changed", "rules_changed", "counts", "changes", "total", "offset", "note")
    COMPARISON_ID_FIELD_NUMBER: _ClassVar[int]
    RULE_FIELD_NUMBER: _ClassVar[int]
    BEFORE_FIELD_NUMBER: _ClassVar[int]
    AFTER_FIELD_NUMBER: _ClassVar[int]
    SOURCE_CHANGED_FIELD_NUMBER: _ClassVar[int]
    RULES_CHANGED_FIELD_NUMBER: _ClassVar[int]
    COUNTS_FIELD_NUMBER: _ClassVar[int]
    CHANGES_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    comparison_id: str
    rule: str
    before: _struct_pb2.Struct
    after: _struct_pb2.Struct
    source_changed: bool
    rules_changed: bool
    counts: _struct_pb2.Struct
    changes: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    total: int
    offset: int
    note: str
    def __init__(self, comparison_id: _Optional[str] = ..., rule: _Optional[str] = ..., before: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., after: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., source_changed: _Optional[bool] = ..., rules_changed: _Optional[bool] = ..., counts: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., changes: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., total: _Optional[int] = ..., offset: _Optional[int] = ..., note: _Optional[str] = ...) -> None: ...

class CompactionRequest(_message.Message):
    __slots__ = ("request_id", "dataset", "scope", "start", "end", "receipt_ids")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    DATASET_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_IDS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    dataset: str
    scope: str
    start: str
    end: str
    receipt_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, request_id: _Optional[str] = ..., dataset: _Optional[str] = ..., scope: _Optional[str] = ..., start: _Optional[str] = ..., end: _Optional[str] = ..., receipt_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class Compaction(_message.Message):
    __slots__ = ("compaction_id", "plan_id", "plan", "created_at", "status", "result", "error", "null_fields")
    COMPACTION_ID_FIELD_NUMBER: _ClassVar[int]
    PLAN_ID_FIELD_NUMBER: _ClassVar[int]
    PLAN_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    compaction_id: str
    plan_id: str
    plan: _struct_pb2.Struct
    created_at: str
    status: str
    result: _struct_pb2.Struct
    error: str
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, compaction_id: _Optional[str] = ..., plan_id: _Optional[str] = ..., plan: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., created_at: _Optional[str] = ..., status: _Optional[str] = ..., result: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., error: _Optional[str] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class CompactionPage(_message.Message):
    __slots__ = ("offset", "limit")
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    offset: int
    limit: int
    def __init__(self, offset: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class CompactionList(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[Compaction]
    def __init__(self, items: _Optional[_Iterable[_Union[Compaction, _Mapping]]] = ...) -> None: ...

class PrepareResearchRequest(_message.Message):
    __slots__ = ("receipt_id", "request_id", "specification", "label_convention", "interpretation_reference")
    RECEIPT_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SPECIFICATION_FIELD_NUMBER: _ClassVar[int]
    LABEL_CONVENTION_FIELD_NUMBER: _ClassVar[int]
    INTERPRETATION_REFERENCE_FIELD_NUMBER: _ClassVar[int]
    receipt_id: str
    request_id: str
    specification: ImportSpecification
    label_convention: str
    interpretation_reference: str
    def __init__(self, receipt_id: _Optional[str] = ..., request_id: _Optional[str] = ..., specification: _Optional[_Union[ImportSpecification, _Mapping]] = ..., label_convention: _Optional[str] = ..., interpretation_reference: _Optional[str] = ...) -> None: ...

class AssembleResearchRequest(_message.Message):
    __slots__ = ("snapshot_ids",)
    SNAPSHOT_IDS_FIELD_NUMBER: _ClassVar[int]
    snapshot_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, snapshot_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class CatalogSnapshot(_message.Message):
    __slots__ = ("snapshot_id", "exchange", "product", "contract", "files", "reference", "time_basis", "fee_basis", "series", "entity_type")
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_FIELD_NUMBER: _ClassVar[int]
    FILES_FIELD_NUMBER: _ClassVar[int]
    REFERENCE_FIELD_NUMBER: _ClassVar[int]
    TIME_BASIS_FIELD_NUMBER: _ClassVar[int]
    FEE_BASIS_FIELD_NUMBER: _ClassVar[int]
    SERIES_FIELD_NUMBER: _ClassVar[int]
    ENTITY_TYPE_FIELD_NUMBER: _ClassVar[int]
    snapshot_id: str
    exchange: str
    product: str
    contract: str
    files: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    reference: _struct_pb2.Struct
    time_basis: str
    fee_basis: str
    series: str
    entity_type: str
    def __init__(self, snapshot_id: _Optional[str] = ..., exchange: _Optional[str] = ..., product: _Optional[str] = ..., contract: _Optional[str] = ..., files: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., reference: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., time_basis: _Optional[str] = ..., fee_basis: _Optional[str] = ..., series: _Optional[str] = ..., entity_type: _Optional[str] = ...) -> None: ...

class CatalogQuery(_message.Message):
    __slots__ = ("domain", "contract", "start", "end", "offset", "limit", "series")
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    SERIES_FIELD_NUMBER: _ClassVar[int]
    domain: str
    contract: str
    start: str
    end: str
    offset: int
    limit: int
    series: str
    def __init__(self, domain: _Optional[str] = ..., contract: _Optional[str] = ..., start: _Optional[str] = ..., end: _Optional[str] = ..., offset: _Optional[int] = ..., limit: _Optional[int] = ..., series: _Optional[str] = ...) -> None: ...

class CatalogRows(_message.Message):
    __slots__ = ("snapshot_id", "domain", "rows", "total", "offset", "limit")
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    ROWS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    snapshot_id: str
    domain: str
    rows: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    total: int
    offset: int
    limit: int
    def __init__(self, snapshot_id: _Optional[str] = ..., domain: _Optional[str] = ..., rows: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., total: _Optional[int] = ..., offset: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class SeriesQuery(_message.Message):
    __slots__ = ("dataset", "search", "offset")
    DATASET_FIELD_NUMBER: _ClassVar[int]
    SEARCH_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    dataset: str
    search: str
    offset: int
    def __init__(self, dataset: _Optional[str] = ..., search: _Optional[str] = ..., offset: _Optional[int] = ...) -> None: ...

class SeriesVersions(_message.Message):
    __slots__ = ("dataset", "scope", "offset")
    DATASET_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    dataset: str
    scope: str
    offset: int
    def __init__(self, dataset: _Optional[str] = ..., scope: _Optional[str] = ..., offset: _Optional[int] = ...) -> None: ...

class SeriesIdentity(_message.Message):
    __slots__ = ("dataset", "scope")
    DATASET_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    dataset: str
    scope: str
    def __init__(self, dataset: _Optional[str] = ..., scope: _Optional[str] = ...) -> None: ...

class SeriesRows(_message.Message):
    __slots__ = ("rows", "total")
    ROWS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    rows: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    total: int
    def __init__(self, rows: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., total: _Optional[int] = ...) -> None: ...

class SeriesRetry(_message.Message):
    __slots__ = ("retried",)
    RETRIED_FIELD_NUMBER: _ClassVar[int]
    retried: int
    def __init__(self, retried: _Optional[int] = ...) -> None: ...
