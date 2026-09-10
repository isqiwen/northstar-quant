from northstar_quant.web import api_options_pb2 as _api_options_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class BrowserSession(_message.Message):
    __slots__ = ("authenticated", "csrf", "operator", "expires_at", "null_fields")
    AUTHENTICATED_FIELD_NUMBER: _ClassVar[int]
    CSRF_FIELD_NUMBER: _ClassVar[int]
    OPERATOR_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    NULL_FIELDS_FIELD_NUMBER: _ClassVar[int]
    authenticated: bool
    csrf: str
    operator: str
    expires_at: str
    null_fields: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, authenticated: _Optional[bool] = ..., csrf: _Optional[str] = ..., operator: _Optional[str] = ..., expires_at: _Optional[str] = ..., null_fields: _Optional[_Iterable[str]] = ...) -> None: ...

class LoginRequest(_message.Message):
    __slots__ = ("password",)
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    password: str
    def __init__(self, password: _Optional[str] = ...) -> None: ...
