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

class AdvanceRequest(_message.Message):
    __slots__ = ("request_id",)
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    def __init__(self, request_id: _Optional[str] = ...) -> None: ...

class Annotation(_message.Message):
    __slots__ = ("at", "description")
    AT_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    at: str
    description: str
    def __init__(self, at: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class AnnotationRequest(_message.Message):
    __slots__ = ("description",)
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    description: str
    def __init__(self, description: _Optional[str] = ...) -> None: ...

class Catalog(_message.Message):
    __slots__ = ("factors", "strategies")
    FACTORS_FIELD_NUMBER: _ClassVar[int]
    STRATEGIES_FIELD_NUMBER: _ClassVar[int]
    factors: _containers.RepeatedCompositeFieldContainer[FactorSummary]
    strategies: _containers.RepeatedCompositeFieldContainer[StrategySummary]
    def __init__(self, factors: _Optional[_Iterable[_Union[FactorSummary, _Mapping]]] = ..., strategies: _Optional[_Iterable[_Union[StrategySummary, _Mapping]]] = ...) -> None: ...

class Comparison(_message.Message):
    __slots__ = ("bar_count", "decision_count", "ending_cash", "ending_equity", "ending_position_lots", "fill_count", "initial_cash", "max_drawdown", "max_drawdown_fraction", "realized_pnl", "run_id", "strategy", "total_fees", "total_return", "unrealized_pnl")
    BAR_COUNT_FIELD_NUMBER: _ClassVar[int]
    DECISION_COUNT_FIELD_NUMBER: _ClassVar[int]
    ENDING_CASH_FIELD_NUMBER: _ClassVar[int]
    ENDING_EQUITY_FIELD_NUMBER: _ClassVar[int]
    ENDING_POSITION_LOTS_FIELD_NUMBER: _ClassVar[int]
    FILL_COUNT_FIELD_NUMBER: _ClassVar[int]
    INITIAL_CASH_FIELD_NUMBER: _ClassVar[int]
    MAX_DRAWDOWN_FIELD_NUMBER: _ClassVar[int]
    MAX_DRAWDOWN_FRACTION_FIELD_NUMBER: _ClassVar[int]
    REALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FEES_FIELD_NUMBER: _ClassVar[int]
    TOTAL_RETURN_FIELD_NUMBER: _ClassVar[int]
    UNREALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    bar_count: int
    decision_count: int
    ending_cash: str
    ending_equity: str
    ending_position_lots: int
    fill_count: int
    initial_cash: str
    max_drawdown: str
    max_drawdown_fraction: str
    realized_pnl: str
    run_id: str
    strategy: str
    total_fees: str
    total_return: str
    unrealized_pnl: str
    def __init__(self, bar_count: _Optional[int] = ..., decision_count: _Optional[int] = ..., ending_cash: _Optional[str] = ..., ending_equity: _Optional[str] = ..., ending_position_lots: _Optional[int] = ..., fill_count: _Optional[int] = ..., initial_cash: _Optional[str] = ..., max_drawdown: _Optional[str] = ..., max_drawdown_fraction: _Optional[str] = ..., realized_pnl: _Optional[str] = ..., run_id: _Optional[str] = ..., strategy: _Optional[str] = ..., total_fees: _Optional[str] = ..., total_return: _Optional[str] = ..., unrealized_pnl: _Optional[str] = ...) -> None: ...

class ComparisonRequest(_message.Message):
    __slots__ = ("run_ids",)
    RUN_IDS_FIELD_NUMBER: _ClassVar[int]
    run_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, run_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class ConfigurationRequest(_message.Message):
    __slots__ = ("config", "name")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    config: ResearchConfigurationInput
    name: str
    def __init__(self, config: _Optional[_Union[ResearchConfigurationInput, _Mapping]] = ..., name: _Optional[str] = ...) -> None: ...

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

class EquityPoint(_message.Message):
    __slots__ = ("at", "equity", "observation_id", "evidence_fields", "close", "cash", "position_lots", "realized_pnl", "unrealized_pnl", "total_fees", "drawdown", "drawdown_fraction", "long_lots", "short_lots", "net_exposure", "gross_exposure", "settlement_pnl", "trade_realized_pnl", "terms_id", "margin_used", "available", "reserved_fee", "reserved_margin", "reserved_close_lots", "available_after_reservations", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    AT_FIELD_NUMBER: _ClassVar[int]
    EQUITY_FIELD_NUMBER: _ClassVar[int]
    OBSERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    CLOSE_FIELD_NUMBER: _ClassVar[int]
    CASH_FIELD_NUMBER: _ClassVar[int]
    POSITION_LOTS_FIELD_NUMBER: _ClassVar[int]
    REALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    UNREALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FEES_FIELD_NUMBER: _ClassVar[int]
    DRAWDOWN_FIELD_NUMBER: _ClassVar[int]
    DRAWDOWN_FRACTION_FIELD_NUMBER: _ClassVar[int]
    LONG_LOTS_FIELD_NUMBER: _ClassVar[int]
    SHORT_LOTS_FIELD_NUMBER: _ClassVar[int]
    NET_EXPOSURE_FIELD_NUMBER: _ClassVar[int]
    GROSS_EXPOSURE_FIELD_NUMBER: _ClassVar[int]
    SETTLEMENT_PNL_FIELD_NUMBER: _ClassVar[int]
    TRADE_REALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    TERMS_ID_FIELD_NUMBER: _ClassVar[int]
    MARGIN_USED_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    RESERVED_FEE_FIELD_NUMBER: _ClassVar[int]
    RESERVED_MARGIN_FIELD_NUMBER: _ClassVar[int]
    RESERVED_CLOSE_LOTS_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_AFTER_RESERVATIONS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    at: str
    equity: str
    observation_id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    close: str
    cash: str
    position_lots: int
    realized_pnl: str
    unrealized_pnl: str
    total_fees: str
    drawdown: str
    drawdown_fraction: str
    long_lots: int
    short_lots: int
    net_exposure: str
    gross_exposure: str
    settlement_pnl: str
    trade_realized_pnl: str
    terms_id: str
    margin_used: str
    available: str
    reserved_fee: str
    reserved_margin: str
    reserved_close_lots: int
    available_after_reservations: str
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, at: _Optional[str] = ..., equity: _Optional[str] = ..., observation_id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., close: _Optional[str] = ..., cash: _Optional[str] = ..., position_lots: _Optional[int] = ..., realized_pnl: _Optional[str] = ..., unrealized_pnl: _Optional[str] = ..., total_fees: _Optional[str] = ..., drawdown: _Optional[str] = ..., drawdown_fraction: _Optional[str] = ..., long_lots: _Optional[int] = ..., short_lots: _Optional[int] = ..., net_exposure: _Optional[str] = ..., gross_exposure: _Optional[str] = ..., settlement_pnl: _Optional[str] = ..., trade_realized_pnl: _Optional[str] = ..., terms_id: _Optional[str] = ..., margin_used: _Optional[str] = ..., available: _Optional[str] = ..., reserved_fee: _Optional[str] = ..., reserved_margin: _Optional[str] = ..., reserved_close_lots: _Optional[int] = ..., available_after_reservations: _Optional[str] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class FactorBinding(_message.Message):
    __slots__ = ("code_revision", "factor_id", "parameters", "revision")
    CODE_REVISION_FIELD_NUMBER: _ClassVar[int]
    FACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    code_revision: str
    factor_id: str
    parameters: _struct_pb2.Struct
    revision: str
    def __init__(self, code_revision: _Optional[str] = ..., factor_id: _Optional[str] = ..., parameters: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., revision: _Optional[str] = ...) -> None: ...

class FactorDescription(_message.Message):
    __slots__ = ("capabilities", "category", "description", "factor_id", "limitations", "name", "parameters", "revision")
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    FACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    LIMITATIONS_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    capabilities: _containers.RepeatedScalarFieldContainer[str]
    category: str
    description: str
    factor_id: str
    limitations: str
    name: str
    parameters: _containers.RepeatedCompositeFieldContainer[ParameterDescription]
    revision: str
    def __init__(self, capabilities: _Optional[_Iterable[str]] = ..., category: _Optional[str] = ..., description: _Optional[str] = ..., factor_id: _Optional[str] = ..., limitations: _Optional[str] = ..., name: _Optional[str] = ..., parameters: _Optional[_Iterable[_Union[ParameterDescription, _Mapping]]] = ..., revision: _Optional[str] = ...) -> None: ...

class FactorInput(_message.Message):
    __slots__ = ("factor_id", "parameters")
    FACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    factor_id: str
    parameters: _struct_pb2.Struct
    def __init__(self, factor_id: _Optional[str] = ..., parameters: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class FactorResult(_message.Message):
    __slots__ = ("evaluation", "inputs", "values")
    EVALUATION_FIELD_NUMBER: _ClassVar[int]
    INPUTS_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    evaluation: _struct_pb2.Struct
    inputs: _struct_pb2.Struct
    values: _containers.RepeatedCompositeFieldContainer[FactorValue]
    def __init__(self, evaluation: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., inputs: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., values: _Optional[_Iterable[_Union[FactorValue, _Mapping]]] = ...) -> None: ...

class FactorRevision(_message.Message):
    __slots__ = ("annotations", "binding", "revision_id")
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    BINDING_FIELD_NUMBER: _ClassVar[int]
    REVISION_ID_FIELD_NUMBER: _ClassVar[int]
    annotations: _containers.RepeatedCompositeFieldContainer[Annotation]
    binding: FactorBinding
    revision_id: str
    def __init__(self, annotations: _Optional[_Iterable[_Union[Annotation, _Mapping]]] = ..., binding: _Optional[_Union[FactorBinding, _Mapping]] = ..., revision_id: _Optional[str] = ...) -> None: ...

class FactorRevisionRequest(_message.Message):
    __slots__ = ("factor_id", "parameters")
    FACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    factor_id: str
    parameters: _struct_pb2.Struct
    def __init__(self, factor_id: _Optional[str] = ..., parameters: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class FactorRun(_message.Message):
    __slots__ = ("attempt_id", "code_revision", "completed_at", "created_at", "error", "result", "revision_id", "snapshot_id", "status", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    ATTEMPT_ID_FIELD_NUMBER: _ClassVar[int]
    CODE_REVISION_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    REVISION_ID_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    attempt_id: str
    code_revision: str
    completed_at: str
    created_at: str
    error: str
    result: FactorResult
    revision_id: str
    snapshot_id: str
    status: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, attempt_id: _Optional[str] = ..., code_revision: _Optional[str] = ..., completed_at: _Optional[str] = ..., created_at: _Optional[str] = ..., error: _Optional[str] = ..., result: _Optional[_Union[FactorResult, _Mapping]] = ..., revision_id: _Optional[str] = ..., snapshot_id: _Optional[str] = ..., status: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class FactorRunRequest(_message.Message):
    __slots__ = ("revision_id", "snapshot_id")
    REVISION_ID_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    revision_id: str
    snapshot_id: str
    def __init__(self, revision_id: _Optional[str] = ..., snapshot_id: _Optional[str] = ...) -> None: ...

class FactorSummary(_message.Message):
    __slots__ = ("capabilities", "category", "description", "factor_id", "limitations", "name")
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    FACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    LIMITATIONS_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    capabilities: _containers.RepeatedScalarFieldContainer[str]
    category: str
    description: str
    factor_id: str
    limitations: str
    name: str
    def __init__(self, capabilities: _Optional[_Iterable[str]] = ..., category: _Optional[str] = ..., description: _Optional[str] = ..., factor_id: _Optional[str] = ..., limitations: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class FactorValue(_message.Message):
    __slots__ = ("at", "observation_id", "reason", "status", "value", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    AT_FIELD_NUMBER: _ClassVar[int]
    OBSERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    at: str
    observation_id: str
    reason: str
    status: str
    value: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, at: _Optional[str] = ..., observation_id: _Optional[str] = ..., reason: _Optional[str] = ..., status: _Optional[str] = ..., value: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class FixedFactor(_message.Message):
    __slots__ = ("code_revision", "factor_id", "parameters", "revision")
    CODE_REVISION_FIELD_NUMBER: _ClassVar[int]
    FACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    code_revision: str
    factor_id: str
    parameters: _struct_pb2.Struct
    revision: str
    def __init__(self, code_revision: _Optional[str] = ..., factor_id: _Optional[str] = ..., parameters: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., revision: _Optional[str] = ...) -> None: ...

class FixedStrategy(_message.Message):
    __slots__ = ("code_revision", "factor_bindings", "parameters", "revision", "strategy_id")
    class FactorBindingsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: FixedFactor
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[FixedFactor, _Mapping]] = ...) -> None: ...
    CODE_REVISION_FIELD_NUMBER: _ClassVar[int]
    FACTOR_BINDINGS_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    code_revision: str
    factor_bindings: _containers.MessageMap[str, FixedFactor]
    parameters: _struct_pb2.Struct
    revision: str
    strategy_id: str
    def __init__(self, code_revision: _Optional[str] = ..., factor_bindings: _Optional[_Mapping[str, FixedFactor]] = ..., parameters: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., revision: _Optional[str] = ..., strategy_id: _Optional[str] = ...) -> None: ...

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

class PaperAdvanced(_message.Message):
    __slots__ = ("sequence", "session_id", "step", "url", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    STEP_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    sequence: int
    session_id: str
    step: _struct_pb2.Struct
    url: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, sequence: _Optional[int] = ..., session_id: _Optional[str] = ..., step: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., url: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class PaperRequest(_message.Message):
    __slots__ = ("configuration_id", "request_id", "snapshot_id")
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    configuration_id: str
    request_id: str
    snapshot_id: str
    def __init__(self, configuration_id: _Optional[str] = ..., request_id: _Optional[str] = ..., snapshot_id: _Optional[str] = ...) -> None: ...

class PaperSession(_message.Message):
    __slots__ = ("configuration", "cursor", "session_id", "status", "summary", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    CONFIGURATION_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    configuration: SavedConfiguration
    cursor: int
    session_id: str
    status: str
    summary: ResearchSummary
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, configuration: _Optional[_Union[SavedConfiguration, _Mapping]] = ..., cursor: _Optional[int] = ..., session_id: _Optional[str] = ..., status: _Optional[str] = ..., summary: _Optional[_Union[ResearchSummary, _Mapping]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class ParameterDescription(_message.Message):
    __slots__ = ("default", "label", "maximum", "minimum", "name", "unit")
    DEFAULT_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    MAXIMUM_FIELD_NUMBER: _ClassVar[int]
    MINIMUM_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    UNIT_FIELD_NUMBER: _ClassVar[int]
    default: _struct_pb2.Value
    label: str
    maximum: _struct_pb2.Value
    minimum: _struct_pb2.Value
    name: str
    unit: str
    def __init__(self, default: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., label: _Optional[str] = ..., maximum: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., minimum: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., name: _Optional[str] = ..., unit: _Optional[str] = ...) -> None: ...

class PublishRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Readiness(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: str
    def __init__(self, status: _Optional[str] = ...) -> None: ...

class ResearchAttempt(_message.Message):
    __slots__ = ("attempt_id", "created_at", "error", "run_id", "status", "evidence_fields", "null_fields")
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
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    attempt_id: str
    created_at: str
    error: str
    run_id: str
    status: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, attempt_id: _Optional[str] = ..., created_at: _Optional[str] = ..., error: _Optional[str] = ..., run_id: _Optional[str] = ..., status: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class ResearchConfiguration(_message.Message):
    __slots__ = ("risk", "simulation", "strategy")
    RISK_FIELD_NUMBER: _ClassVar[int]
    SIMULATION_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    risk: RiskInput
    simulation: SimulationInput
    strategy: FixedStrategy
    def __init__(self, risk: _Optional[_Union[RiskInput, _Mapping]] = ..., simulation: _Optional[_Union[SimulationInput, _Mapping]] = ..., strategy: _Optional[_Union[FixedStrategy, _Mapping]] = ...) -> None: ...

class ResearchConfigurationInput(_message.Message):
    __slots__ = ("risk", "simulation", "strategy", "null_fields")
    RISK_FIELD_NUMBER: _ClassVar[int]
    SIMULATION_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    risk: RiskInput
    simulation: SimulationInput
    strategy: _struct_pb2.Value
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, risk: _Optional[_Union[RiskInput, _Mapping]] = ..., simulation: _Optional[_Union[SimulationInput, _Mapping]] = ..., strategy: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class ResearchResultDocument(_message.Message):
    __slots__ = ("evaluation", "orders", "data", "decisions", "equity_curve", "fills", "summary", "evidence_fields", "null_fields", "settlements")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    EVALUATION_FIELD_NUMBER: _ClassVar[int]
    ORDERS_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    DECISIONS_FIELD_NUMBER: _ClassVar[int]
    EQUITY_CURVE_FIELD_NUMBER: _ClassVar[int]
    FILLS_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    SETTLEMENTS_FIELD_NUMBER: _ClassVar[int]
    evaluation: EvaluationResult
    orders: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    data: DatasetDetails
    decisions: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    equity_curve: _containers.RepeatedCompositeFieldContainer[EquityPoint]
    fills: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    summary: ResearchSummary
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    settlements: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    def __init__(self, evaluation: _Optional[_Union[EvaluationResult, _Mapping]] = ..., orders: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., data: _Optional[_Union[DatasetDetails, _Mapping]] = ..., decisions: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., equity_curve: _Optional[_Iterable[_Union[EquityPoint, _Mapping]]] = ..., fills: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., summary: _Optional[_Union[ResearchSummary, _Mapping]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ..., settlements: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ...) -> None: ...

class ResearchSummary(_message.Message):
    __slots__ = ("bar_count", "decision_count", "ending_cash", "ending_equity", "ending_position_lots", "fill_count", "initial_cash", "max_drawdown", "max_drawdown_fraction", "realized_pnl", "total_fees", "total_return", "unrealized_pnl")
    BAR_COUNT_FIELD_NUMBER: _ClassVar[int]
    DECISION_COUNT_FIELD_NUMBER: _ClassVar[int]
    ENDING_CASH_FIELD_NUMBER: _ClassVar[int]
    ENDING_EQUITY_FIELD_NUMBER: _ClassVar[int]
    ENDING_POSITION_LOTS_FIELD_NUMBER: _ClassVar[int]
    FILL_COUNT_FIELD_NUMBER: _ClassVar[int]
    INITIAL_CASH_FIELD_NUMBER: _ClassVar[int]
    MAX_DRAWDOWN_FIELD_NUMBER: _ClassVar[int]
    MAX_DRAWDOWN_FRACTION_FIELD_NUMBER: _ClassVar[int]
    REALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FEES_FIELD_NUMBER: _ClassVar[int]
    TOTAL_RETURN_FIELD_NUMBER: _ClassVar[int]
    UNREALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    bar_count: int
    decision_count: int
    ending_cash: str
    ending_equity: str
    ending_position_lots: int
    fill_count: int
    initial_cash: str
    max_drawdown: str
    max_drawdown_fraction: str
    realized_pnl: str
    total_fees: str
    total_return: str
    unrealized_pnl: str
    def __init__(self, bar_count: _Optional[int] = ..., decision_count: _Optional[int] = ..., ending_cash: _Optional[str] = ..., ending_equity: _Optional[str] = ..., ending_position_lots: _Optional[int] = ..., fill_count: _Optional[int] = ..., initial_cash: _Optional[str] = ..., max_drawdown: _Optional[str] = ..., max_drawdown_fraction: _Optional[str] = ..., realized_pnl: _Optional[str] = ..., total_fees: _Optional[str] = ..., total_return: _Optional[str] = ..., unrealized_pnl: _Optional[str] = ...) -> None: ...

class RevisionCreated(_message.Message):
    __slots__ = ("revision_id",)
    REVISION_ID_FIELD_NUMBER: _ClassVar[int]
    revision_id: str
    def __init__(self, revision_id: _Optional[str] = ...) -> None: ...

class RiskInput(_message.Message):
    __slots__ = ("initial_margin_fraction", "max_adverse_price_move_fraction", "max_gross_notional", "max_lots", "max_margin_fraction")
    INITIAL_MARGIN_FRACTION_FIELD_NUMBER: _ClassVar[int]
    MAX_ADVERSE_PRICE_MOVE_FRACTION_FIELD_NUMBER: _ClassVar[int]
    MAX_GROSS_NOTIONAL_FIELD_NUMBER: _ClassVar[int]
    MAX_LOTS_FIELD_NUMBER: _ClassVar[int]
    MAX_MARGIN_FRACTION_FIELD_NUMBER: _ClassVar[int]
    initial_margin_fraction: str
    max_adverse_price_move_fraction: str
    max_gross_notional: str
    max_lots: int
    max_margin_fraction: str
    def __init__(self, initial_margin_fraction: _Optional[str] = ..., max_adverse_price_move_fraction: _Optional[str] = ..., max_gross_notional: _Optional[str] = ..., max_lots: _Optional[int] = ..., max_margin_fraction: _Optional[str] = ...) -> None: ...

class RunDetail(_message.Message):
    __slots__ = ("code_revision", "committed_code", "config", "created_at", "result", "run_id", "snapshot", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    CODE_REVISION_FIELD_NUMBER: _ClassVar[int]
    COMMITTED_CODE_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    code_revision: str
    committed_code: bool
    config: ResearchConfiguration
    created_at: str
    result: ResearchResultDocument
    run_id: str
    snapshot: SnapshotReference
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, code_revision: _Optional[str] = ..., committed_code: _Optional[bool] = ..., config: _Optional[_Union[ResearchConfiguration, _Mapping]] = ..., created_at: _Optional[str] = ..., result: _Optional[_Union[ResearchResultDocument, _Mapping]] = ..., run_id: _Optional[str] = ..., snapshot: _Optional[_Union[SnapshotReference, _Mapping]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class RunSummary(_message.Message):
    __slots__ = ("code_revision", "committed_code", "config", "created_at", "market", "run_id", "snapshot", "summary", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    CODE_REVISION_FIELD_NUMBER: _ClassVar[int]
    COMMITTED_CODE_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    MARKET_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    code_revision: str
    committed_code: bool
    config: ResearchConfiguration
    created_at: str
    market: _struct_pb2.Struct
    run_id: str
    snapshot: SnapshotReference
    summary: ResearchSummary
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, code_revision: _Optional[str] = ..., committed_code: _Optional[bool] = ..., config: _Optional[_Union[ResearchConfiguration, _Mapping]] = ..., created_at: _Optional[str] = ..., market: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., run_id: _Optional[str] = ..., snapshot: _Optional[_Union[SnapshotReference, _Mapping]] = ..., summary: _Optional[_Union[ResearchSummary, _Mapping]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class SavedConfiguration(_message.Message):
    __slots__ = ("config", "configuration_id", "created_at", "name", "risk_hash", "strategy_hash")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    RISK_HASH_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_HASH_FIELD_NUMBER: _ClassVar[int]
    config: ResearchConfiguration
    configuration_id: str
    created_at: str
    name: str
    risk_hash: str
    strategy_hash: str
    def __init__(self, config: _Optional[_Union[ResearchConfiguration, _Mapping]] = ..., configuration_id: _Optional[str] = ..., created_at: _Optional[str] = ..., name: _Optional[str] = ..., risk_hash: _Optional[str] = ..., strategy_hash: _Optional[str] = ...) -> None: ...

class SimulationInput(_message.Message):
    __slots__ = ("max_volume_participation", "fee_per_lot", "initial_cash", "slippage_ticks")
    MAX_VOLUME_PARTICIPATION_FIELD_NUMBER: _ClassVar[int]
    FEE_PER_LOT_FIELD_NUMBER: _ClassVar[int]
    INITIAL_CASH_FIELD_NUMBER: _ClassVar[int]
    SLIPPAGE_TICKS_FIELD_NUMBER: _ClassVar[int]
    max_volume_participation: str
    fee_per_lot: str
    initial_cash: str
    slippage_ticks: int
    def __init__(self, max_volume_participation: _Optional[str] = ..., fee_per_lot: _Optional[str] = ..., initial_cash: _Optional[str] = ..., slippage_ticks: _Optional[int] = ...) -> None: ...

class SnapshotReference(_message.Message):
    __slots__ = ("content_hash", "id", "evidence_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    CONTENT_HASH_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    content_hash: str
    id: str
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    def __init__(self, content_hash: _Optional[str] = ..., id: _Optional[str] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ...) -> None: ...

class StrategyCandidate(_message.Message):
    __slots__ = ("candidate_id", "document", "format", "same_clean_revision", "version_id")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    DOCUMENT_FIELD_NUMBER: _ClassVar[int]
    FORMAT_FIELD_NUMBER: _ClassVar[int]
    SAME_CLEAN_REVISION_FIELD_NUMBER: _ClassVar[int]
    VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    document: VersionDocument
    format: int
    same_clean_revision: bool
    version_id: str
    def __init__(self, candidate_id: _Optional[str] = ..., document: _Optional[_Union[VersionDocument, _Mapping]] = ..., format: _Optional[int] = ..., same_clean_revision: _Optional[bool] = ..., version_id: _Optional[str] = ...) -> None: ...

class StrategyDescription(_message.Message):
    __slots__ = ("category", "description", "factor_slots", "name", "parameters", "revision", "strategy_id")
    class FactorSlotsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    FACTOR_SLOTS_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    category: str
    description: str
    factor_slots: _containers.ScalarMap[str, str]
    name: str
    parameters: _containers.RepeatedCompositeFieldContainer[ParameterDescription]
    revision: str
    strategy_id: str
    def __init__(self, category: _Optional[str] = ..., description: _Optional[str] = ..., factor_slots: _Optional[_Mapping[str, str]] = ..., name: _Optional[str] = ..., parameters: _Optional[_Iterable[_Union[ParameterDescription, _Mapping]]] = ..., revision: _Optional[str] = ..., strategy_id: _Optional[str] = ...) -> None: ...

class StrategyInput(_message.Message):
    __slots__ = ("factor_bindings", "parameters", "strategy_id", "null_fields")
    class FactorBindingsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: FactorInput
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[FactorInput, _Mapping]] = ...) -> None: ...
    FACTOR_BINDINGS_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    factor_bindings: _containers.MessageMap[str, FactorInput]
    parameters: _struct_pb2.Struct
    strategy_id: str
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, factor_bindings: _Optional[_Mapping[str, FactorInput]] = ..., parameters: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., strategy_id: _Optional[str] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class StrategySummary(_message.Message):
    __slots__ = ("category", "description", "name", "strategy_id")
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    category: str
    description: str
    name: str
    strategy_id: str
    def __init__(self, category: _Optional[str] = ..., description: _Optional[str] = ..., name: _Optional[str] = ..., strategy_id: _Optional[str] = ...) -> None: ...

class StrategyVersion(_message.Message):
    __slots__ = ("created_at", "document", "name", "version_id")
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    DOCUMENT_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    created_at: str
    document: VersionDocument
    name: str
    version_id: str
    def __init__(self, created_at: _Optional[str] = ..., document: _Optional[_Union[VersionDocument, _Mapping]] = ..., name: _Optional[str] = ..., version_id: _Optional[str] = ...) -> None: ...

class StrategyVersionRequest(_message.Message):
    __slots__ = ("configuration_id", "name", "run_ids")
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    RUN_IDS_FIELD_NUMBER: _ClassVar[int]
    configuration_id: str
    name: str
    run_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, configuration_id: _Optional[str] = ..., name: _Optional[str] = ..., run_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class VersionCreated(_message.Message):
    __slots__ = ("version_id",)
    VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    version_id: str
    def __init__(self, version_id: _Optional[str] = ...) -> None: ...

class VersionDocument(_message.Message):
    __slots__ = ("code_revision", "configuration", "evidence", "validation")
    CODE_REVISION_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    VALIDATION_FIELD_NUMBER: _ClassVar[int]
    code_revision: str
    configuration: SavedConfiguration
    evidence: _containers.RepeatedCompositeFieldContainer[RunDetail]
    validation: _struct_pb2.Struct
    def __init__(self, code_revision: _Optional[str] = ..., configuration: _Optional[_Union[SavedConfiguration, _Mapping]] = ..., evidence: _Optional[_Iterable[_Union[RunDetail, _Mapping]]] = ..., validation: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class GetApiConfigurationsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[SavedConfiguration]
    def __init__(self, items: _Optional[_Iterable[_Union[SavedConfiguration, _Mapping]]] = ...) -> None: ...

class GetApiDatasetsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[DatasetSummary]
    def __init__(self, items: _Optional[_Iterable[_Union[DatasetSummary, _Mapping]]] = ...) -> None: ...

class GetApiFactorRevisionsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[FactorRevision]
    def __init__(self, items: _Optional[_Iterable[_Union[FactorRevision, _Mapping]]] = ...) -> None: ...

class GetApiFactorRunsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[FactorRun]
    def __init__(self, items: _Optional[_Iterable[_Union[FactorRun, _Mapping]]] = ...) -> None: ...

class GetApiPaperResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[PaperSession]
    def __init__(self, items: _Optional[_Iterable[_Union[PaperSession, _Mapping]]] = ...) -> None: ...

class GetApiResearchAttemptsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[ResearchAttempt]
    def __init__(self, items: _Optional[_Iterable[_Union[ResearchAttempt, _Mapping]]] = ...) -> None: ...

class PostApiRunComparisonsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[Comparison]
    def __init__(self, items: _Optional[_Iterable[_Union[Comparison, _Mapping]]] = ...) -> None: ...

class GetApiRunsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[RunSummary]
    def __init__(self, items: _Optional[_Iterable[_Union[RunSummary, _Mapping]]] = ...) -> None: ...

class GetApiStrategyCandidatesResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[StrategyCandidate]
    def __init__(self, items: _Optional[_Iterable[_Union[StrategyCandidate, _Mapping]]] = ...) -> None: ...

class GetApiStrategyVersionsResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[StrategyVersion]
    def __init__(self, items: _Optional[_Iterable[_Union[StrategyVersion, _Mapping]]] = ...) -> None: ...

class ResearchTask(_message.Message):
    __slots__ = ("task_id", "snapshot_id", "snapshot_hash", "code_revision", "created_at", "status", "completed", "total", "run_id", "reason", "config", "attempts", "evidence_fields", "null_fields")
    class EvidenceFieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _struct_pb2.Value
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_HASH_FIELD_NUMBER: _ClassVar[int]
    CODE_REVISION_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    ATTEMPTS_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELDS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    snapshot_id: str
    snapshot_hash: str
    code_revision: str
    created_at: str
    status: str
    completed: int
    total: int
    run_id: str
    reason: str
    config: _struct_pb2.Struct
    attempts: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    evidence_fields: _containers.MessageMap[str, _struct_pb2.Value]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, task_id: _Optional[str] = ..., snapshot_id: _Optional[str] = ..., snapshot_hash: _Optional[str] = ..., code_revision: _Optional[str] = ..., created_at: _Optional[str] = ..., status: _Optional[str] = ..., completed: _Optional[int] = ..., total: _Optional[int] = ..., run_id: _Optional[str] = ..., reason: _Optional[str] = ..., config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., attempts: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., evidence_fields: _Optional[_Mapping[str, _struct_pb2.Value]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class TaskRequest(_message.Message):
    __slots__ = ("request_id", "snapshot_id", "config")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    snapshot_id: str
    config: ResearchConfigurationInput
    def __init__(self, request_id: _Optional[str] = ..., snapshot_id: _Optional[str] = ..., config: _Optional[_Union[ResearchConfigurationInput, _Mapping]] = ...) -> None: ...

class TaskControl(_message.Message):
    __slots__ = ("action",)
    ACTION_FIELD_NUMBER: _ClassVar[int]
    action: str
    def __init__(self, action: _Optional[str] = ...) -> None: ...

class ExperimentRequest(_message.Message):
    __slots__ = ("request_id", "hypothesis", "train_snapshot", "validation_snapshot", "test_snapshot", "configurations")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    HYPOTHESIS_FIELD_NUMBER: _ClassVar[int]
    TRAIN_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    VALIDATION_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    TEST_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATIONS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    hypothesis: str
    train_snapshot: str
    validation_snapshot: str
    test_snapshot: str
    configurations: _containers.RepeatedCompositeFieldContainer[ResearchConfigurationInput]
    def __init__(self, request_id: _Optional[str] = ..., hypothesis: _Optional[str] = ..., train_snapshot: _Optional[str] = ..., validation_snapshot: _Optional[str] = ..., test_snapshot: _Optional[str] = ..., configurations: _Optional[_Iterable[_Union[ResearchConfigurationInput, _Mapping]]] = ...) -> None: ...

class LearningRecipeInput(_message.Message):
    __slots__ = ("fast_bars", "slow_bars", "horizon_bars", "penalties", "threshold", "target_fraction")
    FAST_BARS_FIELD_NUMBER: _ClassVar[int]
    SLOW_BARS_FIELD_NUMBER: _ClassVar[int]
    HORIZON_BARS_FIELD_NUMBER: _ClassVar[int]
    PENALTIES_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    TARGET_FRACTION_FIELD_NUMBER: _ClassVar[int]
    fast_bars: int
    slow_bars: int
    horizon_bars: int
    penalties: _containers.RepeatedScalarFieldContainer[str]
    threshold: str
    target_fraction: str
    def __init__(self, fast_bars: _Optional[int] = ..., slow_bars: _Optional[int] = ..., horizon_bars: _Optional[int] = ..., penalties: _Optional[_Iterable[str]] = ..., threshold: _Optional[str] = ..., target_fraction: _Optional[str] = ...) -> None: ...

class LearningExperimentRequest(_message.Message):
    __slots__ = ("request_id", "hypothesis", "train_snapshot", "validation_snapshot", "test_snapshot", "configurations", "learning")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    HYPOTHESIS_FIELD_NUMBER: _ClassVar[int]
    TRAIN_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    VALIDATION_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    TEST_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATIONS_FIELD_NUMBER: _ClassVar[int]
    LEARNING_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    hypothesis: str
    train_snapshot: str
    validation_snapshot: str
    test_snapshot: str
    configurations: _containers.RepeatedCompositeFieldContainer[ResearchConfigurationInput]
    learning: LearningRecipeInput
    def __init__(self, request_id: _Optional[str] = ..., hypothesis: _Optional[str] = ..., train_snapshot: _Optional[str] = ..., validation_snapshot: _Optional[str] = ..., test_snapshot: _Optional[str] = ..., configurations: _Optional[_Iterable[_Union[ResearchConfigurationInput, _Mapping]]] = ..., learning: _Optional[_Union[LearningRecipeInput, _Mapping]] = ...) -> None: ...

class Experiment(_message.Message):
    __slots__ = ("fitted", "experiment_id", "plan_id", "created_at", "status", "plan", "selection", "trials", "null_fields")
    FITTED_FIELD_NUMBER: _ClassVar[int]
    EXPERIMENT_ID_FIELD_NUMBER: _ClassVar[int]
    PLAN_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PLAN_FIELD_NUMBER: _ClassVar[int]
    SELECTION_FIELD_NUMBER: _ClassVar[int]
    TRIALS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    fitted: _struct_pb2.Struct
    experiment_id: str
    plan_id: str
    created_at: str
    status: str
    plan: _struct_pb2.Struct
    selection: _struct_pb2.Struct
    trials: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, fitted: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., experiment_id: _Optional[str] = ..., plan_id: _Optional[str] = ..., created_at: _Optional[str] = ..., status: _Optional[str] = ..., plan: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., selection: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., trials: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class ExperimentList(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[Experiment]
    def __init__(self, items: _Optional[_Iterable[_Union[Experiment, _Mapping]]] = ...) -> None: ...

class TaskList(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[ResearchTask]
    def __init__(self, items: _Optional[_Iterable[_Union[ResearchTask, _Mapping]]] = ...) -> None: ...

class EvaluationPlan(_message.Message):
    __slots__ = ("plan_id", "revision", "snapshot_id", "content_hash", "window", "event_start", "event_end", "expected_bars", "benchmark", "annualization", "risk_free_rate", "sample_use", "null_fields")
    PLAN_ID_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    CONTENT_HASH_FIELD_NUMBER: _ClassVar[int]
    WINDOW_FIELD_NUMBER: _ClassVar[int]
    EVENT_START_FIELD_NUMBER: _ClassVar[int]
    EVENT_END_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_BARS_FIELD_NUMBER: _ClassVar[int]
    BENCHMARK_FIELD_NUMBER: _ClassVar[int]
    ANNUALIZATION_FIELD_NUMBER: _ClassVar[int]
    RISK_FREE_RATE_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_USE_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    plan_id: str
    revision: str
    snapshot_id: str
    content_hash: str
    window: str
    event_start: str
    event_end: str
    expected_bars: int
    benchmark: str
    annualization: str
    risk_free_rate: str
    sample_use: str
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, plan_id: _Optional[str] = ..., revision: _Optional[str] = ..., snapshot_id: _Optional[str] = ..., content_hash: _Optional[str] = ..., window: _Optional[str] = ..., event_start: _Optional[str] = ..., event_end: _Optional[str] = ..., expected_bars: _Optional[int] = ..., benchmark: _Optional[str] = ..., annualization: _Optional[str] = ..., risk_free_rate: _Optional[str] = ..., sample_use: _Optional[str] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class EvaluationResult(_message.Message):
    __slots__ = ("plan", "status", "observed_bars", "benchmark_ending_equity", "benchmark_return", "excess_return", "annualized_return", "sharpe", "limitations", "null_fields")
    PLAN_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    OBSERVED_BARS_FIELD_NUMBER: _ClassVar[int]
    BENCHMARK_ENDING_EQUITY_FIELD_NUMBER: _ClassVar[int]
    BENCHMARK_RETURN_FIELD_NUMBER: _ClassVar[int]
    EXCESS_RETURN_FIELD_NUMBER: _ClassVar[int]
    ANNUALIZED_RETURN_FIELD_NUMBER: _ClassVar[int]
    SHARPE_FIELD_NUMBER: _ClassVar[int]
    LIMITATIONS_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    plan: EvaluationPlan
    status: str
    observed_bars: int
    benchmark_ending_equity: str
    benchmark_return: str
    excess_return: str
    annualized_return: str
    sharpe: str
    limitations: _containers.RepeatedScalarFieldContainer[str]
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, plan: _Optional[_Union[EvaluationPlan, _Mapping]] = ..., status: _Optional[str] = ..., observed_bars: _Optional[int] = ..., benchmark_ending_equity: _Optional[str] = ..., benchmark_return: _Optional[str] = ..., excess_return: _Optional[str] = ..., annualized_return: _Optional[str] = ..., sharpe: _Optional[str] = ..., limitations: _Optional[_Iterable[str]] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...
