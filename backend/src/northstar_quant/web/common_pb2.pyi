from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class Empty(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Error(_message.Message):
    __slots__ = ("detail", "status", "request_id", "runtime_id", "url", "rejection_id")
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_ID_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    REJECTION_ID_FIELD_NUMBER: _ClassVar[int]
    detail: str
    status: str
    request_id: str
    runtime_id: str
    url: str
    rejection_id: str
    def __init__(self, detail: _Optional[str] = ..., status: _Optional[str] = ..., request_id: _Optional[str] = ..., runtime_id: _Optional[str] = ..., url: _Optional[str] = ..., rejection_id: _Optional[str] = ...) -> None: ...
