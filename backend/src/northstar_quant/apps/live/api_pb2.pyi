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

class InstanceRecord(_message.Message):
    __slots__ = ("instance_id", "environment", "broker_profile")
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    BROKER_PROFILE_FIELD_NUMBER: _ClassVar[int]
    instance_id: str
    environment: str
    broker_profile: str
    def __init__(self, instance_id: _Optional[str] = ..., environment: _Optional[str] = ..., broker_profile: _Optional[str] = ...) -> None: ...

class InstanceCatalog(_message.Message):
    __slots__ = ("instances", "production_available")
    INSTANCES_FIELD_NUMBER: _ClassVar[int]
    PRODUCTION_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    instances: _containers.RepeatedCompositeFieldContainer[InstanceRecord]
    production_available: bool
    def __init__(self, instances: _Optional[_Iterable[_Union[InstanceRecord, _Mapping]]] = ..., production_available: _Optional[bool] = ...) -> None: ...

class AccountCatchupRequest(_message.Message):
    __slots__ = ("baseline_id", "request_id", "through_sequence")
    BASELINE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    THROUGH_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    baseline_id: str
    request_id: str
    through_sequence: int
    def __init__(self, baseline_id: _Optional[str] = ..., request_id: _Optional[str] = ..., through_sequence: _Optional[int] = ...) -> None: ...

class AccountProgress(_message.Message):
    __slots__ = ("status", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    status: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, status: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class ArchiveAttempt(_message.Message):
    __slots__ = ("attempt_id", "parameters", "snapshot_id", "source_id", "status", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    ATTEMPT_ID_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    attempt_id: str
    parameters: _struct_pb2.Struct
    snapshot_id: str
    source_id: str
    status: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, attempt_id: _Optional[str] = ..., parameters: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., snapshot_id: _Optional[str] = ..., source_id: _Optional[str] = ..., status: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class ArchiveDataset(_message.Message):
    __slots__ = ("availability_basis", "availability_note", "bar_count", "content_hash", "exchange", "import_specs", "limitations", "live_runtime", "processing_provenance", "product", "published_at", "quality", "semantics", "session_close", "session_open", "snapshot_id", "source_reference", "sources", "symbol", "trading_days", "null_fields", "settlements", "terms")
    AVAILABILITY_BASIS_FIELD_NUMBER: _ClassVar[int]
    AVAILABILITY_NOTE_FIELD_NUMBER: _ClassVar[int]
    BAR_COUNT_FIELD_NUMBER: _ClassVar[int]
    CONTENT_HASH_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    IMPORT_SPECS_FIELD_NUMBER: _ClassVar[int]
    LIMITATIONS_FIELD_NUMBER: _ClassVar[int]
    LIVE_RUNTIME_FIELD_NUMBER: _ClassVar[int]
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
    live_runtime: _struct_pb2.Struct
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
    def __init__(self, availability_basis: _Optional[str] = ..., availability_note: _Optional[str] = ..., bar_count: _Optional[int] = ..., content_hash: _Optional[str] = ..., exchange: _Optional[str] = ..., import_specs: _Optional[_Iterable[_Union[ImportSpecification, _Mapping]]] = ..., limitations: _Optional[_Iterable[str]] = ..., live_runtime: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., processing_provenance: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., product: _Optional[str] = ..., published_at: _Optional[str] = ..., quality: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., semantics: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., session_close: _Optional[str] = ..., session_open: _Optional[str] = ..., snapshot_id: _Optional[str] = ..., source_reference: _Optional[str] = ..., sources: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., symbol: _Optional[str] = ..., trading_days: _Optional[_Iterable[str]] = ..., null_fields: _Optional[_Iterable[str]] = ..., settlements: _Optional[_Iterable[_Union[_accounting_pb2.SettlementFact, _Mapping]]] = ..., terms: _Optional[_Iterable[_Union[_accounting_pb2.FuturesTerms, _Mapping]]] = ...) -> None: ...

class ArchiveReprocessRequest(_message.Message):
    __slots__ = ("request_id", "spec")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SPEC_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    spec: _struct_pb2.Struct
    def __init__(self, request_id: _Optional[str] = ..., spec: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class ArchiveRequest(_message.Message):
    __slots__ = ("allow_download", "request_id", "session_close", "session_open", "through_sequence")
    ALLOW_DOWNLOAD_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_CLOSE_FIELD_NUMBER: _ClassVar[int]
    SESSION_OPEN_FIELD_NUMBER: _ClassVar[int]
    THROUGH_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    allow_download: bool
    request_id: str
    session_close: str
    session_open: str
    through_sequence: int
    def __init__(self, allow_download: _Optional[bool] = ..., request_id: _Optional[str] = ..., session_close: _Optional[str] = ..., session_open: _Optional[str] = ..., through_sequence: _Optional[int] = ...) -> None: ...

class ArchiveSource(_message.Message):
    __slots__ = ("allow_download", "filename", "source_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    ALLOW_DOWNLOAD_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    allow_download: bool
    filename: str
    source_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, allow_download: _Optional[bool] = ..., filename: _Optional[str] = ..., source_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class BaselineCheckRequest(_message.Message):
    __slots__ = ("baseline_id", "query_batch_id", "request_id")
    BASELINE_ID_FIELD_NUMBER: _ClassVar[int]
    QUERY_BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    baseline_id: str
    query_batch_id: str
    request_id: str
    def __init__(self, baseline_id: _Optional[str] = ..., query_batch_id: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class BaselineContext(_message.Message):
    __slots__ = ("baseline", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    BASELINE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    baseline: BaselineRecord
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, baseline: _Optional[_Union[BaselineRecord, _Mapping]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class BaselineRecord(_message.Message):
    __slots__ = ("baseline_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    BASELINE_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    baseline_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, baseline_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class BaselineRequest(_message.Message):
    __slots__ = ("request_id", "source_batch_id")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    source_batch_id: str
    def __init__(self, request_id: _Optional[str] = ..., source_batch_id: _Optional[str] = ...) -> None: ...

class BrokerStatus(_message.Message):
    __slots__ = ("connection", "credentials", "execution", "profiles", "sdk", "evidence_fields")
    class ExecutionEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: bool
        def __init__(self, key: _Optional[str] = ..., value: _Optional[bool] = ...) -> None: ...
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    CONNECTION_FIELD_NUMBER: _ClassVar[int]
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_FIELD_NUMBER: _ClassVar[int]
    PROFILES_FIELD_NUMBER: _ClassVar[int]
    SDK_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    connection: str
    credentials: _struct_pb2.Struct
    execution: _containers.ScalarMap[str, bool]
    profiles: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    sdk: _struct_pb2.Struct
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, connection: _Optional[str] = ..., credentials: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., execution: _Optional[_Mapping[str, bool]] = ..., profiles: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., sdk: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class BudgetContext(_message.Message):
    __slots__ = ("budgets", "live_runtime", "order_checks", "null_fields")
    BUDGETS_FIELD_NUMBER: _ClassVar[int]
    LIVE_RUNTIME_FIELD_NUMBER: _ClassVar[int]
    ORDER_CHECKS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    budgets: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    live_runtime: _struct_pb2.Struct
    order_checks: _containers.RepeatedCompositeFieldContainer[CheckRecord]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, budgets: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., live_runtime: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., order_checks: _Optional[_Iterable[_Union[CheckRecord, _Mapping]]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class CheckRecord(_message.Message):
    __slots__ = ("check_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    CHECK_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    check_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, check_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class CommandRecord(_message.Message):
    __slots__ = ("request_id", "status", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    status: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, request_id: _Optional[str] = ..., status: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class CompletedMinute(_message.Message):
    __slots__ = ("close", "completed_at", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    CLOSE_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    close: str
    completed_at: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, close: _Optional[str] = ..., completed_at: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class ControlRequest(_message.Message):
    __slots__ = ("action", "request_id")
    ACTION_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    action: str
    request_id: str
    def __init__(self, action: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class Diagnostics(_message.Message):
    __slots__ = ("database", "duration_ms", "observed_at", "scope", "source_filesystem", "status", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    DATABASE_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    OBSERVED_AT_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FILESYSTEM_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    database: _struct_pb2.Struct
    duration_ms: int
    observed_at: str
    scope: str
    source_filesystem: _struct_pb2.Struct
    status: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, database: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., duration_ms: _Optional[int] = ..., observed_at: _Optional[str] = ..., scope: _Optional[str] = ..., source_filesystem: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., status: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class FundsContext(_message.Message):
    __slots__ = ("entries", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    ENTRIES_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    entries: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, entries: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class FundsEntry(_message.Message):
    __slots__ = ("entry_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    ENTRY_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    entry_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, entry_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class FundsEntryRequest(_message.Message):
    __slots__ = ("baseline_id", "request_id", "source_batch_id")
    BASELINE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    baseline_id: str
    request_id: str
    source_batch_id: str
    def __init__(self, baseline_id: _Optional[str] = ..., request_id: _Optional[str] = ..., source_batch_id: _Optional[str] = ...) -> None: ...

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
    __slots__ = ("session_kind", "availability_basis", "availability_note", "currency", "exchange", "multiplier", "price_tick", "product", "quantity_unit", "session_close", "session_open", "source_name", "source_reference", "symbol", "timezone", "trading_day")
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
    def __init__(self, session_kind: _Optional[str] = ..., availability_basis: _Optional[str] = ..., availability_note: _Optional[str] = ..., currency: _Optional[str] = ..., exchange: _Optional[str] = ..., multiplier: _Optional[str] = ..., price_tick: _Optional[str] = ..., product: _Optional[str] = ..., quantity_unit: _Optional[str] = ..., session_close: _Optional[str] = ..., session_open: _Optional[str] = ..., source_name: _Optional[str] = ..., source_reference: _Optional[str] = ..., symbol: _Optional[str] = ..., timezone: _Optional[str] = ..., trading_day: _Optional[str] = ...) -> None: ...

class LedgerContext(_message.Message):
    __slots__ = ("baseline", "baseline_id", "checks", "entries", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    BASELINE_FIELD_NUMBER: _ClassVar[int]
    BASELINE_ID_FIELD_NUMBER: _ClassVar[int]
    CHECKS_FIELD_NUMBER: _ClassVar[int]
    ENTRIES_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    baseline: BaselineRecord
    baseline_id: str
    checks: _containers.RepeatedCompositeFieldContainer[CheckRecord]
    entries: _containers.RepeatedCompositeFieldContainer[PositionEntry]
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, baseline: _Optional[_Union[BaselineRecord, _Mapping]] = ..., baseline_id: _Optional[str] = ..., checks: _Optional[_Iterable[_Union[CheckRecord, _Mapping]]] = ..., entries: _Optional[_Iterable[_Union[PositionEntry, _Mapping]]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class LiveConfiguration(_message.Message):
    __slots__ = ("candidate_id", "config", "configuration_id", "name", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    config: _struct_pb2.Struct
    configuration_id: str
    name: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, candidate_id: _Optional[str] = ..., config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., configuration_id: _Optional[str] = ..., name: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class MaterialRequest(_message.Message):
    __slots__ = ("candidate", "request_id")
    CANDIDATE_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    candidate: _struct_pb2.Struct
    request_id: str
    def __init__(self, candidate: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., request_id: _Optional[str] = ...) -> None: ...

class OpeningBudget(_message.Message):
    __slots__ = ("budget_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    BUDGET_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    budget_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, budget_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class OpeningBudgetRequest(_message.Message):
    __slots__ = ("limit_price", "order_check_id", "request_id", "sequence")
    LIMIT_PRICE_FIELD_NUMBER: _ClassVar[int]
    ORDER_CHECK_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    limit_price: str
    order_check_id: str
    request_id: str
    sequence: int
    def __init__(self, limit_price: _Optional[str] = ..., order_check_id: _Optional[str] = ..., request_id: _Optional[str] = ..., sequence: _Optional[int] = ...) -> None: ...

class OrderCheckRequest(_message.Message):
    __slots__ = ("position_check_id", "request_id")
    POSITION_CHECK_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    position_check_id: str
    request_id: str
    def __init__(self, position_check_id: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class PositionCheckRequest(_message.Message):
    __slots__ = ("entry_id", "query_batch_id", "request_id")
    ENTRY_ID_FIELD_NUMBER: _ClassVar[int]
    QUERY_BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    entry_id: str
    query_batch_id: str
    request_id: str
    def __init__(self, entry_id: _Optional[str] = ..., query_batch_id: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class PositionEntry(_message.Message):
    __slots__ = ("entry_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    ENTRY_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    entry_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, entry_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class PositionEntryRequest(_message.Message):
    __slots__ = ("baseline_id", "request_id", "source_batch_id")
    BASELINE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    baseline_id: str
    request_id: str
    source_batch_id: str
    def __init__(self, baseline_id: _Optional[str] = ..., request_id: _Optional[str] = ..., source_batch_id: _Optional[str] = ...) -> None: ...

class QueryRecord(_message.Message):
    __slots__ = ("batch_id", "instrument", "status", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    batch_id: str
    instrument: str
    status: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, batch_id: _Optional[str] = ..., instrument: _Optional[str] = ..., status: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class QueryRequest(_message.Message):
    __slots__ = ("instrument", "request_id")
    INSTRUMENT_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    instrument: str
    request_id: str
    def __init__(self, instrument: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class Readiness(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: str
    def __init__(self, status: _Optional[str] = ...) -> None: ...

class RuntimeStatus(_message.Message):
    __slots__ = ("instance_id", "environment", "broker_profile", "null_fields", "cancel_sending", "control_available", "observed_at", "order_sending", "pid", "protocol", "release", "runtime_id", "started_at", "status")
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    BROKER_PROFILE_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    CANCEL_SENDING_FIELD_NUMBER: _ClassVar[int]
    CONTROL_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    OBSERVED_AT_FIELD_NUMBER: _ClassVar[int]
    ORDER_SENDING_FIELD_NUMBER: _ClassVar[int]
    PID_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_FIELD_NUMBER: _ClassVar[int]
    RELEASE_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_ID_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    instance_id: str
    environment: str
    broker_profile: str
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    cancel_sending: bool
    control_available: bool
    observed_at: str
    order_sending: bool
    pid: int
    protocol: str
    release: str
    runtime_id: str
    started_at: str
    status: str
    def __init__(self, instance_id: _Optional[str] = ..., environment: _Optional[str] = ..., broker_profile: _Optional[str] = ..., null_fields: _Optional[_Iterable[str]] = ..., cancel_sending: _Optional[bool] = ..., control_available: _Optional[bool] = ..., observed_at: _Optional[str] = ..., order_sending: _Optional[bool] = ..., pid: _Optional[int] = ..., protocol: _Optional[str] = ..., release: _Optional[str] = ..., runtime_id: _Optional[str] = ..., started_at: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class StrategyMaterial(_message.Message):
    __slots__ = ("candidate_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, candidate_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class StreamAccountProgress(_message.Message):
    __slots__ = ("status", "through_sequence", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    STATUS_FIELD_NUMBER: _ClassVar[int]
    THROUGH_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    status: str
    through_sequence: int
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, status: _Optional[str] = ..., through_sequence: _Optional[int] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class StreamArchive(_message.Message):
    __slots__ = ("source_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    source_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, source_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class StreamBinding(_message.Message):
    __slots__ = ("request", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    REQUEST_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    request: StreamBindingRequest
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, request: _Optional[_Union[StreamBindingRequest, _Mapping]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class StreamBindingRequest(_message.Message):
    __slots__ = ("query_batch_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    QUERY_BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    query_batch_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, query_batch_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class StreamControl(_message.Message):
    __slots__ = ("stream_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    STREAM_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    stream_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, stream_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class StreamDecision(_message.Message):
    __slots__ = ("sequence", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    sequence: int
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, sequence: _Optional[int] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class StreamDetail(_message.Message):
    __slots__ = ("account_progress", "archives", "binding", "connection", "cursor", "paused", "received", "steps", "stream_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    ACCOUNT_PROGRESS_FIELD_NUMBER: _ClassVar[int]
    ARCHIVES_FIELD_NUMBER: _ClassVar[int]
    BINDING_FIELD_NUMBER: _ClassVar[int]
    CONNECTION_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    PAUSED_FIELD_NUMBER: _ClassVar[int]
    RECEIVED_FIELD_NUMBER: _ClassVar[int]
    STEPS_FIELD_NUMBER: _ClassVar[int]
    STREAM_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    account_progress: StreamAccountProgress
    archives: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    binding: StreamBinding
    connection: str
    cursor: int
    paused: bool
    received: int
    steps: _containers.RepeatedCompositeFieldContainer[StreamStep]
    stream_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, account_progress: _Optional[_Union[StreamAccountProgress, _Mapping]] = ..., archives: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., binding: _Optional[_Union[StreamBinding, _Mapping]] = ..., connection: _Optional[str] = ..., cursor: _Optional[int] = ..., paused: _Optional[bool] = ..., received: _Optional[int] = ..., steps: _Optional[_Iterable[_Union[StreamStep, _Mapping]]] = ..., stream_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class StreamEvent(_message.Message):
    __slots__ = ("committed_at", "event")
    COMMITTED_AT_FIELD_NUMBER: _ClassVar[int]
    EVENT_FIELD_NUMBER: _ClassVar[int]
    committed_at: str
    event: _struct_pb2.Struct
    def __init__(self, committed_at: _Optional[str] = ..., event: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class StreamPositionsRequest(_message.Message):
    __slots__ = ("baseline_id", "request_id", "through_sequence")
    BASELINE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    THROUGH_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    baseline_id: str
    request_id: str
    through_sequence: int
    def __init__(self, baseline_id: _Optional[str] = ..., request_id: _Optional[str] = ..., through_sequence: _Optional[int] = ...) -> None: ...

class StreamRequest(_message.Message):
    __slots__ = ("allow_retention", "configuration_id", "duration_seconds", "query_batch_id", "request_id", "use_basis")
    ALLOW_RETENTION_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    DURATION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    QUERY_BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    USE_BASIS_FIELD_NUMBER: _ClassVar[int]
    allow_retention: bool
    configuration_id: str
    duration_seconds: int
    query_batch_id: str
    request_id: str
    use_basis: str
    def __init__(self, allow_retention: _Optional[bool] = ..., configuration_id: _Optional[str] = ..., duration_seconds: _Optional[int] = ..., query_batch_id: _Optional[str] = ..., request_id: _Optional[str] = ..., use_basis: _Optional[str] = ...) -> None: ...

class StreamStep(_message.Message):
    __slots__ = ("committed_at", "result", "sequence")
    COMMITTED_AT_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    committed_at: str
    result: StreamStepResult
    sequence: int
    def __init__(self, committed_at: _Optional[str] = ..., result: _Optional[_Union[StreamStepResult, _Mapping]] = ..., sequence: _Optional[int] = ...) -> None: ...

class StreamStepResult(_message.Message):
    __slots__ = ("bar", "intent", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    BAR_FIELD_NUMBER: _ClassVar[int]
    INTENT_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    bar: CompletedMinute
    intent: _struct_pb2.Struct
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, bar: _Optional[_Union[CompletedMinute, _Mapping]] = ..., intent: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class StreamSummary(_message.Message):
    __slots__ = ("stream_id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    STREAM_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    stream_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, stream_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class GetApiBrokerQueriesResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[QueryRecord]
    def __init__(self, items: _Optional[_Iterable[_Union[QueryRecord, _Mapping]]] = ...) -> None: ...

class GetApiConfigurationsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[LiveConfiguration]
    def __init__(self, items: _Optional[_Iterable[_Union[LiveConfiguration, _Mapping]]] = ...) -> None: ...

class GetApiStrategyMaterialsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[StrategyMaterial]
    def __init__(self, items: _Optional[_Iterable[_Union[StrategyMaterial, _Mapping]]] = ...) -> None: ...

class GetApiStreamsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[StreamSummary]
    def __init__(self, items: _Optional[_Iterable[_Union[StreamSummary, _Mapping]]] = ...) -> None: ...

class GetApiStreamsStreamIdEventsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[StreamEvent]
    def __init__(self, items: _Optional[_Iterable[_Union[StreamEvent, _Mapping]]] = ...) -> None: ...

class OrderReservation(_message.Message):
    __slots__ = ("reserved_fee", "reserved_margin", "reserved_gross", "reserved_loss", "reserved_close_lots")
    RESERVED_FEE_FIELD_NUMBER: _ClassVar[int]
    RESERVED_MARGIN_FIELD_NUMBER: _ClassVar[int]
    RESERVED_GROSS_FIELD_NUMBER: _ClassVar[int]
    RESERVED_LOSS_FIELD_NUMBER: _ClassVar[int]
    RESERVED_CLOSE_LOTS_FIELD_NUMBER: _ClassVar[int]
    reserved_fee: str
    reserved_margin: str
    reserved_gross: str
    reserved_loss: str
    reserved_close_lots: int
    def __init__(self, reserved_fee: _Optional[str] = ..., reserved_margin: _Optional[str] = ..., reserved_gross: _Optional[str] = ..., reserved_loss: _Optional[str] = ..., reserved_close_lots: _Optional[int] = ...) -> None: ...

class LocalOrder(_message.Message):
    __slots__ = ("order_id", "contract_id", "authorization_id", "runtime_id", "attempt_id", "status", "quantity_lots", "filled_lots", "requires_reconciliation", "reservation", "order", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHORIZATION_ID_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_ID_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_LOTS_FIELD_NUMBER: _ClassVar[int]
    FILLED_LOTS_FIELD_NUMBER: _ClassVar[int]
    REQUIRES_RECONCILIATION_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    contract_id: str
    authorization_id: str
    runtime_id: str
    attempt_id: str
    status: str
    quantity_lots: int
    filled_lots: int
    requires_reconciliation: bool
    reservation: OrderReservation
    order: _struct_pb2.Struct
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, order_id: _Optional[str] = ..., contract_id: _Optional[str] = ..., authorization_id: _Optional[str] = ..., runtime_id: _Optional[str] = ..., attempt_id: _Optional[str] = ..., status: _Optional[str] = ..., quantity_lots: _Optional[int] = ..., filled_lots: _Optional[int] = ..., requires_reconciliation: _Optional[bool] = ..., reservation: _Optional[_Union[OrderReservation, _Mapping]] = ..., order: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class LocalOrderEvent(_message.Message):
    __slots__ = ("sequence", "event_id", "order_id", "kind", "recorded_at", "document")
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    RECORDED_AT_FIELD_NUMBER: _ClassVar[int]
    DOCUMENT_FIELD_NUMBER: _ClassVar[int]
    sequence: int
    event_id: str
    order_id: str
    kind: str
    recorded_at: str
    document: _struct_pb2.Struct
    def __init__(self, sequence: _Optional[int] = ..., event_id: _Optional[str] = ..., order_id: _Optional[str] = ..., kind: _Optional[str] = ..., recorded_at: _Optional[str] = ..., document: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class LocalOrderPage(_message.Message):
    __slots__ = ("orders", "next_before", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    ORDERS_FIELD_NUMBER: _ClassVar[int]
    NEXT_BEFORE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    orders: _containers.RepeatedCompositeFieldContainer[LocalOrder]
    next_before: int
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, orders: _Optional[_Iterable[_Union[LocalOrder, _Mapping]]] = ..., next_before: _Optional[int] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class LocalOrderDetail(_message.Message):
    __slots__ = ("record", "events", "next_after", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    RECORD_FIELD_NUMBER: _ClassVar[int]
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    NEXT_AFTER_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    record: LocalOrder
    events: _containers.RepeatedCompositeFieldContainer[LocalOrderEvent]
    next_after: int
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, record: _Optional[_Union[LocalOrder, _Mapping]] = ..., events: _Optional[_Iterable[_Union[LocalOrderEvent, _Mapping]]] = ..., next_after: _Optional[int] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class ExecutionConsent(_message.Message):
    __slots__ = ("authorization_id", "status", "requires_current_admission", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    AUTHORIZATION_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    REQUIRES_CURRENT_ADMISSION_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    authorization_id: str
    status: str
    requires_current_admission: bool
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, authorization_id: _Optional[str] = ..., status: _Optional[str] = ..., requires_current_admission: _Optional[bool] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class ConsentPage(_message.Message):
    __slots__ = ("authorizations", "next_before", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    AUTHORIZATIONS_FIELD_NUMBER: _ClassVar[int]
    NEXT_BEFORE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    authorizations: _containers.RepeatedCompositeFieldContainer[ExecutionConsent]
    next_before: int
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, authorizations: _Optional[_Iterable[_Union[ExecutionConsent, _Mapping]]] = ..., next_before: _Optional[int] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class ConsentRequest(_message.Message):
    __slots__ = ("request_id", "expires_at", "max_order_lots", "max_total_lots", "fee", "margin", "gross", "loss")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    MAX_ORDER_LOTS_FIELD_NUMBER: _ClassVar[int]
    MAX_TOTAL_LOTS_FIELD_NUMBER: _ClassVar[int]
    FEE_FIELD_NUMBER: _ClassVar[int]
    MARGIN_FIELD_NUMBER: _ClassVar[int]
    GROSS_FIELD_NUMBER: _ClassVar[int]
    LOSS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    expires_at: str
    max_order_lots: int
    max_total_lots: int
    fee: str
    margin: str
    gross: str
    loss: str
    def __init__(self, request_id: _Optional[str] = ..., expires_at: _Optional[str] = ..., max_order_lots: _Optional[int] = ..., max_total_lots: _Optional[int] = ..., fee: _Optional[str] = ..., margin: _Optional[str] = ..., gross: _Optional[str] = ..., loss: _Optional[str] = ...) -> None: ...

class RevokeConsent(_message.Message):
    __slots__ = ("request_id",)
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    def __init__(self, request_id: _Optional[str] = ...) -> None: ...
