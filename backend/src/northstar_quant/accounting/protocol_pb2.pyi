from northstar_quant.web import api_options_pb2 as _api_options_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ChargeRate(_message.Message):
    __slots__ = ("by_money", "by_volume")
    BY_MONEY_FIELD_NUMBER: _ClassVar[int]
    BY_VOLUME_FIELD_NUMBER: _ClassVar[int]
    by_money: str
    by_volume: str
    def __init__(self, by_money: _Optional[str] = ..., by_volume: _Optional[str] = ...) -> None: ...

class FuturesTerms(_message.Message):
    __slots__ = ("terms_id", "contract_id", "effective_from", "effective_until", "available_at", "source_reference", "open_fee", "close_today_fee", "close_yesterday_fee", "long_margin", "short_margin", "lower_limit", "upper_limit", "money_quantum", "fee_rounding")
    TERMS_ID_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_ID_FIELD_NUMBER: _ClassVar[int]
    EFFECTIVE_FROM_FIELD_NUMBER: _ClassVar[int]
    EFFECTIVE_UNTIL_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_AT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REFERENCE_FIELD_NUMBER: _ClassVar[int]
    OPEN_FEE_FIELD_NUMBER: _ClassVar[int]
    CLOSE_TODAY_FEE_FIELD_NUMBER: _ClassVar[int]
    CLOSE_YESTERDAY_FEE_FIELD_NUMBER: _ClassVar[int]
    LONG_MARGIN_FIELD_NUMBER: _ClassVar[int]
    SHORT_MARGIN_FIELD_NUMBER: _ClassVar[int]
    LOWER_LIMIT_FIELD_NUMBER: _ClassVar[int]
    UPPER_LIMIT_FIELD_NUMBER: _ClassVar[int]
    MONEY_QUANTUM_FIELD_NUMBER: _ClassVar[int]
    FEE_ROUNDING_FIELD_NUMBER: _ClassVar[int]
    terms_id: str
    contract_id: str
    effective_from: str
    effective_until: str
    available_at: str
    source_reference: str
    open_fee: ChargeRate
    close_today_fee: ChargeRate
    close_yesterday_fee: ChargeRate
    long_margin: ChargeRate
    short_margin: ChargeRate
    lower_limit: str
    upper_limit: str
    money_quantum: str
    fee_rounding: str
    def __init__(self, terms_id: _Optional[str] = ..., contract_id: _Optional[str] = ..., effective_from: _Optional[str] = ..., effective_until: _Optional[str] = ..., available_at: _Optional[str] = ..., source_reference: _Optional[str] = ..., open_fee: _Optional[_Union[ChargeRate, _Mapping]] = ..., close_today_fee: _Optional[_Union[ChargeRate, _Mapping]] = ..., close_yesterday_fee: _Optional[_Union[ChargeRate, _Mapping]] = ..., long_margin: _Optional[_Union[ChargeRate, _Mapping]] = ..., short_margin: _Optional[_Union[ChargeRate, _Mapping]] = ..., lower_limit: _Optional[str] = ..., upper_limit: _Optional[str] = ..., money_quantum: _Optional[str] = ..., fee_rounding: _Optional[str] = ...) -> None: ...

class SettlementFact(_message.Message):
    __slots__ = ("settlement_id", "contract_id", "trading_day", "next_trading_day", "settled_at", "available_at", "price", "source_reference")
    SETTLEMENT_ID_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_ID_FIELD_NUMBER: _ClassVar[int]
    TRADING_DAY_FIELD_NUMBER: _ClassVar[int]
    NEXT_TRADING_DAY_FIELD_NUMBER: _ClassVar[int]
    SETTLED_AT_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_AT_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REFERENCE_FIELD_NUMBER: _ClassVar[int]
    settlement_id: str
    contract_id: str
    trading_day: str
    next_trading_day: str
    settled_at: str
    available_at: str
    price: str
    source_reference: str
    def __init__(self, settlement_id: _Optional[str] = ..., contract_id: _Optional[str] = ..., trading_day: _Optional[str] = ..., next_trading_day: _Optional[str] = ..., settled_at: _Optional[str] = ..., available_at: _Optional[str] = ..., price: _Optional[str] = ..., source_reference: _Optional[str] = ...) -> None: ...
