"""Browser code generation from protoc descriptors; no Python application imports."""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from types import ModuleType


def main() -> None:
    target = Path(sys.argv[1])
    # Load only protoc output, without executing application __init__ modules.
    for name in (
        "northstar_quant",
        "northstar_quant.web",
        "northstar_quant.apps",
        "northstar_quant.accounting",
        *[f"northstar_quant.apps.{role}" for role in ("data_hub", "research", "live")],
    ):
        namespace = ModuleType(name)
        namespace.__path__ = [str(target.joinpath(*name.split(".")))]
        sys.modules[name] = namespace
    options = importlib.import_module("northstar_quant.web.api_options_pb2")
    common = importlib.import_module("northstar_quant.web.common_pb2")
    accounting = importlib.import_module("northstar_quant.accounting.protocol_pb2")
    from google.protobuf.descriptor import FieldDescriptor as F

    for role in ("data_hub", "research", "live"):
        module = importlib.import_module(f"northstar_quant.apps.{role}.api_pb2")
        descriptors = {
            **module.DESCRIPTOR.message_types_by_name,
            **common.DESCRIPTOR.message_types_by_name,
            **accounting.DESCRIPTOR.message_types_by_name,
        }
        definitions = {}
        declarations = [
            f"// Generated from {role}.proto. Do not edit.",
            "export type JsonValue = null | boolean | number | string | JsonValue[] | "
            "{ [key: string]: JsonValue };",
        ]
        primitives = {
            F.TYPE_STRING: "string",
            F.TYPE_BOOL: "boolean",
            F.TYPE_INT64: "number",
            F.TYPE_DOUBLE: "number",
        }

        def base_type(field):
            if field.message_type:
                n = field.message_type.full_name
                if n == "google.protobuf.Value":
                    return (
                        "string | number"
                        if field.GetOptions().Extensions[options.string_or_integer]
                        else "JsonValue"
                    )
                if n == "google.protobuf.Struct":
                    return "Record<string, JsonValue>"
                return field.message_type.name
            allowed = field.GetOptions().Extensions[options.allowed_values]
            return (
                " | ".join(json.dumps(v) for v in json.loads(allowed))
                if allowed
                else primitives[field.type]
            )

        def ts_type(field):
            t = base_type(field)
            if field.message_type and field.message_type.GetOptions().map_entry:
                t = "Record<string, " + base_type(field.message_type.fields_by_name["value"]) + ">"
            elif field.is_repeated:
                t = "(" + t + ")[]"
            if field.GetOptions().Extensions[options.nullable]:
                t += " | null"
            return t

        for name, descriptor in descriptors.items():
            if descriptor.GetOptions().Extensions[options.root_array]:
                declarations.append(f"export type {name} = {ts_type(descriptor.fields[0])};")
            else:
                declarations.append(f"export type {name} = {{")
                for f in descriptor.fields:
                    if f.name == "null_fields":
                        continue
                    if f.name == "evidence_fields":
                        declarations.append("  [key: string]: unknown;")
                        continue
                    optional = "" if f.GetOptions().Extensions[options.required_field] else "?"
                    declarations.append(f"  {f.name}{optional}: {ts_type(f)};")
                declarations.append("};")
            fields = {}
            for f in descriptor.fields:
                info = {
                    "id": f.number,
                    "presence": f.has_presence,
                    "type": f.message_type.full_name
                    if f.message_type
                    else {
                        F.TYPE_STRING: "string",
                        F.TYPE_BOOL: "bool",
                        F.TYPE_INT64: "int64",
                        F.TYPE_DOUBLE: "double",
                    }[f.type],
                    "required": f.GetOptions().Extensions[options.required_field],
                    "nullable": f.GetOptions().Extensions[options.nullable],
                }
                if f.message_type and f.message_type.GetOptions().map_entry:
                    value = f.message_type.fields_by_name["value"]
                    info.update(
                        keyType="string",
                        type=value.message_type.full_name if value.message_type else "string",
                    )
                elif f.is_repeated:
                    info["rule"] = "repeated"
                fields[f.name] = info
            definitions[descriptor.full_name] = {
                "fields": fields,
                "array": descriptor.GetOptions().Extensions[options.root_array],
                "evidence": descriptor.GetOptions().Extensions[options.evidence],
            }
        methods = []
        for method in module.DESCRIPTOR.services_by_name["BrowserApi"].methods:
            opt = method.GetOptions()
            methods.append(
                {
                    "method": opt.Extensions[options.http_method],
                    "path": opt.Extensions[options.http_path],
                    "input": method.input_type.full_name,
                    "output": method.output_type.full_name,
                    "runtime": opt.Extensions[options.runtime_required],
                }
            )
        base = target / f"frontend/apps/{role}/api"
        base.mkdir(parents=True, exist_ok=True)
        (base / "generated.ts").write_text("\n".join(declarations) + "\n")
        (base / "protocol.json").write_text(
            json.dumps({"messages": definitions, "methods": methods}, ensure_ascii=False, indent=2)
            + "\n"
        )
        out = [
            f"// Generated from {role}.proto. Do not edit.",
            'import type * as messages from "./generated";',
            'import { mutate as send, registerProtocol } from "../../../shared/api";',
            'import type { Query } from "../../../shared/data";',
            'import protocol from "./protocol.json";',
            'import codec from "./codec";',
            "registerProtocol(protocol, codec);",
        ]
        for verb, label in [("GET", "Get"), ("POST", "Command")]:
            selected = sorted(
                [m for m in methods if m["method"] == verb],
                key=lambda m: len(m["path"]),
                reverse=True,
            )

            def path(m):
                return "`" + re.sub(r"\{[^}]+\}", "${string}", m["path"]) + "`"

            out.append(f"export type {label}Path = " + " | ".join(path(m) for m in selected) + ";")
            for suffix, key in [("Response", "output")] + (
                [("Body", "input")] if verb == "POST" else []
            ):
                out.append(
                    f"export type {label}{suffix}<P> = "
                    + "\n".join(
                        f"P extends {path(m)} ? messages.{m[key].split('.')[-1]} :"
                        for m in selected
                    )
                    + " never;"
                )
        runtime = "runtime: string" if role == "live" else "runtime?: string"
        out += [
            "export function query<P extends GetPath>(path: P | null): "
            "Query<GetResponse<P>> | null {return path === null ? null : {path};}",
            "export function mutate<P extends CommandPath>(path: P, "
            f"body: CommandBody<NoInfer<P>>, {runtime}): Promise<CommandResponse<P>> "
            "{return send(path, body, runtime);}",
        ]
        (base / "client.ts").write_text("\n".join(out) + "\n")
    # Dataset components use the same published message, injected by each app.
    shared = target / "frontend/shared/api"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "generated.ts").write_text(
        "export type { DatasetSummary, DatasetDetails, DatasetLineage } "
        'from "../../apps/data_hub/api/generated";\n'
    )


if __name__ == "__main__":
    main()
