"""Protobuf HTTP messages; business rules remain in the owning operations."""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from typing import Any, cast

from google.protobuf import json_format, message_factory
from google.protobuf.message import DecodeError, Message

from northstar_quant.web import api_options_pb2

MEDIA_TYPE = "application/protobuf"


def _message(descriptor: Any) -> Message:
    return message_factory.GetMessageClass(descriptor)()


def _map(field: Any) -> bool:
    return field.message_type is not None and field.message_type.GetOptions().map_entry


def pack(descriptor: Any, value: Any) -> Message:
    message = _message(descriptor)
    if descriptor.full_name in {"google.protobuf.Value", "google.protobuf.Struct"}:
        json_format.ParseDict(value, message)
        return message
    if cast(Any, descriptor.GetOptions()).Extensions[api_options_pb2.root_array]:
        value = {"items": value}
    if not isinstance(value, Mapping):
        raise ValueError("协议需要对象。")
    values = dict(value)
    if cast(Any, descriptor.GetOptions()).Extensions[api_options_pb2.evidence]:
        values["evidence_fields"] = {
            k: v for k, v in values.items() if k not in descriptor.fields_by_name
        }
        values = {k: v for k, v in values.items() if k in descriptor.fields_by_name}
    elif set(values) - set(descriptor.fields_by_name):
        raise ValueError("协议包含未定义字段。")
    for field in descriptor.fields:
        if field.name == "null_fields":
            continue
        if (
            field.name in values
            and values[field.name] is None
            and cast(Any, field.GetOptions()).Extensions[api_options_pb2.nullable]
        ):
            cast(Any, message).null_fields.append(field.name)
            continue
        if field.name not in values or values[field.name] is None:
            if (
                cast(Any, field.GetOptions()).Extensions[api_options_pb2.required_field]
                and not cast(Any, field.GetOptions()).Extensions[api_options_pb2.nullable]
            ):
                raise ValueError("协议缺少必需字段：" + field.name)
            continue
        item = values[field.name]
        target = getattr(message, field.name)
        if _map(field):
            vfield = field.message_type.fields_by_name["value"]
            for key, v in item.items():
                if vfield.message_type:
                    target[key].CopyFrom(pack(vfield.message_type, v))
                else:
                    target[key] = v
        elif field.is_repeated:
            if field.message_type:
                for v in item:
                    target.add().CopyFrom(pack(field.message_type, v))
            else:
                target.extend(item)
        elif field.message_type:
            target.CopyFrom(pack(field.message_type, item))
        else:
            setattr(message, field.name, item)
    return message


def unpack(message: Message) -> Any:
    descriptor = message.DESCRIPTOR
    if descriptor.full_name in {"google.protobuf.Value", "google.protobuf.Struct"}:

        def integers(value: Any) -> Any:
            if isinstance(value, float) and value.is_integer() and abs(value) <= 2**53 - 1:
                return int(value)
            if isinstance(value, list):
                return [integers(item) for item in value]
            if isinstance(value, dict):
                return {key: integers(item) for key, item in value.items()}
            return value

        return integers(json_format.MessageToDict(message))
    result: dict[str, Any] = {}
    nulls = list(getattr(message, "null_fields", ()))
    if len(nulls) != len(set(nulls)) or any(
        name not in descriptor.fields_by_name
        or not cast(Any, descriptor.fields_by_name[name].GetOptions()).Extensions[
            api_options_pb2.nullable
        ]
        for name in nulls
    ):
        raise ValueError("无效的可空字段声明。")
    for field in descriptor.fields:
        if field.name == "null_fields":
            continue
        present = field.is_repeated or message.HasField(field.name)
        options = cast(Any, field.GetOptions())
        if field.name in nulls:
            if present and (not field.is_repeated or len(getattr(message, field.name))):
                raise ValueError("可空字段不能同时包含值。")
            result[field.name] = None
            continue
        if not present:
            if options.Extensions[api_options_pb2.required_field]:
                raise ValueError("协议缺少必需字段：" + field.name)
            continue
        value = getattr(message, field.name)
        if _map(field):
            is_message = (
                cast(Any, field.message_type).fields_by_name["value"].message_type is not None
            )
            result[field.name] = {k: unpack(v) if is_message else v for k, v in value.items()}
        elif field.is_repeated:
            result[field.name] = [unpack(v) if field.message_type else v for v in value]
        elif field.message_type:
            result[field.name] = unpack(value)
        else:
            allowed = options.Extensions[api_options_pb2.allowed_values]
            if allowed and value not in json.loads(allowed):
                raise ValueError("协议字段值不合法：" + field.name)
            result[field.name] = value
    if cast(Any, descriptor.GetOptions()).Extensions[api_options_pb2.evidence]:
        extra = result.pop("evidence_fields", {})
        if set(descriptor.fields_by_name) & extra.keys():
            raise ValueError("证据扩展不能覆盖协议字段。")
        result.update(extra)
    return (
        result["items"]
        if cast(Any, descriptor.GetOptions()).Extensions[api_options_pb2.root_array]
        else result
    )


def decode(descriptor: Any, data: bytes) -> Any:
    message = _message(descriptor)
    try:
        message.ParseFromString(data)
    except DecodeError as error:
        raise ValueError("无效的 Protobuf 请求。") from error
    original = message.SerializeToString(deterministic=True)
    message.DiscardUnknownFields()
    if message.SerializeToString(deterministic=True) != original:
        raise ValueError("请求包含当前协议未定义字段。")
    return unpack(message)


def methods(role: str) -> dict[tuple[str, str], Any]:
    module = importlib.import_module(f"northstar_quant.apps.{role}.api_pb2")
    return {
        (
            m.GetOptions().Extensions[api_options_pb2.http_method],
            m.GetOptions().Extensions[api_options_pb2.http_path],
        ): m
        for m in module.DESCRIPTOR.services_by_name["BrowserApi"].methods
    }


def bind(app: Any, role: str) -> None:
    """Fail startup if declared browser operations and installed routes diverge."""
    from fastapi.routing import APIRoute

    declared = methods(role)
    implemented = {
        (verb, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/api/")
        and route.response_model is not None
        for verb in (route.methods or ())
        if verb in {"GET", "POST"}
    }
    if implemented != set(declared):
        raise RuntimeError("HTTP operations do not match the current Protobuf protocol")
    app.state.protobuf_methods = declared
