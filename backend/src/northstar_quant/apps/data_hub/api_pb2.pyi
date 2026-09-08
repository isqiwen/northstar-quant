from google.protobuf import struct_pb2 as _struct_pb2
from northstar_quant.web import api_options_pb2 as _api_options_pb2
from northstar_quant.web import common_pb2 as _common_pb2
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

class BrowserSession(_message.Message):
    __slots__ = ("csrf",)
    CSRF_FIELD_NUMBER: _ClassVar[int]
    csrf: str
    def __init__(self, csrf: _Optional[str] = ...) -> None: ...

class DatasetDetails(_message.Message):
    __slots__ = ("availability_basis", "availability_note", "bar_count", "content_hash", "exchange", "import_spec", "limitations", "processing_provenance", "product", "published_at", "quality", "semantics", "session_close", "session_open", "snapshot_id", "source_reference", "sources", "symbol", "trading_day", "null_fields")
    AVAILABILITY_BASIS_FIELD_NUMBER: _ClassVar[int]
    AVAILABILITY_NOTE_FIELD_NUMBER: _ClassVar[int]
    BAR_COUNT_FIELD_NUMBER: _ClassVar[int]
    CONTENT_HASH_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    IMPORT_SPEC_FIELD_NUMBER: _ClassVar[int]
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
    TRADING_DAY_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    availability_basis: str
    availability_note: str
    bar_count: int
    content_hash: str
    exchange: str
    import_spec: ImportSpecification
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
    trading_day: str
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, availability_basis: _Optional[str] = ..., availability_note: _Optional[str] = ..., bar_count: _Optional[int] = ..., content_hash: _Optional[str] = ..., exchange: _Optional[str] = ..., import_spec: _Optional[_Union[ImportSpecification, _Mapping]] = ..., limitations: _Optional[_Iterable[str]] = ..., processing_provenance: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., product: _Optional[str] = ..., published_at: _Optional[str] = ..., quality: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., semantics: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., session_close: _Optional[str] = ..., session_open: _Optional[str] = ..., snapshot_id: _Optional[str] = ..., source_reference: _Optional[str] = ..., sources: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., symbol: _Optional[str] = ..., trading_day: _Optional[str] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

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
    __slots__ = ("bar_count", "content_hash", "exchange", "product", "published_at", "session_close", "session_open", "snapshot_id", "symbol", "trading_day")
    BAR_COUNT_FIELD_NUMBER: _ClassVar[int]
    CONTENT_HASH_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_FIELD_NUMBER: _ClassVar[int]
    PUBLISHED_AT_FIELD_NUMBER: _ClassVar[int]
    SESSION_CLOSE_FIELD_NUMBER: _ClassVar[int]
    SESSION_OPEN_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    TRADING_DAY_FIELD_NUMBER: _ClassVar[int]
    bar_count: int
    content_hash: str
    exchange: str
    product: str
    published_at: str
    session_close: str
    session_open: str
    snapshot_id: str
    symbol: str
    trading_day: str
    def __init__(self, bar_count: _Optional[int] = ..., content_hash: _Optional[str] = ..., exchange: _Optional[str] = ..., product: _Optional[str] = ..., published_at: _Optional[str] = ..., session_close: _Optional[str] = ..., session_open: _Optional[str] = ..., snapshot_id: _Optional[str] = ..., symbol: _Optional[str] = ..., trading_day: _Optional[str] = ...) -> None: ...

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

class ImportRequest(_message.Message):
    __slots__ = ("allow_download", "allow_retention", "content_base64", "filename", "input_kind", "request_id", "source_name", "spec", "transformation_note", "upstream_source_id", "use_basis", "null_fields")
    ALLOW_DOWNLOAD_FIELD_NUMBER: _ClassVar[int]
    ALLOW_RETENTION_FIELD_NUMBER: _ClassVar[int]
    CONTENT_BASE64_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    INPUT_KIND_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    SPEC_FIELD_NUMBER: _ClassVar[int]
    TRANSFORMATION_NOTE_FIELD_NUMBER: _ClassVar[int]
    UPSTREAM_SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    USE_BASIS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    allow_download: bool
    allow_retention: bool
    content_base64: str
    filename: str
    input_kind: str
    request_id: str
    source_name: str
    spec: _struct_pb2.Struct
    transformation_note: str
    upstream_source_id: str
    use_basis: str
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, allow_download: _Optional[bool] = ..., allow_retention: _Optional[bool] = ..., content_base64: _Optional[str] = ..., filename: _Optional[str] = ..., input_kind: _Optional[str] = ..., request_id: _Optional[str] = ..., source_name: _Optional[str] = ..., spec: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., transformation_note: _Optional[str] = ..., upstream_source_id: _Optional[str] = ..., use_basis: _Optional[str] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class ImportSpecification(_message.Message):
    __slots__ = ("availability_basis", "availability_note", "currency", "exchange", "multiplier", "price_tick", "product", "quantity_unit", "session_close", "session_open", "source_name", "source_reference", "symbol", "timezone", "trading_day")
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
    def __init__(self, availability_basis: _Optional[str] = ..., availability_note: _Optional[str] = ..., currency: _Optional[str] = ..., exchange: _Optional[str] = ..., multiplier: _Optional[str] = ..., price_tick: _Optional[str] = ..., product: _Optional[str] = ..., quantity_unit: _Optional[str] = ..., session_close: _Optional[str] = ..., session_open: _Optional[str] = ..., source_name: _Optional[str] = ..., source_reference: _Optional[str] = ..., symbol: _Optional[str] = ..., timezone: _Optional[str] = ..., trading_day: _Optional[str] = ...) -> None: ...

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

class ReprocessRequest(_message.Message):
    __slots__ = ("request_id", "spec")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SPEC_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    spec: _struct_pb2.Struct
    def __init__(self, request_id: _Optional[str] = ..., spec: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

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
