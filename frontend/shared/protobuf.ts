/** Binary Protobuf adapter. No application business behavior lives here. */
type Field = {
  id: number;
  type: string;
  rule?: string;
  keyType?: string;
  required?: boolean;
  nullable?: boolean;
};
type Definition = {
  fields: Record<string, Field>;
  array?: boolean;
  evidence?: boolean;
};
export type Protocol = {
  messages: Record<string, Definition>;
  methods: {
    method: string;
    path: string;
    input: string;
    output: string;
    runtime: boolean;
  }[];
};
let definitions: Protocol["messages"] = {};
let bindings: Protocol["methods"] = [];
type MessageCodec = {
  encode(value: object): { finish(): Uint8Array };
  fromObject(value: object): object;
  decode(value: Uint8Array): object;
  toObject(value: object, options: { longs: NumberConstructor }): object;
};
const codecs = new Map<string, MessageCodec>();
export function registerProtocol(protocol: Protocol, codec: object) {
  for (const name of Object.keys(protocol.messages)) {
    let type: unknown = codec;
    for (const part of name.split("."))
      type = (type as Record<string, unknown>)[part];
    codecs.set(name, type as MessageCodec);
  }
  definitions = { ...definitions, ...protocol.messages };
  bindings = [...bindings, ...protocol.methods];
}
function binding(method: string, path: string) {
  const found = bindings.find(
    (b) =>
      b.method === method &&
      new RegExp("^" + b.path.replace(/\{[^}]+\}/g, "[^/]+") + "$").test(
        path.split("?")[0],
      ),
  );
  if (!found) throw new Error("接口不在当前 Protobuf 协议中");
  return found;
}
function encodeValue(value: unknown): object {
  if (value === null) return { nullValue: 0 };
  if (typeof value === "string") return { stringValue: value };
  if (typeof value === "number") return { numberValue: value };
  if (typeof value === "boolean") return { boolValue: value };
  if (Array.isArray(value))
    return { listValue: { values: value.map(encodeValue) } };
  if (typeof value === "object")
    return {
      structValue: {
        fields: Object.fromEntries(
          Object.entries(value!).map(([k, v]) => [k, encodeValue(v)]),
        ),
      },
    };
  throw new Error("无效的协议值");
}
function decodeValue(value: Record<string, unknown>): unknown {
  for (const key of ["stringValue", "numberValue", "boolValue"])
    if (Object.hasOwn(value, key)) return value[key];
  if (Object.hasOwn(value, "nullValue")) return null;
  if (value.listValue)
    return (
      (value.listValue as { values?: Record<string, unknown>[] }).values || []
    ).map(decodeValue);
  if (value.structValue)
    return decodeStruct(
      value.structValue as { fields?: Record<string, Record<string, unknown>> },
    );
  throw new Error("缺失的协议值");
}
function decodeStruct(value: {
  fields?: Record<string, Record<string, unknown>>;
}) {
  return Object.fromEntries(
    Object.entries(value.fields || {}).map(([k, v]) => [k, decodeValue(v)]),
  );
}
function convert(name: string, value: unknown, encode: boolean): unknown {
  if (name === "google.protobuf.Value")
    return encode
      ? encodeValue(value)
      : decodeValue(value as Record<string, unknown>);
  if (name === "google.protobuf.Struct")
    return encode
      ? {
          fields: Object.fromEntries(
            Object.entries(value as object).map(([k, v]) => [
              k,
              encodeValue(v),
            ]),
          ),
        }
      : decodeStruct(
          value as { fields?: Record<string, Record<string, unknown>> },
        );
  const definition = definitions[name];
  if (!definition) {
    if (
      name === "int64" &&
      (typeof value !== "number" || !Number.isSafeInteger(value))
    )
      throw new Error("整数超出浏览器精确表示范围");
    if (name === "string" && typeof value !== "string")
      throw new Error("协议需要字符串");
    if (name === "bool" && typeof value !== "boolean")
      throw new Error("协议需要布尔值");
    return value;
  }
  const source = {
    ...(encode && definition.array
      ? { items: value }
      : (value as Record<string, unknown>)),
  };
  const output: Record<string, unknown> = {};
  if (
    encode &&
    !definition.evidence &&
    Object.keys(source).some((key) => !Object.hasOwn(definition.fields, key))
  )
    throw new Error("请求包含当前协议未定义字段");
  if (encode && definition.evidence)
    source.evidence_fields = Object.fromEntries(
      Object.entries(source).filter(
        ([k]) => !Object.hasOwn(definition.fields, k),
      ),
    );
  const nulls = (source.null_fields || []) as string[];
  for (const [key, field] of Object.entries(definition.fields)) {
    if (key === "null_fields") continue;
    const v = source[key];
    if (encode && v === null && field.nullable) {
      ((output.null_fields ??= []) as string[]).push(key);
      continue;
    }
    if (!encode && nulls.includes(key)) {
      output[key] = null;
      continue;
    }
    if (v === undefined || v === null) {
      if (!encode && field.rule === "repeated") output[key] = [];
      else if (!encode && field.keyType) output[key] = {};
      else if (
        field.required &&
        !field.nullable &&
        field.rule !== "repeated" &&
        !field.keyType
      )
        throw new Error("协议缺少字段：" + key);
      continue;
    }
    output[key] = field.keyType
      ? Object.fromEntries(
          Object.entries(v as object).map(([k, item]) => [
            k,
            convert(field.type, item, encode),
          ]),
        )
      : field.rule === "repeated"
        ? (v as unknown[]).map((item) => convert(field.type, item, encode))
        : convert(field.type, v, encode);
  }
  if (!encode && definition.evidence) {
    const extras = output.evidence_fields as Record<string, unknown>;
    if (
      Object.keys(extras || {}).some((key) =>
        Object.hasOwn(definition.fields, key),
      )
    )
      throw new Error("证据扩展不能覆盖协议字段");
    for (const [key, value] of Object.entries(extras || {}))
      Object.defineProperty(output, key, { value, enumerable: true });
    delete output.evidence_fields;
  }
  return !encode && definition.array ? output.items : output;
}
export function encodeRequest(
  method: string,
  path: string,
  value: unknown,
): Uint8Array {
  const name = binding(method, path).input;
  const type = codecs.get(name)!;
  return type
    .encode(type.fromObject(convert(name, value, true) as object))
    .finish();
}
export async function decodeResponse(
  method: string,
  path: string,
  response: Response,
): Promise<unknown> {
  if (!response.headers.get("content-type")?.startsWith("application/protobuf"))
    throw new Error("服务没有返回 Protobuf 回执");
  const name = response.ok
    ? binding(method, path).output
    : "northstar.web.Error";
  const type = codecs.get(name)!;
  const value = type.toObject(
    type.decode(new Uint8Array(await response.arrayBuffer())),
    { longs: Number },
  );
  return convert(name, value, false);
}
