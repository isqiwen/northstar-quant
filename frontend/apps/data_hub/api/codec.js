/*eslint-disable block-scoped-var, id-length, no-control-regex, no-magic-numbers, no-mixed-operators, no-prototype-builtins, no-redeclare, no-shadow, no-var, sort-vars, default-case, jsdoc/require-param*/
import $protobuf from "protobufjs/minimal.js";

// Common aliases
const $Reader = $protobuf.Reader, $Writer = $protobuf.Writer, $util = $protobuf.util;
const $Object = $util.global.Object, $undefined = $util.global.undefined, $Error = $util.global.Error, $RangeError = $util.global.RangeError, $TypeError = $util.global.TypeError, $Number = $util.global.Number, $String = $util.global.String, $Boolean = $util.global.Boolean, $isFinite = $util.global.isFinite, $Array = $util.global.Array, $parseInt = $util.global.parseInt, $BigInt = $util.global.BigInt;

// Exported root namespace
const $root = $protobuf.roots["northstar_data_hub"] || ($protobuf.roots["northstar_data_hub"] = {});

export const google = $root.google = (() => {

    /**
     * Namespace google.
     * @exports google
     * @namespace
     */
    const google = {};

    google.protobuf = (function() {

        /**
         * Namespace protobuf.
         * @memberof google
         * @namespace
         */
        const protobuf = {};

        protobuf.Struct = (function() {

            /**
             * Properties of a Struct.
             * @typedef {Object} google.protobuf.Struct.$Properties
             * @property {Object.<string,google.protobuf.Value.$Properties>|null} [fields] Struct fields
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a Struct.
             * @memberof google.protobuf
             * @interface IStruct
             * @augments google.protobuf.Struct.$Properties
             * @deprecated Use google.protobuf.Struct.$Properties instead.
             */

            /**
             * Shape of a Struct.
             * @typedef {{
             *   fields?: Object.<string,google.protobuf.Value.$Shape>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * }} google.protobuf.Struct.$Shape
             */

            /**
             * Constructs a new Struct.
             * @memberof google.protobuf
             * @classdesc Represents a Struct.
             * @constructor
             * @param {google.protobuf.Struct.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const Struct = function (properties) {
                this.fields = {};
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * Struct fields.
             * @member {Object.<string,google.protobuf.Value.$Properties>} fields
             * @memberof google.protobuf.Struct
             * @instance
             */
            Struct.prototype.fields = $util.emptyObject;

            /**
             * Creates a new Struct instance using the specified properties.
             * @function create
             * @memberof google.protobuf.Struct
             * @static
             * @param {google.protobuf.Struct.$Properties=} [properties] Properties to set
             * @returns {google.protobuf.Struct} Struct instance
             * @type {{
             *   (properties: google.protobuf.Struct.$Shape): google.protobuf.Struct & google.protobuf.Struct.$Shape;
             *   (properties?: google.protobuf.Struct.$Properties): google.protobuf.Struct;
             * }}
             */
            Struct.create = function(properties) {
                return new Struct(properties);
            };

            /**
             * Encodes the specified Struct message. Does not implicitly {@link google.protobuf.Struct.verify|verify} messages.
             * @function encode
             * @memberof google.protobuf.Struct
             * @static
             * @param {google.protobuf.Struct.$Properties} message Struct message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            Struct.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.fields != null && $Object.hasOwnProperty.call(message, "fields"))
                    for (let keys = $Object.keys(message.fields), i = 0; i < keys.length; ++i) {
                        writer.uint32(/* id 1, wireType 2 =*/10).fork().uint32(/* id 1, wireType 2 =*/10).string(keys[i]);
                        $root.google.protobuf.Value.encode(message.fields[keys[i]], writer.uint32(/* id 2, wireType 2 =*/18).fork(), _depth + 1).ldelim().ldelim();
                    }
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a Struct message from the specified reader or buffer.
             * @function decode
             * @memberof google.protobuf.Struct
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {google.protobuf.Struct & google.protobuf.Struct.$Shape} Struct
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            Struct.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message, key, value;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.google.protobuf.Struct();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            if (message.fields === $util.emptyObject)
                                message.fields = {};
                            let end2 = reader.uint32() + reader.pos;
                            if (end2 > reader.len)
                                throw $RangeError("index out of range");
                            reader.len = end2;
                            key = "";
                            value = null;
                            while (reader.pos < end2) {
                                let tag2 = reader.tag();
                                wireType = tag2 & 7;
                                switch (tag2 >>>= 3) {
                                case 1:
                                    if (wireType !== 2)
                                        break;
                                    key = reader.stringVerify();
                                    continue;
                                case 2:
                                    if (wireType !== 2)
                                        break;
                                    value = $root.google.protobuf.Value.decode(reader, reader.uint32(), $undefined, _depth + 1, value);
                                    continue;
                                }
                                reader.skipType(wireType, _depth, tag2);
                            }
                            if (reader.pos !== end2)
                                throw $RangeError("index out of range");
                            reader.len = end;
                            if (key === "__proto__")
                                $util.makeProp(message.fields, key);
                            message.fields[key] = value || new $root.google.protobuf.Value();
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a Struct message.
             * @function verify
             * @memberof google.protobuf.Struct
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            Struct.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                if (message.fields != null && $Object.hasOwnProperty.call(message, "fields")) {
                    if (!$util.isObject(message.fields))
                        return "fields: object expected";
                    let key = $Object.keys(message.fields);
                    for (let i = 0; i < key.length; ++i) {
                        let error = $root.google.protobuf.Value.verify(message.fields[key[i]], _depth + 1);
                        if (error)
                            return "fields." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a Struct message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof google.protobuf.Struct
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {google.protobuf.Struct} Struct
             */
            Struct.fromObject = function (object, _depth) {
                if (object instanceof $root.google.protobuf.Struct)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".google.protobuf.Struct: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.google.protobuf.Struct();
                if (object.fields) {
                    if (!$util.isObject(object.fields))
                        throw $TypeError(".google.protobuf.Struct.fields: object expected");
                    message.fields = {};
                    for (let keys = $Object.keys(object.fields), i = 0; i < keys.length; ++i) {
                        if (keys[i] === "__proto__")
                            $util.makeProp(message.fields, keys[i]);
                        if (!$util.isObject(object.fields[keys[i]]))
                            throw $TypeError(".google.protobuf.Struct.fields: object expected");
                        message.fields[keys[i]] = $root.google.protobuf.Value.fromObject(object.fields[keys[i]], _depth + 1);
                    }
                }
                return message;
            };

            /**
             * Creates a plain object from a Struct message. Also converts values to other types if specified.
             * @function toObject
             * @memberof google.protobuf.Struct
             * @static
             * @param {google.protobuf.Struct} message Struct
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            Struct.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.objects || options.defaults)
                    object.fields = {};
                let keys2;
                if (message.fields && (keys2 = $Object.keys(message.fields)).length) {
                    object.fields = {};
                    for (let j = 0; j < keys2.length; ++j) {
                        if (keys2[j] === "__proto__")
                            $util.makeProp(object.fields, keys2[j]);
                        object.fields[keys2[j]] = $root.google.protobuf.Value.toObject(message.fields[keys2[j]], options, _depth + 1);
                    }
                }
                return object;
            };

            /**
             * Converts this Struct to JSON.
             * @function toJSON
             * @memberof google.protobuf.Struct
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            Struct.prototype.toJSON = function() {
                return Struct.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for Struct
             * @function getTypeUrl
             * @memberof google.protobuf.Struct
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            Struct.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/google.protobuf.Struct";
            };

            return Struct;
        })();

        protobuf.Value = (function() {

            /**
             * Properties of a Value.
             * @typedef {Object} google.protobuf.Value.$Properties
             * @property {google.protobuf.NullValue|null} [nullValue] Value nullValue
             * @property {number|null} [numberValue] Value numberValue
             * @property {string|null} [stringValue] Value stringValue
             * @property {boolean|null} [boolValue] Value boolValue
             * @property {google.protobuf.Struct.$Properties|null} [structValue] Value structValue
             * @property {google.protobuf.ListValue.$Properties|null} [listValue] Value listValue
             * @property {"nullValue"|"numberValue"|"stringValue"|"boolValue"|"structValue"|"listValue"} [kind] Value kind
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a Value.
             * @memberof google.protobuf
             * @interface IValue
             * @augments google.protobuf.Value.$Properties
             * @deprecated Use google.protobuf.Value.$Properties instead.
             */

            /**
             * Narrowed shape of a Value.
             * @typedef {{
             *   nullValue?: google.protobuf.NullValue|null;
             *   numberValue?: number|null;
             *   stringValue?: string|null;
             *   boolValue?: boolean|null;
             *   structValue?: google.protobuf.Struct.$Shape|null;
             *   listValue?: google.protobuf.ListValue.$Shape|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ kind?: undefined; nullValue?: null; numberValue?: null; stringValue?: null; boolValue?: null; structValue?: null; listValue?: null }|{ kind?: "nullValue"; nullValue: google.protobuf.NullValue; numberValue?: null; stringValue?: null; boolValue?: null; structValue?: null; listValue?: null }|{ kind?: "numberValue"; nullValue?: null; numberValue: number; stringValue?: null; boolValue?: null; structValue?: null; listValue?: null }|{ kind?: "stringValue"; nullValue?: null; numberValue?: null; stringValue: string; boolValue?: null; structValue?: null; listValue?: null }|{ kind?: "boolValue"; nullValue?: null; numberValue?: null; stringValue?: null; boolValue: boolean; structValue?: null; listValue?: null }|{ kind?: "structValue"; nullValue?: null; numberValue?: null; stringValue?: null; boolValue?: null; structValue: google.protobuf.Struct.$Shape; listValue?: null }|{ kind?: "listValue"; nullValue?: null; numberValue?: null; stringValue?: null; boolValue?: null; structValue?: null; listValue: google.protobuf.ListValue.$Shape })
             * )} google.protobuf.Value.$Shape
             */

            /**
             * Constructs a new Value.
             * @memberof google.protobuf
             * @classdesc Represents a Value.
             * @constructor
             * @param {google.protobuf.Value.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const Value = function (properties) {
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * Value nullValue.
             * @member {google.protobuf.NullValue|null|undefined} nullValue
             * @memberof google.protobuf.Value
             * @instance
             */
            Value.prototype.nullValue = null;

            /**
             * Value numberValue.
             * @member {number|null|undefined} numberValue
             * @memberof google.protobuf.Value
             * @instance
             */
            Value.prototype.numberValue = null;

            /**
             * Value stringValue.
             * @member {string|null|undefined} stringValue
             * @memberof google.protobuf.Value
             * @instance
             */
            Value.prototype.stringValue = null;

            /**
             * Value boolValue.
             * @member {boolean|null|undefined} boolValue
             * @memberof google.protobuf.Value
             * @instance
             */
            Value.prototype.boolValue = null;

            /**
             * Value structValue.
             * @member {google.protobuf.Struct.$Properties|null|undefined} structValue
             * @memberof google.protobuf.Value
             * @instance
             */
            Value.prototype.structValue = null;

            /**
             * Value listValue.
             * @member {google.protobuf.ListValue.$Properties|null|undefined} listValue
             * @memberof google.protobuf.Value
             * @instance
             */
            Value.prototype.listValue = null;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * Value kind.
             * @member {"nullValue"|"numberValue"|"stringValue"|"boolValue"|"structValue"|"listValue"|undefined} kind
             * @memberof google.protobuf.Value
             * @instance
             */
            $Object.defineProperty(Value.prototype, "kind", {
                get: $util.oneOfGetter($oneOfFields = ["nullValue", "numberValue", "stringValue", "boolValue", "structValue", "listValue"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new Value instance using the specified properties.
             * @function create
             * @memberof google.protobuf.Value
             * @static
             * @param {google.protobuf.Value.$Properties=} [properties] Properties to set
             * @returns {google.protobuf.Value} Value instance
             * @type {{
             *   (properties: google.protobuf.Value.$Shape): google.protobuf.Value & google.protobuf.Value.$Shape;
             *   (properties?: google.protobuf.Value.$Properties): google.protobuf.Value;
             * }}
             */
            Value.create = function(properties) {
                return new Value(properties);
            };

            /**
             * Encodes the specified Value message. Does not implicitly {@link google.protobuf.Value.verify|verify} messages.
             * @function encode
             * @memberof google.protobuf.Value
             * @static
             * @param {google.protobuf.Value.$Properties} message Value message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            Value.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.nullValue != null && $Object.hasOwnProperty.call(message, "nullValue"))
                    writer.uint32(/* id 1, wireType 0 =*/8).int32(message.nullValue);
                if (message.numberValue != null && $Object.hasOwnProperty.call(message, "numberValue"))
                    writer.uint32(/* id 2, wireType 1 =*/17).double(message.numberValue);
                if (message.stringValue != null && $Object.hasOwnProperty.call(message, "stringValue"))
                    writer.uint32(/* id 3, wireType 2 =*/26).string(message.stringValue);
                if (message.boolValue != null && $Object.hasOwnProperty.call(message, "boolValue"))
                    writer.uint32(/* id 4, wireType 0 =*/32).bool(message.boolValue);
                if (message.structValue != null && $Object.hasOwnProperty.call(message, "structValue"))
                    $root.google.protobuf.Struct.encode(message.structValue, writer.uint32(/* id 5, wireType 2 =*/42).fork(), _depth + 1).ldelim();
                if (message.listValue != null && $Object.hasOwnProperty.call(message, "listValue"))
                    $root.google.protobuf.ListValue.encode(message.listValue, writer.uint32(/* id 6, wireType 2 =*/50).fork(), _depth + 1).ldelim();
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a Value message from the specified reader or buffer.
             * @function decode
             * @memberof google.protobuf.Value
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {google.protobuf.Value & google.protobuf.Value.$Shape} Value
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            Value.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message, value;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.google.protobuf.Value();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 0)
                                break;
                            message.nullValue = reader.int32();
                            message.kind = "nullValue";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 1)
                                break;
                            message.numberValue = reader.double();
                            message.kind = "numberValue";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 2)
                                break;
                            message.stringValue = reader.stringVerify();
                            message.kind = "stringValue";
                            continue;
                        }
                    case 4: {
                            if (wireType !== 0)
                                break;
                            message.boolValue = reader.bool();
                            message.kind = "boolValue";
                            continue;
                        }
                    case 5: {
                            if (wireType !== 2)
                                break;
                            message.structValue = $root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1, message.structValue);
                            message.kind = "structValue";
                            continue;
                        }
                    case 6: {
                            if (wireType !== 2)
                                break;
                            message.listValue = $root.google.protobuf.ListValue.decode(reader, reader.uint32(), $undefined, _depth + 1, message.listValue);
                            message.kind = "listValue";
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a Value message.
             * @function verify
             * @memberof google.protobuf.Value
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            Value.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.nullValue != null && $Object.hasOwnProperty.call(message, "nullValue")) {
                    properties.kind = 1;
                    if (typeof message.nullValue !== "number" || (message.nullValue | 0) !== message.nullValue)
                        return "nullValue: enum value expected";
                }
                if (message.numberValue != null && $Object.hasOwnProperty.call(message, "numberValue")) {
                    if (properties.kind === 1)
                        return "kind: multiple values";
                    properties.kind = 1;
                    if (typeof message.numberValue !== "number")
                        return "numberValue: number expected";
                }
                if (message.stringValue != null && $Object.hasOwnProperty.call(message, "stringValue")) {
                    if (properties.kind === 1)
                        return "kind: multiple values";
                    properties.kind = 1;
                    if (!$util.isString(message.stringValue))
                        return "stringValue: string expected";
                }
                if (message.boolValue != null && $Object.hasOwnProperty.call(message, "boolValue")) {
                    if (properties.kind === 1)
                        return "kind: multiple values";
                    properties.kind = 1;
                    if (typeof message.boolValue !== "boolean")
                        return "boolValue: boolean expected";
                }
                if (message.structValue != null && $Object.hasOwnProperty.call(message, "structValue")) {
                    if (properties.kind === 1)
                        return "kind: multiple values";
                    properties.kind = 1;
                    {
                        let error = $root.google.protobuf.Struct.verify(message.structValue, _depth + 1);
                        if (error)
                            return "structValue." + error;
                    }
                }
                if (message.listValue != null && $Object.hasOwnProperty.call(message, "listValue")) {
                    if (properties.kind === 1)
                        return "kind: multiple values";
                    properties.kind = 1;
                    {
                        let error = $root.google.protobuf.ListValue.verify(message.listValue, _depth + 1);
                        if (error)
                            return "listValue." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a Value message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof google.protobuf.Value
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {google.protobuf.Value} Value
             */
            Value.fromObject = function (object, _depth) {
                if (object instanceof $root.google.protobuf.Value)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".google.protobuf.Value: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.google.protobuf.Value();
                switch (object.nullValue) {
                case "NULL_VALUE":
                case 0:
                    message.nullValue = 0;
                    break;
                default:
                    if (typeof object.nullValue === "number" && (object.nullValue | 0) === object.nullValue)
                        message.nullValue = object.nullValue;
                }
                if (object.numberValue != null)
                    message.numberValue = $Number(object.numberValue);
                if (object.stringValue != null)
                    message.stringValue = $String(object.stringValue);
                if (object.boolValue != null)
                    message.boolValue = $Boolean(object.boolValue);
                if (object.structValue != null) {
                    if (!$util.isObject(object.structValue))
                        throw $TypeError(".google.protobuf.Value.structValue: object expected");
                    message.structValue = $root.google.protobuf.Struct.fromObject(object.structValue, _depth + 1);
                }
                if (object.listValue != null) {
                    if (!$util.isObject(object.listValue))
                        throw $TypeError(".google.protobuf.Value.listValue: object expected");
                    message.listValue = $root.google.protobuf.ListValue.fromObject(object.listValue, _depth + 1);
                }
                return message;
            };

            /**
             * Creates a plain object from a Value message. Also converts values to other types if specified.
             * @function toObject
             * @memberof google.protobuf.Value
             * @static
             * @param {google.protobuf.Value} message Value
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            Value.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (message.nullValue != null && $Object.hasOwnProperty.call(message, "nullValue")) {
                    object.nullValue = options.enums === $String ? $root.google.protobuf.NullValue[message.nullValue] === $undefined ? message.nullValue : $root.google.protobuf.NullValue[message.nullValue] : message.nullValue;
                    if (options.oneofs)
                        object.kind = "nullValue";
                }
                if (message.numberValue != null && $Object.hasOwnProperty.call(message, "numberValue")) {
                    object.numberValue = options.json && !$isFinite(message.numberValue) ? $String(message.numberValue) : message.numberValue;
                    if (options.oneofs)
                        object.kind = "numberValue";
                }
                if (message.stringValue != null && $Object.hasOwnProperty.call(message, "stringValue")) {
                    object.stringValue = message.stringValue;
                    if (options.oneofs)
                        object.kind = "stringValue";
                }
                if (message.boolValue != null && $Object.hasOwnProperty.call(message, "boolValue")) {
                    object.boolValue = message.boolValue;
                    if (options.oneofs)
                        object.kind = "boolValue";
                }
                if (message.structValue != null && $Object.hasOwnProperty.call(message, "structValue")) {
                    object.structValue = $root.google.protobuf.Struct.toObject(message.structValue, options, _depth + 1);
                    if (options.oneofs)
                        object.kind = "structValue";
                }
                if (message.listValue != null && $Object.hasOwnProperty.call(message, "listValue")) {
                    object.listValue = $root.google.protobuf.ListValue.toObject(message.listValue, options, _depth + 1);
                    if (options.oneofs)
                        object.kind = "listValue";
                }
                return object;
            };

            /**
             * Converts this Value to JSON.
             * @function toJSON
             * @memberof google.protobuf.Value
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            Value.prototype.toJSON = function() {
                return Value.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for Value
             * @function getTypeUrl
             * @memberof google.protobuf.Value
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            Value.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/google.protobuf.Value";
            };

            return Value;
        })();

        /**
         * NullValue enum.
         * @name google.protobuf.NullValue
         * @enum {number}
         * @property {number} NULL_VALUE=0 NULL_VALUE value
         */
        protobuf.NullValue = (function() {
            const valuesById = $Object.create(null), values = $Object.create(valuesById);
            values[valuesById[0] = "NULL_VALUE"] = 0;
            return values;
        })();

        protobuf.ListValue = (function() {

            /**
             * Properties of a ListValue.
             * @typedef {Object} google.protobuf.ListValue.$Properties
             * @property {Array.<google.protobuf.Value.$Properties>|null} [values] ListValue values
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a ListValue.
             * @memberof google.protobuf
             * @interface IListValue
             * @augments google.protobuf.ListValue.$Properties
             * @deprecated Use google.protobuf.ListValue.$Properties instead.
             */

            /**
             * Shape of a ListValue.
             * @typedef {{
             *   values?: Array.<google.protobuf.Value.$Shape>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * }} google.protobuf.ListValue.$Shape
             */

            /**
             * Constructs a new ListValue.
             * @memberof google.protobuf
             * @classdesc Represents a ListValue.
             * @constructor
             * @param {google.protobuf.ListValue.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const ListValue = function (properties) {
                this.values = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * ListValue values.
             * @member {Array.<google.protobuf.Value.$Properties>} values
             * @memberof google.protobuf.ListValue
             * @instance
             */
            ListValue.prototype.values = $util.emptyArray;

            /**
             * Creates a new ListValue instance using the specified properties.
             * @function create
             * @memberof google.protobuf.ListValue
             * @static
             * @param {google.protobuf.ListValue.$Properties=} [properties] Properties to set
             * @returns {google.protobuf.ListValue} ListValue instance
             * @type {{
             *   (properties: google.protobuf.ListValue.$Shape): google.protobuf.ListValue & google.protobuf.ListValue.$Shape;
             *   (properties?: google.protobuf.ListValue.$Properties): google.protobuf.ListValue;
             * }}
             */
            ListValue.create = function(properties) {
                return new ListValue(properties);
            };

            /**
             * Encodes the specified ListValue message. Does not implicitly {@link google.protobuf.ListValue.verify|verify} messages.
             * @function encode
             * @memberof google.protobuf.ListValue
             * @static
             * @param {google.protobuf.ListValue.$Properties} message ListValue message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            ListValue.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.values != null && message.values.length)
                    for (let i = 0; i < message.values.length; ++i)
                        $root.google.protobuf.Value.encode(message.values[i], writer.uint32(/* id 1, wireType 2 =*/10).fork(), _depth + 1).ldelim();
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a ListValue message from the specified reader or buffer.
             * @function decode
             * @memberof google.protobuf.ListValue
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {google.protobuf.ListValue & google.protobuf.ListValue.$Shape} ListValue
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            ListValue.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.google.protobuf.ListValue();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            if (!(message.values && message.values.length))
                                message.values = [];
                            message.values.push($root.google.protobuf.Value.decode(reader, reader.uint32(), $undefined, _depth + 1));
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a ListValue message.
             * @function verify
             * @memberof google.protobuf.ListValue
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            ListValue.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                if (message.values != null && $Object.hasOwnProperty.call(message, "values")) {
                    if (!$Array.isArray(message.values))
                        return "values: array expected";
                    for (let i = 0; i < message.values.length; ++i) {
                        let error = $root.google.protobuf.Value.verify(message.values[i], _depth + 1);
                        if (error)
                            return "values." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a ListValue message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof google.protobuf.ListValue
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {google.protobuf.ListValue} ListValue
             */
            ListValue.fromObject = function (object, _depth) {
                if (object instanceof $root.google.protobuf.ListValue)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".google.protobuf.ListValue: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.google.protobuf.ListValue();
                if (object.values) {
                    if (!$Array.isArray(object.values))
                        throw $TypeError(".google.protobuf.ListValue.values: array expected");
                    message.values = $Array(object.values.length);
                    for (let i = 0; i < object.values.length; ++i) {
                        if (!$util.isObject(object.values[i]))
                            throw $TypeError(".google.protobuf.ListValue.values: object expected");
                        message.values[i] = $root.google.protobuf.Value.fromObject(object.values[i], _depth + 1);
                    }
                }
                return message;
            };

            /**
             * Creates a plain object from a ListValue message. Also converts values to other types if specified.
             * @function toObject
             * @memberof google.protobuf.ListValue
             * @static
             * @param {google.protobuf.ListValue} message ListValue
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            ListValue.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults)
                    object.values = [];
                if (message.values && message.values.length) {
                    object.values = $Array(message.values.length);
                    for (let j = 0; j < message.values.length; ++j)
                        object.values[j] = $root.google.protobuf.Value.toObject(message.values[j], options, _depth + 1);
                }
                return object;
            };

            /**
             * Converts this ListValue to JSON.
             * @function toJSON
             * @memberof google.protobuf.ListValue
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            ListValue.prototype.toJSON = function() {
                return ListValue.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for ListValue
             * @function getTypeUrl
             * @memberof google.protobuf.ListValue
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            ListValue.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/google.protobuf.ListValue";
            };

            return ListValue;
        })();

        return protobuf;
    })();

    return google;
})();

export const northstar = $root.northstar = (() => {

    /**
     * Namespace northstar.
     * @exports northstar
     * @namespace
     */
    const northstar = {};

    northstar.data_hub = (function() {

        /**
         * Namespace data_hub.
         * @memberof northstar
         * @namespace
         */
        const data_hub = {};

        data_hub.AdmissionRejection = (function() {

            /**
             * Properties of an AdmissionRejection.
             * @typedef {Object} northstar.data_hub.AdmissionRejection.$Properties
             * @property {string|null} [reason] AdmissionRejection reason
             * @property {string|null} [rejection_id] AdmissionRejection rejection_id
             * @property {Object.<string,google.protobuf.Value.$Properties>|null} [evidence_fields] AdmissionRejection evidence_fields
             * @property {"reason"} [_reason] AdmissionRejection _reason
             * @property {"rejection_id"} [_rejection_id] AdmissionRejection _rejection_id
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of an AdmissionRejection.
             * @memberof northstar.data_hub
             * @interface IAdmissionRejection
             * @augments northstar.data_hub.AdmissionRejection.$Properties
             * @deprecated Use northstar.data_hub.AdmissionRejection.$Properties instead.
             */

            /**
             * Narrowed shape of an AdmissionRejection.
             * @typedef {{
             *   reason?: string|null;
             *   rejection_id?: string|null;
             *   evidence_fields?: Object.<string,google.protobuf.Value.$Shape>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _reason?: undefined; reason?: null }|{ _reason?: "reason"; reason: string })
             * ) & (
             *   ({ _rejection_id?: undefined; rejection_id?: null }|{ _rejection_id?: "rejection_id"; rejection_id: string })
             * )} northstar.data_hub.AdmissionRejection.$Shape
             */

            /**
             * Constructs a new AdmissionRejection.
             * @memberof northstar.data_hub
             * @classdesc Represents an AdmissionRejection.
             * @constructor
             * @param {northstar.data_hub.AdmissionRejection.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const AdmissionRejection = function (properties) {
                this.evidence_fields = {};
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * AdmissionRejection reason.
             * @member {string|null|undefined} reason
             * @memberof northstar.data_hub.AdmissionRejection
             * @instance
             */
            AdmissionRejection.prototype.reason = null;

            /**
             * AdmissionRejection rejection_id.
             * @member {string|null|undefined} rejection_id
             * @memberof northstar.data_hub.AdmissionRejection
             * @instance
             */
            AdmissionRejection.prototype.rejection_id = null;

            /**
             * AdmissionRejection evidence_fields.
             * @member {Object.<string,google.protobuf.Value.$Properties>} evidence_fields
             * @memberof northstar.data_hub.AdmissionRejection
             * @instance
             */
            AdmissionRejection.prototype.evidence_fields = $util.emptyObject;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * AdmissionRejection _reason.
             * @member {"reason"|undefined} _reason
             * @memberof northstar.data_hub.AdmissionRejection
             * @instance
             */
            $Object.defineProperty(AdmissionRejection.prototype, "_reason", {
                get: $util.oneOfGetter($oneOfFields = ["reason"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * AdmissionRejection _rejection_id.
             * @member {"rejection_id"|undefined} _rejection_id
             * @memberof northstar.data_hub.AdmissionRejection
             * @instance
             */
            $Object.defineProperty(AdmissionRejection.prototype, "_rejection_id", {
                get: $util.oneOfGetter($oneOfFields = ["rejection_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new AdmissionRejection instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.AdmissionRejection
             * @static
             * @param {northstar.data_hub.AdmissionRejection.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.AdmissionRejection} AdmissionRejection instance
             * @type {{
             *   (properties: northstar.data_hub.AdmissionRejection.$Shape): northstar.data_hub.AdmissionRejection & northstar.data_hub.AdmissionRejection.$Shape;
             *   (properties?: northstar.data_hub.AdmissionRejection.$Properties): northstar.data_hub.AdmissionRejection;
             * }}
             */
            AdmissionRejection.create = function(properties) {
                return new AdmissionRejection(properties);
            };

            /**
             * Encodes the specified AdmissionRejection message. Does not implicitly {@link northstar.data_hub.AdmissionRejection.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.AdmissionRejection
             * @static
             * @param {northstar.data_hub.AdmissionRejection.$Properties} message AdmissionRejection message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            AdmissionRejection.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.reason != null && $Object.hasOwnProperty.call(message, "reason"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.reason);
                if (message.rejection_id != null && $Object.hasOwnProperty.call(message, "rejection_id"))
                    writer.uint32(/* id 2, wireType 2 =*/18).string(message.rejection_id);
                if (message.evidence_fields != null && $Object.hasOwnProperty.call(message, "evidence_fields"))
                    for (let keys = $Object.keys(message.evidence_fields), i = 0; i < keys.length; ++i) {
                        writer.uint32(/* id 1000, wireType 2 =*/8002).fork().uint32(/* id 1, wireType 2 =*/10).string(keys[i]);
                        $root.google.protobuf.Value.encode(message.evidence_fields[keys[i]], writer.uint32(/* id 2, wireType 2 =*/18).fork(), _depth + 1).ldelim().ldelim();
                    }
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes an AdmissionRejection message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.AdmissionRejection
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.AdmissionRejection & northstar.data_hub.AdmissionRejection.$Shape} AdmissionRejection
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            AdmissionRejection.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message, key, value;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.AdmissionRejection();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.reason = reader.stringVerify();
                            message._reason = "reason";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 2)
                                break;
                            message.rejection_id = reader.stringVerify();
                            message._rejection_id = "rejection_id";
                            continue;
                        }
                    case 1000: {
                            if (wireType !== 2)
                                break;
                            if (message.evidence_fields === $util.emptyObject)
                                message.evidence_fields = {};
                            let end2 = reader.uint32() + reader.pos;
                            if (end2 > reader.len)
                                throw $RangeError("index out of range");
                            reader.len = end2;
                            key = "";
                            value = null;
                            while (reader.pos < end2) {
                                let tag2 = reader.tag();
                                wireType = tag2 & 7;
                                switch (tag2 >>>= 3) {
                                case 1:
                                    if (wireType !== 2)
                                        break;
                                    key = reader.stringVerify();
                                    continue;
                                case 2:
                                    if (wireType !== 2)
                                        break;
                                    value = $root.google.protobuf.Value.decode(reader, reader.uint32(), $undefined, _depth + 1, value);
                                    continue;
                                }
                                reader.skipType(wireType, _depth, tag2);
                            }
                            if (reader.pos !== end2)
                                throw $RangeError("index out of range");
                            reader.len = end;
                            if (key === "__proto__")
                                $util.makeProp(message.evidence_fields, key);
                            message.evidence_fields[key] = value || new $root.google.protobuf.Value();
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies an AdmissionRejection message.
             * @function verify
             * @memberof northstar.data_hub.AdmissionRejection
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            AdmissionRejection.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.reason != null && $Object.hasOwnProperty.call(message, "reason")) {
                    properties._reason = 1;
                    if (!$util.isString(message.reason))
                        return "reason: string expected";
                }
                if (message.rejection_id != null && $Object.hasOwnProperty.call(message, "rejection_id")) {
                    properties._rejection_id = 1;
                    if (!$util.isString(message.rejection_id))
                        return "rejection_id: string expected";
                }
                if (message.evidence_fields != null && $Object.hasOwnProperty.call(message, "evidence_fields")) {
                    if (!$util.isObject(message.evidence_fields))
                        return "evidence_fields: object expected";
                    let key = $Object.keys(message.evidence_fields);
                    for (let i = 0; i < key.length; ++i) {
                        let error = $root.google.protobuf.Value.verify(message.evidence_fields[key[i]], _depth + 1);
                        if (error)
                            return "evidence_fields." + error;
                    }
                }
                return null;
            };

            /**
             * Creates an AdmissionRejection message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.AdmissionRejection
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.AdmissionRejection} AdmissionRejection
             */
            AdmissionRejection.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.AdmissionRejection)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.AdmissionRejection: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.AdmissionRejection();
                if (object.reason != null)
                    message.reason = $String(object.reason);
                if (object.rejection_id != null)
                    message.rejection_id = $String(object.rejection_id);
                if (object.evidence_fields) {
                    if (!$util.isObject(object.evidence_fields))
                        throw $TypeError(".northstar.data_hub.AdmissionRejection.evidence_fields: object expected");
                    message.evidence_fields = {};
                    for (let keys = $Object.keys(object.evidence_fields), i = 0; i < keys.length; ++i) {
                        if (keys[i] === "__proto__")
                            $util.makeProp(message.evidence_fields, keys[i]);
                        if (!$util.isObject(object.evidence_fields[keys[i]]))
                            throw $TypeError(".northstar.data_hub.AdmissionRejection.evidence_fields: object expected");
                        message.evidence_fields[keys[i]] = $root.google.protobuf.Value.fromObject(object.evidence_fields[keys[i]], _depth + 1);
                    }
                }
                return message;
            };

            /**
             * Creates a plain object from an AdmissionRejection message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.AdmissionRejection
             * @static
             * @param {northstar.data_hub.AdmissionRejection} message AdmissionRejection
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            AdmissionRejection.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.objects || options.defaults)
                    object.evidence_fields = {};
                if (message.reason != null && $Object.hasOwnProperty.call(message, "reason")) {
                    object.reason = message.reason;
                    if (options.oneofs)
                        object._reason = "reason";
                }
                if (message.rejection_id != null && $Object.hasOwnProperty.call(message, "rejection_id")) {
                    object.rejection_id = message.rejection_id;
                    if (options.oneofs)
                        object._rejection_id = "rejection_id";
                }
                let keys2;
                if (message.evidence_fields && (keys2 = $Object.keys(message.evidence_fields)).length) {
                    object.evidence_fields = {};
                    for (let j = 0; j < keys2.length; ++j) {
                        if (keys2[j] === "__proto__")
                            $util.makeProp(object.evidence_fields, keys2[j]);
                        object.evidence_fields[keys2[j]] = $root.google.protobuf.Value.toObject(message.evidence_fields[keys2[j]], options, _depth + 1);
                    }
                }
                return object;
            };

            /**
             * Converts this AdmissionRejection to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.AdmissionRejection
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            AdmissionRejection.prototype.toJSON = function() {
                return AdmissionRejection.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for AdmissionRejection
             * @function getTypeUrl
             * @memberof northstar.data_hub.AdmissionRejection
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            AdmissionRejection.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.AdmissionRejection";
            };

            return AdmissionRejection;
        })();

        data_hub.BrowserSession = (function() {

            /**
             * Properties of a BrowserSession.
             * @typedef {Object} northstar.data_hub.BrowserSession.$Properties
             * @property {string|null} [csrf] BrowserSession csrf
             * @property {"csrf"} [_csrf] BrowserSession _csrf
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a BrowserSession.
             * @memberof northstar.data_hub
             * @interface IBrowserSession
             * @augments northstar.data_hub.BrowserSession.$Properties
             * @deprecated Use northstar.data_hub.BrowserSession.$Properties instead.
             */

            /**
             * Narrowed shape of a BrowserSession.
             * @typedef {{
             *   csrf?: string|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _csrf?: undefined; csrf?: null }|{ _csrf?: "csrf"; csrf: string })
             * )} northstar.data_hub.BrowserSession.$Shape
             */

            /**
             * Constructs a new BrowserSession.
             * @memberof northstar.data_hub
             * @classdesc Represents a BrowserSession.
             * @constructor
             * @param {northstar.data_hub.BrowserSession.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const BrowserSession = function (properties) {
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * BrowserSession csrf.
             * @member {string|null|undefined} csrf
             * @memberof northstar.data_hub.BrowserSession
             * @instance
             */
            BrowserSession.prototype.csrf = null;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * BrowserSession _csrf.
             * @member {"csrf"|undefined} _csrf
             * @memberof northstar.data_hub.BrowserSession
             * @instance
             */
            $Object.defineProperty(BrowserSession.prototype, "_csrf", {
                get: $util.oneOfGetter($oneOfFields = ["csrf"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new BrowserSession instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.BrowserSession
             * @static
             * @param {northstar.data_hub.BrowserSession.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.BrowserSession} BrowserSession instance
             * @type {{
             *   (properties: northstar.data_hub.BrowserSession.$Shape): northstar.data_hub.BrowserSession & northstar.data_hub.BrowserSession.$Shape;
             *   (properties?: northstar.data_hub.BrowserSession.$Properties): northstar.data_hub.BrowserSession;
             * }}
             */
            BrowserSession.create = function(properties) {
                return new BrowserSession(properties);
            };

            /**
             * Encodes the specified BrowserSession message. Does not implicitly {@link northstar.data_hub.BrowserSession.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.BrowserSession
             * @static
             * @param {northstar.data_hub.BrowserSession.$Properties} message BrowserSession message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            BrowserSession.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.csrf != null && $Object.hasOwnProperty.call(message, "csrf"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.csrf);
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a BrowserSession message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.BrowserSession
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.BrowserSession & northstar.data_hub.BrowserSession.$Shape} BrowserSession
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            BrowserSession.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.BrowserSession();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.csrf = reader.stringVerify();
                            message._csrf = "csrf";
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a BrowserSession message.
             * @function verify
             * @memberof northstar.data_hub.BrowserSession
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            BrowserSession.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.csrf != null && $Object.hasOwnProperty.call(message, "csrf")) {
                    properties._csrf = 1;
                    if (!$util.isString(message.csrf))
                        return "csrf: string expected";
                }
                return null;
            };

            /**
             * Creates a BrowserSession message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.BrowserSession
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.BrowserSession} BrowserSession
             */
            BrowserSession.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.BrowserSession)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.BrowserSession: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.BrowserSession();
                if (object.csrf != null)
                    message.csrf = $String(object.csrf);
                return message;
            };

            /**
             * Creates a plain object from a BrowserSession message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.BrowserSession
             * @static
             * @param {northstar.data_hub.BrowserSession} message BrowserSession
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            BrowserSession.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (message.csrf != null && $Object.hasOwnProperty.call(message, "csrf")) {
                    object.csrf = message.csrf;
                    if (options.oneofs)
                        object._csrf = "csrf";
                }
                return object;
            };

            /**
             * Converts this BrowserSession to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.BrowserSession
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            BrowserSession.prototype.toJSON = function() {
                return BrowserSession.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for BrowserSession
             * @function getTypeUrl
             * @memberof northstar.data_hub.BrowserSession
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            BrowserSession.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.BrowserSession";
            };

            return BrowserSession;
        })();

        data_hub.DatasetDetails = (function() {

            /**
             * Properties of a DatasetDetails.
             * @typedef {Object} northstar.data_hub.DatasetDetails.$Properties
             * @property {string|null} [availability_basis] DatasetDetails availability_basis
             * @property {string|null} [availability_note] DatasetDetails availability_note
             * @property {number|Long|null} [bar_count] DatasetDetails bar_count
             * @property {string|null} [content_hash] DatasetDetails content_hash
             * @property {string|null} [exchange] DatasetDetails exchange
             * @property {northstar.data_hub.ImportSpecification.$Properties|null} [import_spec] DatasetDetails import_spec
             * @property {Array.<string>|null} [limitations] DatasetDetails limitations
             * @property {google.protobuf.Struct.$Properties|null} [processing_provenance] DatasetDetails processing_provenance
             * @property {string|null} [product] DatasetDetails product
             * @property {string|null} [published_at] DatasetDetails published_at
             * @property {google.protobuf.Struct.$Properties|null} [quality] DatasetDetails quality
             * @property {google.protobuf.Struct.$Properties|null} [semantics] DatasetDetails semantics
             * @property {string|null} [session_close] DatasetDetails session_close
             * @property {string|null} [session_open] DatasetDetails session_open
             * @property {string|null} [snapshot_id] DatasetDetails snapshot_id
             * @property {string|null} [source_reference] DatasetDetails source_reference
             * @property {Array.<google.protobuf.Struct.$Properties>|null} [sources] DatasetDetails sources
             * @property {string|null} [symbol] DatasetDetails symbol
             * @property {string|null} [trading_day] DatasetDetails trading_day
             * @property {Array.<string>|null} [null_fields] DatasetDetails null_fields
             * @property {"availability_basis"} [_availability_basis] DatasetDetails _availability_basis
             * @property {"availability_note"} [_availability_note] DatasetDetails _availability_note
             * @property {"bar_count"} [_bar_count] DatasetDetails _bar_count
             * @property {"content_hash"} [_content_hash] DatasetDetails _content_hash
             * @property {"exchange"} [_exchange] DatasetDetails _exchange
             * @property {"import_spec"} [_import_spec] DatasetDetails _import_spec
             * @property {"processing_provenance"} [_processing_provenance] DatasetDetails _processing_provenance
             * @property {"product"} [_product] DatasetDetails _product
             * @property {"published_at"} [_published_at] DatasetDetails _published_at
             * @property {"quality"} [_quality] DatasetDetails _quality
             * @property {"semantics"} [_semantics] DatasetDetails _semantics
             * @property {"session_close"} [_session_close] DatasetDetails _session_close
             * @property {"session_open"} [_session_open] DatasetDetails _session_open
             * @property {"snapshot_id"} [_snapshot_id] DatasetDetails _snapshot_id
             * @property {"source_reference"} [_source_reference] DatasetDetails _source_reference
             * @property {"symbol"} [_symbol] DatasetDetails _symbol
             * @property {"trading_day"} [_trading_day] DatasetDetails _trading_day
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a DatasetDetails.
             * @memberof northstar.data_hub
             * @interface IDatasetDetails
             * @augments northstar.data_hub.DatasetDetails.$Properties
             * @deprecated Use northstar.data_hub.DatasetDetails.$Properties instead.
             */

            /**
             * Narrowed shape of a DatasetDetails.
             * @typedef {{
             *   availability_basis?: string|null;
             *   availability_note?: string|null;
             *   bar_count?: number|Long|null;
             *   content_hash?: string|null;
             *   exchange?: string|null;
             *   import_spec?: northstar.data_hub.ImportSpecification.$Shape|null;
             *   limitations?: Array.<string>|null;
             *   processing_provenance?: google.protobuf.Struct.$Shape|null;
             *   product?: string|null;
             *   published_at?: string|null;
             *   quality?: google.protobuf.Struct.$Shape|null;
             *   semantics?: google.protobuf.Struct.$Shape|null;
             *   session_close?: string|null;
             *   session_open?: string|null;
             *   snapshot_id?: string|null;
             *   source_reference?: string|null;
             *   sources?: Array.<google.protobuf.Struct.$Shape>|null;
             *   symbol?: string|null;
             *   trading_day?: string|null;
             *   null_fields?: Array.<string>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _availability_basis?: undefined; availability_basis?: null }|{ _availability_basis?: "availability_basis"; availability_basis: string })
             * ) & (
             *   ({ _availability_note?: undefined; availability_note?: null }|{ _availability_note?: "availability_note"; availability_note: string })
             * ) & (
             *   ({ _bar_count?: undefined; bar_count?: null }|{ _bar_count?: "bar_count"; bar_count: number|Long })
             * ) & (
             *   ({ _content_hash?: undefined; content_hash?: null }|{ _content_hash?: "content_hash"; content_hash: string })
             * ) & (
             *   ({ _exchange?: undefined; exchange?: null }|{ _exchange?: "exchange"; exchange: string })
             * ) & (
             *   ({ _import_spec?: undefined; import_spec?: null }|{ _import_spec?: "import_spec"; import_spec: northstar.data_hub.ImportSpecification.$Shape })
             * ) & (
             *   ({ _processing_provenance?: undefined; processing_provenance?: null }|{ _processing_provenance?: "processing_provenance"; processing_provenance: google.protobuf.Struct.$Shape })
             * ) & (
             *   ({ _product?: undefined; product?: null }|{ _product?: "product"; product: string })
             * ) & (
             *   ({ _published_at?: undefined; published_at?: null }|{ _published_at?: "published_at"; published_at: string })
             * ) & (
             *   ({ _quality?: undefined; quality?: null }|{ _quality?: "quality"; quality: google.protobuf.Struct.$Shape })
             * ) & (
             *   ({ _semantics?: undefined; semantics?: null }|{ _semantics?: "semantics"; semantics: google.protobuf.Struct.$Shape })
             * ) & (
             *   ({ _session_close?: undefined; session_close?: null }|{ _session_close?: "session_close"; session_close: string })
             * ) & (
             *   ({ _session_open?: undefined; session_open?: null }|{ _session_open?: "session_open"; session_open: string })
             * ) & (
             *   ({ _snapshot_id?: undefined; snapshot_id?: null }|{ _snapshot_id?: "snapshot_id"; snapshot_id: string })
             * ) & (
             *   ({ _source_reference?: undefined; source_reference?: null }|{ _source_reference?: "source_reference"; source_reference: string })
             * ) & (
             *   ({ _symbol?: undefined; symbol?: null }|{ _symbol?: "symbol"; symbol: string })
             * ) & (
             *   ({ _trading_day?: undefined; trading_day?: null }|{ _trading_day?: "trading_day"; trading_day: string })
             * )} northstar.data_hub.DatasetDetails.$Shape
             */

            /**
             * Constructs a new DatasetDetails.
             * @memberof northstar.data_hub
             * @classdesc Represents a DatasetDetails.
             * @constructor
             * @param {northstar.data_hub.DatasetDetails.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const DatasetDetails = function (properties) {
                this.limitations = [];
                this.sources = [];
                this.null_fields = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * DatasetDetails availability_basis.
             * @member {string|null|undefined} availability_basis
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.availability_basis = null;

            /**
             * DatasetDetails availability_note.
             * @member {string|null|undefined} availability_note
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.availability_note = null;

            /**
             * DatasetDetails bar_count.
             * @member {number|Long|null|undefined} bar_count
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.bar_count = null;

            /**
             * DatasetDetails content_hash.
             * @member {string|null|undefined} content_hash
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.content_hash = null;

            /**
             * DatasetDetails exchange.
             * @member {string|null|undefined} exchange
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.exchange = null;

            /**
             * DatasetDetails import_spec.
             * @member {northstar.data_hub.ImportSpecification.$Properties|null|undefined} import_spec
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.import_spec = null;

            /**
             * DatasetDetails limitations.
             * @member {Array.<string>} limitations
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.limitations = $util.emptyArray;

            /**
             * DatasetDetails processing_provenance.
             * @member {google.protobuf.Struct.$Properties|null|undefined} processing_provenance
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.processing_provenance = null;

            /**
             * DatasetDetails product.
             * @member {string|null|undefined} product
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.product = null;

            /**
             * DatasetDetails published_at.
             * @member {string|null|undefined} published_at
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.published_at = null;

            /**
             * DatasetDetails quality.
             * @member {google.protobuf.Struct.$Properties|null|undefined} quality
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.quality = null;

            /**
             * DatasetDetails semantics.
             * @member {google.protobuf.Struct.$Properties|null|undefined} semantics
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.semantics = null;

            /**
             * DatasetDetails session_close.
             * @member {string|null|undefined} session_close
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.session_close = null;

            /**
             * DatasetDetails session_open.
             * @member {string|null|undefined} session_open
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.session_open = null;

            /**
             * DatasetDetails snapshot_id.
             * @member {string|null|undefined} snapshot_id
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.snapshot_id = null;

            /**
             * DatasetDetails source_reference.
             * @member {string|null|undefined} source_reference
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.source_reference = null;

            /**
             * DatasetDetails sources.
             * @member {Array.<google.protobuf.Struct.$Properties>} sources
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.sources = $util.emptyArray;

            /**
             * DatasetDetails symbol.
             * @member {string|null|undefined} symbol
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.symbol = null;

            /**
             * DatasetDetails trading_day.
             * @member {string|null|undefined} trading_day
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.trading_day = null;

            /**
             * DatasetDetails null_fields.
             * @member {Array.<string>} null_fields
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            DatasetDetails.prototype.null_fields = $util.emptyArray;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * DatasetDetails _availability_basis.
             * @member {"availability_basis"|undefined} _availability_basis
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_availability_basis", {
                get: $util.oneOfGetter($oneOfFields = ["availability_basis"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _availability_note.
             * @member {"availability_note"|undefined} _availability_note
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_availability_note", {
                get: $util.oneOfGetter($oneOfFields = ["availability_note"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _bar_count.
             * @member {"bar_count"|undefined} _bar_count
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_bar_count", {
                get: $util.oneOfGetter($oneOfFields = ["bar_count"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _content_hash.
             * @member {"content_hash"|undefined} _content_hash
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_content_hash", {
                get: $util.oneOfGetter($oneOfFields = ["content_hash"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _exchange.
             * @member {"exchange"|undefined} _exchange
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_exchange", {
                get: $util.oneOfGetter($oneOfFields = ["exchange"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _import_spec.
             * @member {"import_spec"|undefined} _import_spec
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_import_spec", {
                get: $util.oneOfGetter($oneOfFields = ["import_spec"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _processing_provenance.
             * @member {"processing_provenance"|undefined} _processing_provenance
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_processing_provenance", {
                get: $util.oneOfGetter($oneOfFields = ["processing_provenance"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _product.
             * @member {"product"|undefined} _product
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_product", {
                get: $util.oneOfGetter($oneOfFields = ["product"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _published_at.
             * @member {"published_at"|undefined} _published_at
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_published_at", {
                get: $util.oneOfGetter($oneOfFields = ["published_at"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _quality.
             * @member {"quality"|undefined} _quality
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_quality", {
                get: $util.oneOfGetter($oneOfFields = ["quality"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _semantics.
             * @member {"semantics"|undefined} _semantics
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_semantics", {
                get: $util.oneOfGetter($oneOfFields = ["semantics"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _session_close.
             * @member {"session_close"|undefined} _session_close
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_session_close", {
                get: $util.oneOfGetter($oneOfFields = ["session_close"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _session_open.
             * @member {"session_open"|undefined} _session_open
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_session_open", {
                get: $util.oneOfGetter($oneOfFields = ["session_open"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _snapshot_id.
             * @member {"snapshot_id"|undefined} _snapshot_id
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_snapshot_id", {
                get: $util.oneOfGetter($oneOfFields = ["snapshot_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _source_reference.
             * @member {"source_reference"|undefined} _source_reference
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_source_reference", {
                get: $util.oneOfGetter($oneOfFields = ["source_reference"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _symbol.
             * @member {"symbol"|undefined} _symbol
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_symbol", {
                get: $util.oneOfGetter($oneOfFields = ["symbol"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetDetails _trading_day.
             * @member {"trading_day"|undefined} _trading_day
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             */
            $Object.defineProperty(DatasetDetails.prototype, "_trading_day", {
                get: $util.oneOfGetter($oneOfFields = ["trading_day"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new DatasetDetails instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.DatasetDetails
             * @static
             * @param {northstar.data_hub.DatasetDetails.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.DatasetDetails} DatasetDetails instance
             * @type {{
             *   (properties: northstar.data_hub.DatasetDetails.$Shape): northstar.data_hub.DatasetDetails & northstar.data_hub.DatasetDetails.$Shape;
             *   (properties?: northstar.data_hub.DatasetDetails.$Properties): northstar.data_hub.DatasetDetails;
             * }}
             */
            DatasetDetails.create = function(properties) {
                return new DatasetDetails(properties);
            };

            /**
             * Encodes the specified DatasetDetails message. Does not implicitly {@link northstar.data_hub.DatasetDetails.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.DatasetDetails
             * @static
             * @param {northstar.data_hub.DatasetDetails.$Properties} message DatasetDetails message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            DatasetDetails.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.availability_basis != null && $Object.hasOwnProperty.call(message, "availability_basis"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.availability_basis);
                if (message.availability_note != null && $Object.hasOwnProperty.call(message, "availability_note"))
                    writer.uint32(/* id 2, wireType 2 =*/18).string(message.availability_note);
                if (message.bar_count != null && $Object.hasOwnProperty.call(message, "bar_count"))
                    writer.uint32(/* id 3, wireType 0 =*/24).int64(message.bar_count);
                if (message.content_hash != null && $Object.hasOwnProperty.call(message, "content_hash"))
                    writer.uint32(/* id 4, wireType 2 =*/34).string(message.content_hash);
                if (message.exchange != null && $Object.hasOwnProperty.call(message, "exchange"))
                    writer.uint32(/* id 5, wireType 2 =*/42).string(message.exchange);
                if (message.import_spec != null && $Object.hasOwnProperty.call(message, "import_spec"))
                    $root.northstar.data_hub.ImportSpecification.encode(message.import_spec, writer.uint32(/* id 6, wireType 2 =*/50).fork(), _depth + 1).ldelim();
                if (message.limitations != null && message.limitations.length)
                    for (let i = 0; i < message.limitations.length; ++i)
                        writer.uint32(/* id 7, wireType 2 =*/58).string(message.limitations[i]);
                if (message.processing_provenance != null && $Object.hasOwnProperty.call(message, "processing_provenance"))
                    $root.google.protobuf.Struct.encode(message.processing_provenance, writer.uint32(/* id 8, wireType 2 =*/66).fork(), _depth + 1).ldelim();
                if (message.product != null && $Object.hasOwnProperty.call(message, "product"))
                    writer.uint32(/* id 9, wireType 2 =*/74).string(message.product);
                if (message.published_at != null && $Object.hasOwnProperty.call(message, "published_at"))
                    writer.uint32(/* id 10, wireType 2 =*/82).string(message.published_at);
                if (message.quality != null && $Object.hasOwnProperty.call(message, "quality"))
                    $root.google.protobuf.Struct.encode(message.quality, writer.uint32(/* id 11, wireType 2 =*/90).fork(), _depth + 1).ldelim();
                if (message.semantics != null && $Object.hasOwnProperty.call(message, "semantics"))
                    $root.google.protobuf.Struct.encode(message.semantics, writer.uint32(/* id 12, wireType 2 =*/98).fork(), _depth + 1).ldelim();
                if (message.session_close != null && $Object.hasOwnProperty.call(message, "session_close"))
                    writer.uint32(/* id 13, wireType 2 =*/106).string(message.session_close);
                if (message.session_open != null && $Object.hasOwnProperty.call(message, "session_open"))
                    writer.uint32(/* id 14, wireType 2 =*/114).string(message.session_open);
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id"))
                    writer.uint32(/* id 15, wireType 2 =*/122).string(message.snapshot_id);
                if (message.source_reference != null && $Object.hasOwnProperty.call(message, "source_reference"))
                    writer.uint32(/* id 16, wireType 2 =*/130).string(message.source_reference);
                if (message.sources != null && message.sources.length)
                    for (let i = 0; i < message.sources.length; ++i)
                        $root.google.protobuf.Struct.encode(message.sources[i], writer.uint32(/* id 17, wireType 2 =*/138).fork(), _depth + 1).ldelim();
                if (message.symbol != null && $Object.hasOwnProperty.call(message, "symbol"))
                    writer.uint32(/* id 18, wireType 2 =*/146).string(message.symbol);
                if (message.trading_day != null && $Object.hasOwnProperty.call(message, "trading_day"))
                    writer.uint32(/* id 19, wireType 2 =*/154).string(message.trading_day);
                if (message.null_fields != null && message.null_fields.length)
                    for (let i = 0; i < message.null_fields.length; ++i)
                        writer.uint32(/* id 2046, wireType 2 =*/16370).string(message.null_fields[i]);
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a DatasetDetails message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.DatasetDetails
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.DatasetDetails & northstar.data_hub.DatasetDetails.$Shape} DatasetDetails
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            DatasetDetails.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.DatasetDetails();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.availability_basis = reader.stringVerify();
                            message._availability_basis = "availability_basis";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 2)
                                break;
                            message.availability_note = reader.stringVerify();
                            message._availability_note = "availability_note";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 0)
                                break;
                            message.bar_count = reader.int64();
                            message._bar_count = "bar_count";
                            continue;
                        }
                    case 4: {
                            if (wireType !== 2)
                                break;
                            message.content_hash = reader.stringVerify();
                            message._content_hash = "content_hash";
                            continue;
                        }
                    case 5: {
                            if (wireType !== 2)
                                break;
                            message.exchange = reader.stringVerify();
                            message._exchange = "exchange";
                            continue;
                        }
                    case 6: {
                            if (wireType !== 2)
                                break;
                            message.import_spec = $root.northstar.data_hub.ImportSpecification.decode(reader, reader.uint32(), $undefined, _depth + 1, message.import_spec);
                            message._import_spec = "import_spec";
                            continue;
                        }
                    case 7: {
                            if (wireType !== 2)
                                break;
                            if (!(message.limitations && message.limitations.length))
                                message.limitations = [];
                            message.limitations.push(reader.stringVerify());
                            continue;
                        }
                    case 8: {
                            if (wireType !== 2)
                                break;
                            message.processing_provenance = $root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1, message.processing_provenance);
                            message._processing_provenance = "processing_provenance";
                            continue;
                        }
                    case 9: {
                            if (wireType !== 2)
                                break;
                            message.product = reader.stringVerify();
                            message._product = "product";
                            continue;
                        }
                    case 10: {
                            if (wireType !== 2)
                                break;
                            message.published_at = reader.stringVerify();
                            message._published_at = "published_at";
                            continue;
                        }
                    case 11: {
                            if (wireType !== 2)
                                break;
                            message.quality = $root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1, message.quality);
                            message._quality = "quality";
                            continue;
                        }
                    case 12: {
                            if (wireType !== 2)
                                break;
                            message.semantics = $root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1, message.semantics);
                            message._semantics = "semantics";
                            continue;
                        }
                    case 13: {
                            if (wireType !== 2)
                                break;
                            message.session_close = reader.stringVerify();
                            message._session_close = "session_close";
                            continue;
                        }
                    case 14: {
                            if (wireType !== 2)
                                break;
                            message.session_open = reader.stringVerify();
                            message._session_open = "session_open";
                            continue;
                        }
                    case 15: {
                            if (wireType !== 2)
                                break;
                            message.snapshot_id = reader.stringVerify();
                            message._snapshot_id = "snapshot_id";
                            continue;
                        }
                    case 16: {
                            if (wireType !== 2)
                                break;
                            message.source_reference = reader.stringVerify();
                            message._source_reference = "source_reference";
                            continue;
                        }
                    case 17: {
                            if (wireType !== 2)
                                break;
                            if (!(message.sources && message.sources.length))
                                message.sources = [];
                            message.sources.push($root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1));
                            continue;
                        }
                    case 18: {
                            if (wireType !== 2)
                                break;
                            message.symbol = reader.stringVerify();
                            message._symbol = "symbol";
                            continue;
                        }
                    case 19: {
                            if (wireType !== 2)
                                break;
                            message.trading_day = reader.stringVerify();
                            message._trading_day = "trading_day";
                            continue;
                        }
                    case 2046: {
                            if (wireType !== 2)
                                break;
                            if (!(message.null_fields && message.null_fields.length))
                                message.null_fields = [];
                            message.null_fields.push(reader.stringVerify());
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a DatasetDetails message.
             * @function verify
             * @memberof northstar.data_hub.DatasetDetails
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            DatasetDetails.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.availability_basis != null && $Object.hasOwnProperty.call(message, "availability_basis")) {
                    properties._availability_basis = 1;
                    if (!$util.isString(message.availability_basis))
                        return "availability_basis: string expected";
                }
                if (message.availability_note != null && $Object.hasOwnProperty.call(message, "availability_note")) {
                    properties._availability_note = 1;
                    if (!$util.isString(message.availability_note))
                        return "availability_note: string expected";
                }
                if (message.bar_count != null && $Object.hasOwnProperty.call(message, "bar_count")) {
                    properties._bar_count = 1;
                    if (!$util.isInteger(message.bar_count) && !(message.bar_count && $util.isInteger(message.bar_count.low) && $util.isInteger(message.bar_count.high)))
                        return "bar_count: integer|Long expected";
                }
                if (message.content_hash != null && $Object.hasOwnProperty.call(message, "content_hash")) {
                    properties._content_hash = 1;
                    if (!$util.isString(message.content_hash))
                        return "content_hash: string expected";
                }
                if (message.exchange != null && $Object.hasOwnProperty.call(message, "exchange")) {
                    properties._exchange = 1;
                    if (!$util.isString(message.exchange))
                        return "exchange: string expected";
                }
                if (message.import_spec != null && $Object.hasOwnProperty.call(message, "import_spec")) {
                    properties._import_spec = 1;
                    {
                        let error = $root.northstar.data_hub.ImportSpecification.verify(message.import_spec, _depth + 1);
                        if (error)
                            return "import_spec." + error;
                    }
                }
                if (message.limitations != null && $Object.hasOwnProperty.call(message, "limitations")) {
                    if (!$Array.isArray(message.limitations))
                        return "limitations: array expected";
                    for (let i = 0; i < message.limitations.length; ++i)
                        if (!$util.isString(message.limitations[i]))
                            return "limitations: string[] expected";
                }
                if (message.processing_provenance != null && $Object.hasOwnProperty.call(message, "processing_provenance")) {
                    properties._processing_provenance = 1;
                    {
                        let error = $root.google.protobuf.Struct.verify(message.processing_provenance, _depth + 1);
                        if (error)
                            return "processing_provenance." + error;
                    }
                }
                if (message.product != null && $Object.hasOwnProperty.call(message, "product")) {
                    properties._product = 1;
                    if (!$util.isString(message.product))
                        return "product: string expected";
                }
                if (message.published_at != null && $Object.hasOwnProperty.call(message, "published_at")) {
                    properties._published_at = 1;
                    if (!$util.isString(message.published_at))
                        return "published_at: string expected";
                }
                if (message.quality != null && $Object.hasOwnProperty.call(message, "quality")) {
                    properties._quality = 1;
                    {
                        let error = $root.google.protobuf.Struct.verify(message.quality, _depth + 1);
                        if (error)
                            return "quality." + error;
                    }
                }
                if (message.semantics != null && $Object.hasOwnProperty.call(message, "semantics")) {
                    properties._semantics = 1;
                    {
                        let error = $root.google.protobuf.Struct.verify(message.semantics, _depth + 1);
                        if (error)
                            return "semantics." + error;
                    }
                }
                if (message.session_close != null && $Object.hasOwnProperty.call(message, "session_close")) {
                    properties._session_close = 1;
                    if (!$util.isString(message.session_close))
                        return "session_close: string expected";
                }
                if (message.session_open != null && $Object.hasOwnProperty.call(message, "session_open")) {
                    properties._session_open = 1;
                    if (!$util.isString(message.session_open))
                        return "session_open: string expected";
                }
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id")) {
                    properties._snapshot_id = 1;
                    if (!$util.isString(message.snapshot_id))
                        return "snapshot_id: string expected";
                }
                if (message.source_reference != null && $Object.hasOwnProperty.call(message, "source_reference")) {
                    properties._source_reference = 1;
                    if (!$util.isString(message.source_reference))
                        return "source_reference: string expected";
                }
                if (message.sources != null && $Object.hasOwnProperty.call(message, "sources")) {
                    if (!$Array.isArray(message.sources))
                        return "sources: array expected";
                    for (let i = 0; i < message.sources.length; ++i) {
                        let error = $root.google.protobuf.Struct.verify(message.sources[i], _depth + 1);
                        if (error)
                            return "sources." + error;
                    }
                }
                if (message.symbol != null && $Object.hasOwnProperty.call(message, "symbol")) {
                    properties._symbol = 1;
                    if (!$util.isString(message.symbol))
                        return "symbol: string expected";
                }
                if (message.trading_day != null && $Object.hasOwnProperty.call(message, "trading_day")) {
                    properties._trading_day = 1;
                    if (!$util.isString(message.trading_day))
                        return "trading_day: string expected";
                }
                if (message.null_fields != null && $Object.hasOwnProperty.call(message, "null_fields")) {
                    if (!$Array.isArray(message.null_fields))
                        return "null_fields: array expected";
                    for (let i = 0; i < message.null_fields.length; ++i)
                        if (!$util.isString(message.null_fields[i]))
                            return "null_fields: string[] expected";
                }
                return null;
            };

            /**
             * Creates a DatasetDetails message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.DatasetDetails
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.DatasetDetails} DatasetDetails
             */
            DatasetDetails.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.DatasetDetails)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.DatasetDetails: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.DatasetDetails();
                if (object.availability_basis != null)
                    message.availability_basis = $String(object.availability_basis);
                if (object.availability_note != null)
                    message.availability_note = $String(object.availability_note);
                if (object.bar_count != null)
                    if ($util.Long)
                        message.bar_count = $util.Long.fromValue(object.bar_count, false);
                    else if (typeof object.bar_count === "string")
                        message.bar_count = $parseInt(object.bar_count, 10);
                    else if (typeof object.bar_count === "number")
                        message.bar_count = object.bar_count;
                    else if (typeof object.bar_count === "object")
                        message.bar_count = new $util.LongBits(object.bar_count.low >>> 0, object.bar_count.high >>> 0).toNumber();
                if (object.content_hash != null)
                    message.content_hash = $String(object.content_hash);
                if (object.exchange != null)
                    message.exchange = $String(object.exchange);
                if (object.import_spec != null) {
                    if (!$util.isObject(object.import_spec))
                        throw $TypeError(".northstar.data_hub.DatasetDetails.import_spec: object expected");
                    message.import_spec = $root.northstar.data_hub.ImportSpecification.fromObject(object.import_spec, _depth + 1);
                }
                if (object.limitations) {
                    if (!$Array.isArray(object.limitations))
                        throw $TypeError(".northstar.data_hub.DatasetDetails.limitations: array expected");
                    message.limitations = $Array(object.limitations.length);
                    for (let i = 0; i < object.limitations.length; ++i)
                        message.limitations[i] = $String(object.limitations[i]);
                }
                if (object.processing_provenance != null) {
                    if (!$util.isObject(object.processing_provenance))
                        throw $TypeError(".northstar.data_hub.DatasetDetails.processing_provenance: object expected");
                    message.processing_provenance = $root.google.protobuf.Struct.fromObject(object.processing_provenance, _depth + 1);
                }
                if (object.product != null)
                    message.product = $String(object.product);
                if (object.published_at != null)
                    message.published_at = $String(object.published_at);
                if (object.quality != null) {
                    if (!$util.isObject(object.quality))
                        throw $TypeError(".northstar.data_hub.DatasetDetails.quality: object expected");
                    message.quality = $root.google.protobuf.Struct.fromObject(object.quality, _depth + 1);
                }
                if (object.semantics != null) {
                    if (!$util.isObject(object.semantics))
                        throw $TypeError(".northstar.data_hub.DatasetDetails.semantics: object expected");
                    message.semantics = $root.google.protobuf.Struct.fromObject(object.semantics, _depth + 1);
                }
                if (object.session_close != null)
                    message.session_close = $String(object.session_close);
                if (object.session_open != null)
                    message.session_open = $String(object.session_open);
                if (object.snapshot_id != null)
                    message.snapshot_id = $String(object.snapshot_id);
                if (object.source_reference != null)
                    message.source_reference = $String(object.source_reference);
                if (object.sources) {
                    if (!$Array.isArray(object.sources))
                        throw $TypeError(".northstar.data_hub.DatasetDetails.sources: array expected");
                    message.sources = $Array(object.sources.length);
                    for (let i = 0; i < object.sources.length; ++i) {
                        if (!$util.isObject(object.sources[i]))
                            throw $TypeError(".northstar.data_hub.DatasetDetails.sources: object expected");
                        message.sources[i] = $root.google.protobuf.Struct.fromObject(object.sources[i], _depth + 1);
                    }
                }
                if (object.symbol != null)
                    message.symbol = $String(object.symbol);
                if (object.trading_day != null)
                    message.trading_day = $String(object.trading_day);
                if (object.null_fields) {
                    if (!$Array.isArray(object.null_fields))
                        throw $TypeError(".northstar.data_hub.DatasetDetails.null_fields: array expected");
                    message.null_fields = $Array(object.null_fields.length);
                    for (let i = 0; i < object.null_fields.length; ++i)
                        message.null_fields[i] = $String(object.null_fields[i]);
                }
                return message;
            };

            /**
             * Creates a plain object from a DatasetDetails message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.DatasetDetails
             * @static
             * @param {northstar.data_hub.DatasetDetails} message DatasetDetails
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            DatasetDetails.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults) {
                    object.limitations = [];
                    object.sources = [];
                    object.null_fields = [];
                }
                if (message.availability_basis != null && $Object.hasOwnProperty.call(message, "availability_basis")) {
                    object.availability_basis = message.availability_basis;
                    if (options.oneofs)
                        object._availability_basis = "availability_basis";
                }
                if (message.availability_note != null && $Object.hasOwnProperty.call(message, "availability_note")) {
                    object.availability_note = message.availability_note;
                    if (options.oneofs)
                        object._availability_note = "availability_note";
                }
                if (message.bar_count != null && $Object.hasOwnProperty.call(message, "bar_count")) {
                    if (typeof $BigInt !== "undefined" && options.longs === $BigInt)
                        object.bar_count = typeof message.bar_count === "number" ? $BigInt(message.bar_count) : $util.Long.fromBits(message.bar_count.low >>> 0, message.bar_count.high >>> 0, false).toBigInt();
                    else if (typeof message.bar_count === "number")
                        object.bar_count = options.longs === $String ? $String(message.bar_count) : message.bar_count;
                    else
                        object.bar_count = options.longs === $String ? $util.Long.prototype.toString.call(message.bar_count) : options.longs === $Number ? new $util.LongBits(message.bar_count.low >>> 0, message.bar_count.high >>> 0).toNumber() : message.bar_count;
                    if (options.oneofs)
                        object._bar_count = "bar_count";
                }
                if (message.content_hash != null && $Object.hasOwnProperty.call(message, "content_hash")) {
                    object.content_hash = message.content_hash;
                    if (options.oneofs)
                        object._content_hash = "content_hash";
                }
                if (message.exchange != null && $Object.hasOwnProperty.call(message, "exchange")) {
                    object.exchange = message.exchange;
                    if (options.oneofs)
                        object._exchange = "exchange";
                }
                if (message.import_spec != null && $Object.hasOwnProperty.call(message, "import_spec")) {
                    object.import_spec = $root.northstar.data_hub.ImportSpecification.toObject(message.import_spec, options, _depth + 1);
                    if (options.oneofs)
                        object._import_spec = "import_spec";
                }
                if (message.limitations && message.limitations.length) {
                    object.limitations = $Array(message.limitations.length);
                    for (let j = 0; j < message.limitations.length; ++j)
                        object.limitations[j] = message.limitations[j];
                }
                if (message.processing_provenance != null && $Object.hasOwnProperty.call(message, "processing_provenance")) {
                    object.processing_provenance = $root.google.protobuf.Struct.toObject(message.processing_provenance, options, _depth + 1);
                    if (options.oneofs)
                        object._processing_provenance = "processing_provenance";
                }
                if (message.product != null && $Object.hasOwnProperty.call(message, "product")) {
                    object.product = message.product;
                    if (options.oneofs)
                        object._product = "product";
                }
                if (message.published_at != null && $Object.hasOwnProperty.call(message, "published_at")) {
                    object.published_at = message.published_at;
                    if (options.oneofs)
                        object._published_at = "published_at";
                }
                if (message.quality != null && $Object.hasOwnProperty.call(message, "quality")) {
                    object.quality = $root.google.protobuf.Struct.toObject(message.quality, options, _depth + 1);
                    if (options.oneofs)
                        object._quality = "quality";
                }
                if (message.semantics != null && $Object.hasOwnProperty.call(message, "semantics")) {
                    object.semantics = $root.google.protobuf.Struct.toObject(message.semantics, options, _depth + 1);
                    if (options.oneofs)
                        object._semantics = "semantics";
                }
                if (message.session_close != null && $Object.hasOwnProperty.call(message, "session_close")) {
                    object.session_close = message.session_close;
                    if (options.oneofs)
                        object._session_close = "session_close";
                }
                if (message.session_open != null && $Object.hasOwnProperty.call(message, "session_open")) {
                    object.session_open = message.session_open;
                    if (options.oneofs)
                        object._session_open = "session_open";
                }
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id")) {
                    object.snapshot_id = message.snapshot_id;
                    if (options.oneofs)
                        object._snapshot_id = "snapshot_id";
                }
                if (message.source_reference != null && $Object.hasOwnProperty.call(message, "source_reference")) {
                    object.source_reference = message.source_reference;
                    if (options.oneofs)
                        object._source_reference = "source_reference";
                }
                if (message.sources && message.sources.length) {
                    object.sources = $Array(message.sources.length);
                    for (let j = 0; j < message.sources.length; ++j)
                        object.sources[j] = $root.google.protobuf.Struct.toObject(message.sources[j], options, _depth + 1);
                }
                if (message.symbol != null && $Object.hasOwnProperty.call(message, "symbol")) {
                    object.symbol = message.symbol;
                    if (options.oneofs)
                        object._symbol = "symbol";
                }
                if (message.trading_day != null && $Object.hasOwnProperty.call(message, "trading_day")) {
                    object.trading_day = message.trading_day;
                    if (options.oneofs)
                        object._trading_day = "trading_day";
                }
                if (message.null_fields && message.null_fields.length) {
                    object.null_fields = $Array(message.null_fields.length);
                    for (let j = 0; j < message.null_fields.length; ++j)
                        object.null_fields[j] = message.null_fields[j];
                }
                return object;
            };

            /**
             * Converts this DatasetDetails to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.DatasetDetails
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            DatasetDetails.prototype.toJSON = function() {
                return DatasetDetails.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for DatasetDetails
             * @function getTypeUrl
             * @memberof northstar.data_hub.DatasetDetails
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            DatasetDetails.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.DatasetDetails";
            };

            return DatasetDetails;
        })();

        data_hub.DatasetLineage = (function() {

            /**
             * Properties of a DatasetLineage.
             * @typedef {Object} northstar.data_hub.DatasetLineage.$Properties
             * @property {Array.<google.protobuf.Struct.$Properties>|null} [attempts] DatasetLineage attempts
             * @property {string|null} [snapshot_id] DatasetLineage snapshot_id
             * @property {Array.<google.protobuf.Struct.$Properties>|null} [sources] DatasetLineage sources
             * @property {Array.<google.protobuf.Struct.$Properties>|null} [usages] DatasetLineage usages
             * @property {"snapshot_id"} [_snapshot_id] DatasetLineage _snapshot_id
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a DatasetLineage.
             * @memberof northstar.data_hub
             * @interface IDatasetLineage
             * @augments northstar.data_hub.DatasetLineage.$Properties
             * @deprecated Use northstar.data_hub.DatasetLineage.$Properties instead.
             */

            /**
             * Narrowed shape of a DatasetLineage.
             * @typedef {{
             *   attempts?: Array.<google.protobuf.Struct.$Shape>|null;
             *   snapshot_id?: string|null;
             *   sources?: Array.<google.protobuf.Struct.$Shape>|null;
             *   usages?: Array.<google.protobuf.Struct.$Shape>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _snapshot_id?: undefined; snapshot_id?: null }|{ _snapshot_id?: "snapshot_id"; snapshot_id: string })
             * )} northstar.data_hub.DatasetLineage.$Shape
             */

            /**
             * Constructs a new DatasetLineage.
             * @memberof northstar.data_hub
             * @classdesc Represents a DatasetLineage.
             * @constructor
             * @param {northstar.data_hub.DatasetLineage.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const DatasetLineage = function (properties) {
                this.attempts = [];
                this.sources = [];
                this.usages = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * DatasetLineage attempts.
             * @member {Array.<google.protobuf.Struct.$Properties>} attempts
             * @memberof northstar.data_hub.DatasetLineage
             * @instance
             */
            DatasetLineage.prototype.attempts = $util.emptyArray;

            /**
             * DatasetLineage snapshot_id.
             * @member {string|null|undefined} snapshot_id
             * @memberof northstar.data_hub.DatasetLineage
             * @instance
             */
            DatasetLineage.prototype.snapshot_id = null;

            /**
             * DatasetLineage sources.
             * @member {Array.<google.protobuf.Struct.$Properties>} sources
             * @memberof northstar.data_hub.DatasetLineage
             * @instance
             */
            DatasetLineage.prototype.sources = $util.emptyArray;

            /**
             * DatasetLineage usages.
             * @member {Array.<google.protobuf.Struct.$Properties>} usages
             * @memberof northstar.data_hub.DatasetLineage
             * @instance
             */
            DatasetLineage.prototype.usages = $util.emptyArray;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * DatasetLineage _snapshot_id.
             * @member {"snapshot_id"|undefined} _snapshot_id
             * @memberof northstar.data_hub.DatasetLineage
             * @instance
             */
            $Object.defineProperty(DatasetLineage.prototype, "_snapshot_id", {
                get: $util.oneOfGetter($oneOfFields = ["snapshot_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new DatasetLineage instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.DatasetLineage
             * @static
             * @param {northstar.data_hub.DatasetLineage.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.DatasetLineage} DatasetLineage instance
             * @type {{
             *   (properties: northstar.data_hub.DatasetLineage.$Shape): northstar.data_hub.DatasetLineage & northstar.data_hub.DatasetLineage.$Shape;
             *   (properties?: northstar.data_hub.DatasetLineage.$Properties): northstar.data_hub.DatasetLineage;
             * }}
             */
            DatasetLineage.create = function(properties) {
                return new DatasetLineage(properties);
            };

            /**
             * Encodes the specified DatasetLineage message. Does not implicitly {@link northstar.data_hub.DatasetLineage.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.DatasetLineage
             * @static
             * @param {northstar.data_hub.DatasetLineage.$Properties} message DatasetLineage message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            DatasetLineage.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.attempts != null && message.attempts.length)
                    for (let i = 0; i < message.attempts.length; ++i)
                        $root.google.protobuf.Struct.encode(message.attempts[i], writer.uint32(/* id 1, wireType 2 =*/10).fork(), _depth + 1).ldelim();
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id"))
                    writer.uint32(/* id 2, wireType 2 =*/18).string(message.snapshot_id);
                if (message.sources != null && message.sources.length)
                    for (let i = 0; i < message.sources.length; ++i)
                        $root.google.protobuf.Struct.encode(message.sources[i], writer.uint32(/* id 3, wireType 2 =*/26).fork(), _depth + 1).ldelim();
                if (message.usages != null && message.usages.length)
                    for (let i = 0; i < message.usages.length; ++i)
                        $root.google.protobuf.Struct.encode(message.usages[i], writer.uint32(/* id 4, wireType 2 =*/34).fork(), _depth + 1).ldelim();
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a DatasetLineage message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.DatasetLineage
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.DatasetLineage & northstar.data_hub.DatasetLineage.$Shape} DatasetLineage
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            DatasetLineage.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.DatasetLineage();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            if (!(message.attempts && message.attempts.length))
                                message.attempts = [];
                            message.attempts.push($root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1));
                            continue;
                        }
                    case 2: {
                            if (wireType !== 2)
                                break;
                            message.snapshot_id = reader.stringVerify();
                            message._snapshot_id = "snapshot_id";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 2)
                                break;
                            if (!(message.sources && message.sources.length))
                                message.sources = [];
                            message.sources.push($root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1));
                            continue;
                        }
                    case 4: {
                            if (wireType !== 2)
                                break;
                            if (!(message.usages && message.usages.length))
                                message.usages = [];
                            message.usages.push($root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1));
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a DatasetLineage message.
             * @function verify
             * @memberof northstar.data_hub.DatasetLineage
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            DatasetLineage.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.attempts != null && $Object.hasOwnProperty.call(message, "attempts")) {
                    if (!$Array.isArray(message.attempts))
                        return "attempts: array expected";
                    for (let i = 0; i < message.attempts.length; ++i) {
                        let error = $root.google.protobuf.Struct.verify(message.attempts[i], _depth + 1);
                        if (error)
                            return "attempts." + error;
                    }
                }
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id")) {
                    properties._snapshot_id = 1;
                    if (!$util.isString(message.snapshot_id))
                        return "snapshot_id: string expected";
                }
                if (message.sources != null && $Object.hasOwnProperty.call(message, "sources")) {
                    if (!$Array.isArray(message.sources))
                        return "sources: array expected";
                    for (let i = 0; i < message.sources.length; ++i) {
                        let error = $root.google.protobuf.Struct.verify(message.sources[i], _depth + 1);
                        if (error)
                            return "sources." + error;
                    }
                }
                if (message.usages != null && $Object.hasOwnProperty.call(message, "usages")) {
                    if (!$Array.isArray(message.usages))
                        return "usages: array expected";
                    for (let i = 0; i < message.usages.length; ++i) {
                        let error = $root.google.protobuf.Struct.verify(message.usages[i], _depth + 1);
                        if (error)
                            return "usages." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a DatasetLineage message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.DatasetLineage
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.DatasetLineage} DatasetLineage
             */
            DatasetLineage.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.DatasetLineage)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.DatasetLineage: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.DatasetLineage();
                if (object.attempts) {
                    if (!$Array.isArray(object.attempts))
                        throw $TypeError(".northstar.data_hub.DatasetLineage.attempts: array expected");
                    message.attempts = $Array(object.attempts.length);
                    for (let i = 0; i < object.attempts.length; ++i) {
                        if (!$util.isObject(object.attempts[i]))
                            throw $TypeError(".northstar.data_hub.DatasetLineage.attempts: object expected");
                        message.attempts[i] = $root.google.protobuf.Struct.fromObject(object.attempts[i], _depth + 1);
                    }
                }
                if (object.snapshot_id != null)
                    message.snapshot_id = $String(object.snapshot_id);
                if (object.sources) {
                    if (!$Array.isArray(object.sources))
                        throw $TypeError(".northstar.data_hub.DatasetLineage.sources: array expected");
                    message.sources = $Array(object.sources.length);
                    for (let i = 0; i < object.sources.length; ++i) {
                        if (!$util.isObject(object.sources[i]))
                            throw $TypeError(".northstar.data_hub.DatasetLineage.sources: object expected");
                        message.sources[i] = $root.google.protobuf.Struct.fromObject(object.sources[i], _depth + 1);
                    }
                }
                if (object.usages) {
                    if (!$Array.isArray(object.usages))
                        throw $TypeError(".northstar.data_hub.DatasetLineage.usages: array expected");
                    message.usages = $Array(object.usages.length);
                    for (let i = 0; i < object.usages.length; ++i) {
                        if (!$util.isObject(object.usages[i]))
                            throw $TypeError(".northstar.data_hub.DatasetLineage.usages: object expected");
                        message.usages[i] = $root.google.protobuf.Struct.fromObject(object.usages[i], _depth + 1);
                    }
                }
                return message;
            };

            /**
             * Creates a plain object from a DatasetLineage message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.DatasetLineage
             * @static
             * @param {northstar.data_hub.DatasetLineage} message DatasetLineage
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            DatasetLineage.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults) {
                    object.attempts = [];
                    object.sources = [];
                    object.usages = [];
                }
                if (message.attempts && message.attempts.length) {
                    object.attempts = $Array(message.attempts.length);
                    for (let j = 0; j < message.attempts.length; ++j)
                        object.attempts[j] = $root.google.protobuf.Struct.toObject(message.attempts[j], options, _depth + 1);
                }
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id")) {
                    object.snapshot_id = message.snapshot_id;
                    if (options.oneofs)
                        object._snapshot_id = "snapshot_id";
                }
                if (message.sources && message.sources.length) {
                    object.sources = $Array(message.sources.length);
                    for (let j = 0; j < message.sources.length; ++j)
                        object.sources[j] = $root.google.protobuf.Struct.toObject(message.sources[j], options, _depth + 1);
                }
                if (message.usages && message.usages.length) {
                    object.usages = $Array(message.usages.length);
                    for (let j = 0; j < message.usages.length; ++j)
                        object.usages[j] = $root.google.protobuf.Struct.toObject(message.usages[j], options, _depth + 1);
                }
                return object;
            };

            /**
             * Converts this DatasetLineage to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.DatasetLineage
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            DatasetLineage.prototype.toJSON = function() {
                return DatasetLineage.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for DatasetLineage
             * @function getTypeUrl
             * @memberof northstar.data_hub.DatasetLineage
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            DatasetLineage.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.DatasetLineage";
            };

            return DatasetLineage;
        })();

        data_hub.DatasetSummary = (function() {

            /**
             * Properties of a DatasetSummary.
             * @typedef {Object} northstar.data_hub.DatasetSummary.$Properties
             * @property {number|Long|null} [bar_count] DatasetSummary bar_count
             * @property {string|null} [content_hash] DatasetSummary content_hash
             * @property {string|null} [exchange] DatasetSummary exchange
             * @property {string|null} [product] DatasetSummary product
             * @property {string|null} [published_at] DatasetSummary published_at
             * @property {string|null} [session_close] DatasetSummary session_close
             * @property {string|null} [session_open] DatasetSummary session_open
             * @property {string|null} [snapshot_id] DatasetSummary snapshot_id
             * @property {string|null} [symbol] DatasetSummary symbol
             * @property {string|null} [trading_day] DatasetSummary trading_day
             * @property {"bar_count"} [_bar_count] DatasetSummary _bar_count
             * @property {"content_hash"} [_content_hash] DatasetSummary _content_hash
             * @property {"exchange"} [_exchange] DatasetSummary _exchange
             * @property {"product"} [_product] DatasetSummary _product
             * @property {"published_at"} [_published_at] DatasetSummary _published_at
             * @property {"session_close"} [_session_close] DatasetSummary _session_close
             * @property {"session_open"} [_session_open] DatasetSummary _session_open
             * @property {"snapshot_id"} [_snapshot_id] DatasetSummary _snapshot_id
             * @property {"symbol"} [_symbol] DatasetSummary _symbol
             * @property {"trading_day"} [_trading_day] DatasetSummary _trading_day
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a DatasetSummary.
             * @memberof northstar.data_hub
             * @interface IDatasetSummary
             * @augments northstar.data_hub.DatasetSummary.$Properties
             * @deprecated Use northstar.data_hub.DatasetSummary.$Properties instead.
             */

            /**
             * Narrowed shape of a DatasetSummary.
             * @typedef {{
             *   bar_count?: number|Long|null;
             *   content_hash?: string|null;
             *   exchange?: string|null;
             *   product?: string|null;
             *   published_at?: string|null;
             *   session_close?: string|null;
             *   session_open?: string|null;
             *   snapshot_id?: string|null;
             *   symbol?: string|null;
             *   trading_day?: string|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _bar_count?: undefined; bar_count?: null }|{ _bar_count?: "bar_count"; bar_count: number|Long })
             * ) & (
             *   ({ _content_hash?: undefined; content_hash?: null }|{ _content_hash?: "content_hash"; content_hash: string })
             * ) & (
             *   ({ _exchange?: undefined; exchange?: null }|{ _exchange?: "exchange"; exchange: string })
             * ) & (
             *   ({ _product?: undefined; product?: null }|{ _product?: "product"; product: string })
             * ) & (
             *   ({ _published_at?: undefined; published_at?: null }|{ _published_at?: "published_at"; published_at: string })
             * ) & (
             *   ({ _session_close?: undefined; session_close?: null }|{ _session_close?: "session_close"; session_close: string })
             * ) & (
             *   ({ _session_open?: undefined; session_open?: null }|{ _session_open?: "session_open"; session_open: string })
             * ) & (
             *   ({ _snapshot_id?: undefined; snapshot_id?: null }|{ _snapshot_id?: "snapshot_id"; snapshot_id: string })
             * ) & (
             *   ({ _symbol?: undefined; symbol?: null }|{ _symbol?: "symbol"; symbol: string })
             * ) & (
             *   ({ _trading_day?: undefined; trading_day?: null }|{ _trading_day?: "trading_day"; trading_day: string })
             * )} northstar.data_hub.DatasetSummary.$Shape
             */

            /**
             * Constructs a new DatasetSummary.
             * @memberof northstar.data_hub
             * @classdesc Represents a DatasetSummary.
             * @constructor
             * @param {northstar.data_hub.DatasetSummary.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const DatasetSummary = function (properties) {
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * DatasetSummary bar_count.
             * @member {number|Long|null|undefined} bar_count
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            DatasetSummary.prototype.bar_count = null;

            /**
             * DatasetSummary content_hash.
             * @member {string|null|undefined} content_hash
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            DatasetSummary.prototype.content_hash = null;

            /**
             * DatasetSummary exchange.
             * @member {string|null|undefined} exchange
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            DatasetSummary.prototype.exchange = null;

            /**
             * DatasetSummary product.
             * @member {string|null|undefined} product
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            DatasetSummary.prototype.product = null;

            /**
             * DatasetSummary published_at.
             * @member {string|null|undefined} published_at
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            DatasetSummary.prototype.published_at = null;

            /**
             * DatasetSummary session_close.
             * @member {string|null|undefined} session_close
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            DatasetSummary.prototype.session_close = null;

            /**
             * DatasetSummary session_open.
             * @member {string|null|undefined} session_open
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            DatasetSummary.prototype.session_open = null;

            /**
             * DatasetSummary snapshot_id.
             * @member {string|null|undefined} snapshot_id
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            DatasetSummary.prototype.snapshot_id = null;

            /**
             * DatasetSummary symbol.
             * @member {string|null|undefined} symbol
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            DatasetSummary.prototype.symbol = null;

            /**
             * DatasetSummary trading_day.
             * @member {string|null|undefined} trading_day
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            DatasetSummary.prototype.trading_day = null;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * DatasetSummary _bar_count.
             * @member {"bar_count"|undefined} _bar_count
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            $Object.defineProperty(DatasetSummary.prototype, "_bar_count", {
                get: $util.oneOfGetter($oneOfFields = ["bar_count"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetSummary _content_hash.
             * @member {"content_hash"|undefined} _content_hash
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            $Object.defineProperty(DatasetSummary.prototype, "_content_hash", {
                get: $util.oneOfGetter($oneOfFields = ["content_hash"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetSummary _exchange.
             * @member {"exchange"|undefined} _exchange
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            $Object.defineProperty(DatasetSummary.prototype, "_exchange", {
                get: $util.oneOfGetter($oneOfFields = ["exchange"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetSummary _product.
             * @member {"product"|undefined} _product
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            $Object.defineProperty(DatasetSummary.prototype, "_product", {
                get: $util.oneOfGetter($oneOfFields = ["product"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetSummary _published_at.
             * @member {"published_at"|undefined} _published_at
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            $Object.defineProperty(DatasetSummary.prototype, "_published_at", {
                get: $util.oneOfGetter($oneOfFields = ["published_at"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetSummary _session_close.
             * @member {"session_close"|undefined} _session_close
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            $Object.defineProperty(DatasetSummary.prototype, "_session_close", {
                get: $util.oneOfGetter($oneOfFields = ["session_close"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetSummary _session_open.
             * @member {"session_open"|undefined} _session_open
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            $Object.defineProperty(DatasetSummary.prototype, "_session_open", {
                get: $util.oneOfGetter($oneOfFields = ["session_open"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetSummary _snapshot_id.
             * @member {"snapshot_id"|undefined} _snapshot_id
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            $Object.defineProperty(DatasetSummary.prototype, "_snapshot_id", {
                get: $util.oneOfGetter($oneOfFields = ["snapshot_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetSummary _symbol.
             * @member {"symbol"|undefined} _symbol
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            $Object.defineProperty(DatasetSummary.prototype, "_symbol", {
                get: $util.oneOfGetter($oneOfFields = ["symbol"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * DatasetSummary _trading_day.
             * @member {"trading_day"|undefined} _trading_day
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             */
            $Object.defineProperty(DatasetSummary.prototype, "_trading_day", {
                get: $util.oneOfGetter($oneOfFields = ["trading_day"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new DatasetSummary instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.DatasetSummary
             * @static
             * @param {northstar.data_hub.DatasetSummary.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.DatasetSummary} DatasetSummary instance
             * @type {{
             *   (properties: northstar.data_hub.DatasetSummary.$Shape): northstar.data_hub.DatasetSummary & northstar.data_hub.DatasetSummary.$Shape;
             *   (properties?: northstar.data_hub.DatasetSummary.$Properties): northstar.data_hub.DatasetSummary;
             * }}
             */
            DatasetSummary.create = function(properties) {
                return new DatasetSummary(properties);
            };

            /**
             * Encodes the specified DatasetSummary message. Does not implicitly {@link northstar.data_hub.DatasetSummary.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.DatasetSummary
             * @static
             * @param {northstar.data_hub.DatasetSummary.$Properties} message DatasetSummary message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            DatasetSummary.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.bar_count != null && $Object.hasOwnProperty.call(message, "bar_count"))
                    writer.uint32(/* id 1, wireType 0 =*/8).int64(message.bar_count);
                if (message.content_hash != null && $Object.hasOwnProperty.call(message, "content_hash"))
                    writer.uint32(/* id 2, wireType 2 =*/18).string(message.content_hash);
                if (message.exchange != null && $Object.hasOwnProperty.call(message, "exchange"))
                    writer.uint32(/* id 3, wireType 2 =*/26).string(message.exchange);
                if (message.product != null && $Object.hasOwnProperty.call(message, "product"))
                    writer.uint32(/* id 4, wireType 2 =*/34).string(message.product);
                if (message.published_at != null && $Object.hasOwnProperty.call(message, "published_at"))
                    writer.uint32(/* id 5, wireType 2 =*/42).string(message.published_at);
                if (message.session_close != null && $Object.hasOwnProperty.call(message, "session_close"))
                    writer.uint32(/* id 6, wireType 2 =*/50).string(message.session_close);
                if (message.session_open != null && $Object.hasOwnProperty.call(message, "session_open"))
                    writer.uint32(/* id 7, wireType 2 =*/58).string(message.session_open);
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id"))
                    writer.uint32(/* id 8, wireType 2 =*/66).string(message.snapshot_id);
                if (message.symbol != null && $Object.hasOwnProperty.call(message, "symbol"))
                    writer.uint32(/* id 9, wireType 2 =*/74).string(message.symbol);
                if (message.trading_day != null && $Object.hasOwnProperty.call(message, "trading_day"))
                    writer.uint32(/* id 10, wireType 2 =*/82).string(message.trading_day);
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a DatasetSummary message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.DatasetSummary
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.DatasetSummary & northstar.data_hub.DatasetSummary.$Shape} DatasetSummary
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            DatasetSummary.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.DatasetSummary();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 0)
                                break;
                            message.bar_count = reader.int64();
                            message._bar_count = "bar_count";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 2)
                                break;
                            message.content_hash = reader.stringVerify();
                            message._content_hash = "content_hash";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 2)
                                break;
                            message.exchange = reader.stringVerify();
                            message._exchange = "exchange";
                            continue;
                        }
                    case 4: {
                            if (wireType !== 2)
                                break;
                            message.product = reader.stringVerify();
                            message._product = "product";
                            continue;
                        }
                    case 5: {
                            if (wireType !== 2)
                                break;
                            message.published_at = reader.stringVerify();
                            message._published_at = "published_at";
                            continue;
                        }
                    case 6: {
                            if (wireType !== 2)
                                break;
                            message.session_close = reader.stringVerify();
                            message._session_close = "session_close";
                            continue;
                        }
                    case 7: {
                            if (wireType !== 2)
                                break;
                            message.session_open = reader.stringVerify();
                            message._session_open = "session_open";
                            continue;
                        }
                    case 8: {
                            if (wireType !== 2)
                                break;
                            message.snapshot_id = reader.stringVerify();
                            message._snapshot_id = "snapshot_id";
                            continue;
                        }
                    case 9: {
                            if (wireType !== 2)
                                break;
                            message.symbol = reader.stringVerify();
                            message._symbol = "symbol";
                            continue;
                        }
                    case 10: {
                            if (wireType !== 2)
                                break;
                            message.trading_day = reader.stringVerify();
                            message._trading_day = "trading_day";
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a DatasetSummary message.
             * @function verify
             * @memberof northstar.data_hub.DatasetSummary
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            DatasetSummary.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.bar_count != null && $Object.hasOwnProperty.call(message, "bar_count")) {
                    properties._bar_count = 1;
                    if (!$util.isInteger(message.bar_count) && !(message.bar_count && $util.isInteger(message.bar_count.low) && $util.isInteger(message.bar_count.high)))
                        return "bar_count: integer|Long expected";
                }
                if (message.content_hash != null && $Object.hasOwnProperty.call(message, "content_hash")) {
                    properties._content_hash = 1;
                    if (!$util.isString(message.content_hash))
                        return "content_hash: string expected";
                }
                if (message.exchange != null && $Object.hasOwnProperty.call(message, "exchange")) {
                    properties._exchange = 1;
                    if (!$util.isString(message.exchange))
                        return "exchange: string expected";
                }
                if (message.product != null && $Object.hasOwnProperty.call(message, "product")) {
                    properties._product = 1;
                    if (!$util.isString(message.product))
                        return "product: string expected";
                }
                if (message.published_at != null && $Object.hasOwnProperty.call(message, "published_at")) {
                    properties._published_at = 1;
                    if (!$util.isString(message.published_at))
                        return "published_at: string expected";
                }
                if (message.session_close != null && $Object.hasOwnProperty.call(message, "session_close")) {
                    properties._session_close = 1;
                    if (!$util.isString(message.session_close))
                        return "session_close: string expected";
                }
                if (message.session_open != null && $Object.hasOwnProperty.call(message, "session_open")) {
                    properties._session_open = 1;
                    if (!$util.isString(message.session_open))
                        return "session_open: string expected";
                }
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id")) {
                    properties._snapshot_id = 1;
                    if (!$util.isString(message.snapshot_id))
                        return "snapshot_id: string expected";
                }
                if (message.symbol != null && $Object.hasOwnProperty.call(message, "symbol")) {
                    properties._symbol = 1;
                    if (!$util.isString(message.symbol))
                        return "symbol: string expected";
                }
                if (message.trading_day != null && $Object.hasOwnProperty.call(message, "trading_day")) {
                    properties._trading_day = 1;
                    if (!$util.isString(message.trading_day))
                        return "trading_day: string expected";
                }
                return null;
            };

            /**
             * Creates a DatasetSummary message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.DatasetSummary
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.DatasetSummary} DatasetSummary
             */
            DatasetSummary.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.DatasetSummary)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.DatasetSummary: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.DatasetSummary();
                if (object.bar_count != null)
                    if ($util.Long)
                        message.bar_count = $util.Long.fromValue(object.bar_count, false);
                    else if (typeof object.bar_count === "string")
                        message.bar_count = $parseInt(object.bar_count, 10);
                    else if (typeof object.bar_count === "number")
                        message.bar_count = object.bar_count;
                    else if (typeof object.bar_count === "object")
                        message.bar_count = new $util.LongBits(object.bar_count.low >>> 0, object.bar_count.high >>> 0).toNumber();
                if (object.content_hash != null)
                    message.content_hash = $String(object.content_hash);
                if (object.exchange != null)
                    message.exchange = $String(object.exchange);
                if (object.product != null)
                    message.product = $String(object.product);
                if (object.published_at != null)
                    message.published_at = $String(object.published_at);
                if (object.session_close != null)
                    message.session_close = $String(object.session_close);
                if (object.session_open != null)
                    message.session_open = $String(object.session_open);
                if (object.snapshot_id != null)
                    message.snapshot_id = $String(object.snapshot_id);
                if (object.symbol != null)
                    message.symbol = $String(object.symbol);
                if (object.trading_day != null)
                    message.trading_day = $String(object.trading_day);
                return message;
            };

            /**
             * Creates a plain object from a DatasetSummary message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.DatasetSummary
             * @static
             * @param {northstar.data_hub.DatasetSummary} message DatasetSummary
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            DatasetSummary.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (message.bar_count != null && $Object.hasOwnProperty.call(message, "bar_count")) {
                    if (typeof $BigInt !== "undefined" && options.longs === $BigInt)
                        object.bar_count = typeof message.bar_count === "number" ? $BigInt(message.bar_count) : $util.Long.fromBits(message.bar_count.low >>> 0, message.bar_count.high >>> 0, false).toBigInt();
                    else if (typeof message.bar_count === "number")
                        object.bar_count = options.longs === $String ? $String(message.bar_count) : message.bar_count;
                    else
                        object.bar_count = options.longs === $String ? $util.Long.prototype.toString.call(message.bar_count) : options.longs === $Number ? new $util.LongBits(message.bar_count.low >>> 0, message.bar_count.high >>> 0).toNumber() : message.bar_count;
                    if (options.oneofs)
                        object._bar_count = "bar_count";
                }
                if (message.content_hash != null && $Object.hasOwnProperty.call(message, "content_hash")) {
                    object.content_hash = message.content_hash;
                    if (options.oneofs)
                        object._content_hash = "content_hash";
                }
                if (message.exchange != null && $Object.hasOwnProperty.call(message, "exchange")) {
                    object.exchange = message.exchange;
                    if (options.oneofs)
                        object._exchange = "exchange";
                }
                if (message.product != null && $Object.hasOwnProperty.call(message, "product")) {
                    object.product = message.product;
                    if (options.oneofs)
                        object._product = "product";
                }
                if (message.published_at != null && $Object.hasOwnProperty.call(message, "published_at")) {
                    object.published_at = message.published_at;
                    if (options.oneofs)
                        object._published_at = "published_at";
                }
                if (message.session_close != null && $Object.hasOwnProperty.call(message, "session_close")) {
                    object.session_close = message.session_close;
                    if (options.oneofs)
                        object._session_close = "session_close";
                }
                if (message.session_open != null && $Object.hasOwnProperty.call(message, "session_open")) {
                    object.session_open = message.session_open;
                    if (options.oneofs)
                        object._session_open = "session_open";
                }
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id")) {
                    object.snapshot_id = message.snapshot_id;
                    if (options.oneofs)
                        object._snapshot_id = "snapshot_id";
                }
                if (message.symbol != null && $Object.hasOwnProperty.call(message, "symbol")) {
                    object.symbol = message.symbol;
                    if (options.oneofs)
                        object._symbol = "symbol";
                }
                if (message.trading_day != null && $Object.hasOwnProperty.call(message, "trading_day")) {
                    object.trading_day = message.trading_day;
                    if (options.oneofs)
                        object._trading_day = "trading_day";
                }
                return object;
            };

            /**
             * Converts this DatasetSummary to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.DatasetSummary
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            DatasetSummary.prototype.toJSON = function() {
                return DatasetSummary.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for DatasetSummary
             * @function getTypeUrl
             * @memberof northstar.data_hub.DatasetSummary
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            DatasetSummary.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.DatasetSummary";
            };

            return DatasetSummary;
        })();

        data_hub.HttpError = (function() {

            /**
             * Properties of a HttpError.
             * @typedef {Object} northstar.data_hub.HttpError.$Properties
             * @property {string|null} [detail] HttpError detail
             * @property {string|null} [rejection_id] HttpError rejection_id
             * @property {string|null} [request_id] HttpError request_id
             * @property {string|null} [runtime_id] HttpError runtime_id
             * @property {string|null} [status] HttpError status
             * @property {string|null} [url] HttpError url
             * @property {Array.<string>|null} [null_fields] HttpError null_fields
             * @property {"detail"} [_detail] HttpError _detail
             * @property {"rejection_id"} [_rejection_id] HttpError _rejection_id
             * @property {"request_id"} [_request_id] HttpError _request_id
             * @property {"runtime_id"} [_runtime_id] HttpError _runtime_id
             * @property {"status"} [_status] HttpError _status
             * @property {"url"} [_url] HttpError _url
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a HttpError.
             * @memberof northstar.data_hub
             * @interface IHttpError
             * @augments northstar.data_hub.HttpError.$Properties
             * @deprecated Use northstar.data_hub.HttpError.$Properties instead.
             */

            /**
             * Narrowed shape of a HttpError.
             * @typedef {{
             *   detail?: string|null;
             *   rejection_id?: string|null;
             *   request_id?: string|null;
             *   runtime_id?: string|null;
             *   status?: string|null;
             *   url?: string|null;
             *   null_fields?: Array.<string>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _detail?: undefined; detail?: null }|{ _detail?: "detail"; detail: string })
             * ) & (
             *   ({ _rejection_id?: undefined; rejection_id?: null }|{ _rejection_id?: "rejection_id"; rejection_id: string })
             * ) & (
             *   ({ _request_id?: undefined; request_id?: null }|{ _request_id?: "request_id"; request_id: string })
             * ) & (
             *   ({ _runtime_id?: undefined; runtime_id?: null }|{ _runtime_id?: "runtime_id"; runtime_id: string })
             * ) & (
             *   ({ _status?: undefined; status?: null }|{ _status?: "status"; status: string })
             * ) & (
             *   ({ _url?: undefined; url?: null }|{ _url?: "url"; url: string })
             * )} northstar.data_hub.HttpError.$Shape
             */

            /**
             * Constructs a new HttpError.
             * @memberof northstar.data_hub
             * @classdesc Represents a HttpError.
             * @constructor
             * @param {northstar.data_hub.HttpError.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const HttpError = function (properties) {
                this.null_fields = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * HttpError detail.
             * @member {string|null|undefined} detail
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            HttpError.prototype.detail = null;

            /**
             * HttpError rejection_id.
             * @member {string|null|undefined} rejection_id
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            HttpError.prototype.rejection_id = null;

            /**
             * HttpError request_id.
             * @member {string|null|undefined} request_id
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            HttpError.prototype.request_id = null;

            /**
             * HttpError runtime_id.
             * @member {string|null|undefined} runtime_id
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            HttpError.prototype.runtime_id = null;

            /**
             * HttpError status.
             * @member {string|null|undefined} status
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            HttpError.prototype.status = null;

            /**
             * HttpError url.
             * @member {string|null|undefined} url
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            HttpError.prototype.url = null;

            /**
             * HttpError null_fields.
             * @member {Array.<string>} null_fields
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            HttpError.prototype.null_fields = $util.emptyArray;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * HttpError _detail.
             * @member {"detail"|undefined} _detail
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            $Object.defineProperty(HttpError.prototype, "_detail", {
                get: $util.oneOfGetter($oneOfFields = ["detail"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * HttpError _rejection_id.
             * @member {"rejection_id"|undefined} _rejection_id
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            $Object.defineProperty(HttpError.prototype, "_rejection_id", {
                get: $util.oneOfGetter($oneOfFields = ["rejection_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * HttpError _request_id.
             * @member {"request_id"|undefined} _request_id
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            $Object.defineProperty(HttpError.prototype, "_request_id", {
                get: $util.oneOfGetter($oneOfFields = ["request_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * HttpError _runtime_id.
             * @member {"runtime_id"|undefined} _runtime_id
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            $Object.defineProperty(HttpError.prototype, "_runtime_id", {
                get: $util.oneOfGetter($oneOfFields = ["runtime_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * HttpError _status.
             * @member {"status"|undefined} _status
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            $Object.defineProperty(HttpError.prototype, "_status", {
                get: $util.oneOfGetter($oneOfFields = ["status"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * HttpError _url.
             * @member {"url"|undefined} _url
             * @memberof northstar.data_hub.HttpError
             * @instance
             */
            $Object.defineProperty(HttpError.prototype, "_url", {
                get: $util.oneOfGetter($oneOfFields = ["url"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new HttpError instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.HttpError
             * @static
             * @param {northstar.data_hub.HttpError.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.HttpError} HttpError instance
             * @type {{
             *   (properties: northstar.data_hub.HttpError.$Shape): northstar.data_hub.HttpError & northstar.data_hub.HttpError.$Shape;
             *   (properties?: northstar.data_hub.HttpError.$Properties): northstar.data_hub.HttpError;
             * }}
             */
            HttpError.create = function(properties) {
                return new HttpError(properties);
            };

            /**
             * Encodes the specified HttpError message. Does not implicitly {@link northstar.data_hub.HttpError.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.HttpError
             * @static
             * @param {northstar.data_hub.HttpError.$Properties} message HttpError message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            HttpError.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.detail != null && $Object.hasOwnProperty.call(message, "detail"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.detail);
                if (message.rejection_id != null && $Object.hasOwnProperty.call(message, "rejection_id"))
                    writer.uint32(/* id 2, wireType 2 =*/18).string(message.rejection_id);
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id"))
                    writer.uint32(/* id 3, wireType 2 =*/26).string(message.request_id);
                if (message.runtime_id != null && $Object.hasOwnProperty.call(message, "runtime_id"))
                    writer.uint32(/* id 4, wireType 2 =*/34).string(message.runtime_id);
                if (message.status != null && $Object.hasOwnProperty.call(message, "status"))
                    writer.uint32(/* id 5, wireType 2 =*/42).string(message.status);
                if (message.url != null && $Object.hasOwnProperty.call(message, "url"))
                    writer.uint32(/* id 6, wireType 2 =*/50).string(message.url);
                if (message.null_fields != null && message.null_fields.length)
                    for (let i = 0; i < message.null_fields.length; ++i)
                        writer.uint32(/* id 2046, wireType 2 =*/16370).string(message.null_fields[i]);
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a HttpError message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.HttpError
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.HttpError & northstar.data_hub.HttpError.$Shape} HttpError
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            HttpError.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.HttpError();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.detail = reader.stringVerify();
                            message._detail = "detail";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 2)
                                break;
                            message.rejection_id = reader.stringVerify();
                            message._rejection_id = "rejection_id";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 2)
                                break;
                            message.request_id = reader.stringVerify();
                            message._request_id = "request_id";
                            continue;
                        }
                    case 4: {
                            if (wireType !== 2)
                                break;
                            message.runtime_id = reader.stringVerify();
                            message._runtime_id = "runtime_id";
                            continue;
                        }
                    case 5: {
                            if (wireType !== 2)
                                break;
                            message.status = reader.stringVerify();
                            message._status = "status";
                            continue;
                        }
                    case 6: {
                            if (wireType !== 2)
                                break;
                            message.url = reader.stringVerify();
                            message._url = "url";
                            continue;
                        }
                    case 2046: {
                            if (wireType !== 2)
                                break;
                            if (!(message.null_fields && message.null_fields.length))
                                message.null_fields = [];
                            message.null_fields.push(reader.stringVerify());
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a HttpError message.
             * @function verify
             * @memberof northstar.data_hub.HttpError
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            HttpError.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.detail != null && $Object.hasOwnProperty.call(message, "detail")) {
                    properties._detail = 1;
                    if (!$util.isString(message.detail))
                        return "detail: string expected";
                }
                if (message.rejection_id != null && $Object.hasOwnProperty.call(message, "rejection_id")) {
                    properties._rejection_id = 1;
                    if (!$util.isString(message.rejection_id))
                        return "rejection_id: string expected";
                }
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    properties._request_id = 1;
                    if (!$util.isString(message.request_id))
                        return "request_id: string expected";
                }
                if (message.runtime_id != null && $Object.hasOwnProperty.call(message, "runtime_id")) {
                    properties._runtime_id = 1;
                    if (!$util.isString(message.runtime_id))
                        return "runtime_id: string expected";
                }
                if (message.status != null && $Object.hasOwnProperty.call(message, "status")) {
                    properties._status = 1;
                    if (!$util.isString(message.status))
                        return "status: string expected";
                }
                if (message.url != null && $Object.hasOwnProperty.call(message, "url")) {
                    properties._url = 1;
                    if (!$util.isString(message.url))
                        return "url: string expected";
                }
                if (message.null_fields != null && $Object.hasOwnProperty.call(message, "null_fields")) {
                    if (!$Array.isArray(message.null_fields))
                        return "null_fields: array expected";
                    for (let i = 0; i < message.null_fields.length; ++i)
                        if (!$util.isString(message.null_fields[i]))
                            return "null_fields: string[] expected";
                }
                return null;
            };

            /**
             * Creates a HttpError message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.HttpError
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.HttpError} HttpError
             */
            HttpError.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.HttpError)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.HttpError: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.HttpError();
                if (object.detail != null)
                    message.detail = $String(object.detail);
                if (object.rejection_id != null)
                    message.rejection_id = $String(object.rejection_id);
                if (object.request_id != null)
                    message.request_id = $String(object.request_id);
                if (object.runtime_id != null)
                    message.runtime_id = $String(object.runtime_id);
                if (object.status != null)
                    message.status = $String(object.status);
                if (object.url != null)
                    message.url = $String(object.url);
                if (object.null_fields) {
                    if (!$Array.isArray(object.null_fields))
                        throw $TypeError(".northstar.data_hub.HttpError.null_fields: array expected");
                    message.null_fields = $Array(object.null_fields.length);
                    for (let i = 0; i < object.null_fields.length; ++i)
                        message.null_fields[i] = $String(object.null_fields[i]);
                }
                return message;
            };

            /**
             * Creates a plain object from a HttpError message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.HttpError
             * @static
             * @param {northstar.data_hub.HttpError} message HttpError
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            HttpError.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults)
                    object.null_fields = [];
                if (message.detail != null && $Object.hasOwnProperty.call(message, "detail")) {
                    object.detail = message.detail;
                    if (options.oneofs)
                        object._detail = "detail";
                }
                if (message.rejection_id != null && $Object.hasOwnProperty.call(message, "rejection_id")) {
                    object.rejection_id = message.rejection_id;
                    if (options.oneofs)
                        object._rejection_id = "rejection_id";
                }
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    object.request_id = message.request_id;
                    if (options.oneofs)
                        object._request_id = "request_id";
                }
                if (message.runtime_id != null && $Object.hasOwnProperty.call(message, "runtime_id")) {
                    object.runtime_id = message.runtime_id;
                    if (options.oneofs)
                        object._runtime_id = "runtime_id";
                }
                if (message.status != null && $Object.hasOwnProperty.call(message, "status")) {
                    object.status = message.status;
                    if (options.oneofs)
                        object._status = "status";
                }
                if (message.url != null && $Object.hasOwnProperty.call(message, "url")) {
                    object.url = message.url;
                    if (options.oneofs)
                        object._url = "url";
                }
                if (message.null_fields && message.null_fields.length) {
                    object.null_fields = $Array(message.null_fields.length);
                    for (let j = 0; j < message.null_fields.length; ++j)
                        object.null_fields[j] = message.null_fields[j];
                }
                return object;
            };

            /**
             * Converts this HttpError to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.HttpError
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            HttpError.prototype.toJSON = function() {
                return HttpError.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for HttpError
             * @function getTypeUrl
             * @memberof northstar.data_hub.HttpError
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            HttpError.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.HttpError";
            };

            return HttpError;
        })();

        data_hub.ImportRequest = (function() {

            /**
             * Properties of an ImportRequest.
             * @typedef {Object} northstar.data_hub.ImportRequest.$Properties
             * @property {boolean|null} [allow_download] ImportRequest allow_download
             * @property {boolean|null} [allow_retention] ImportRequest allow_retention
             * @property {string|null} [content_base64] ImportRequest content_base64
             * @property {string|null} [filename] ImportRequest filename
             * @property {string|null} [input_kind] ImportRequest input_kind
             * @property {string|null} [request_id] ImportRequest request_id
             * @property {string|null} [source_name] ImportRequest source_name
             * @property {google.protobuf.Struct.$Properties|null} [spec] ImportRequest spec
             * @property {string|null} [transformation_note] ImportRequest transformation_note
             * @property {string|null} [upstream_source_id] ImportRequest upstream_source_id
             * @property {string|null} [use_basis] ImportRequest use_basis
             * @property {Array.<string>|null} [null_fields] ImportRequest null_fields
             * @property {"allow_download"} [_allow_download] ImportRequest _allow_download
             * @property {"allow_retention"} [_allow_retention] ImportRequest _allow_retention
             * @property {"content_base64"} [_content_base64] ImportRequest _content_base64
             * @property {"filename"} [_filename] ImportRequest _filename
             * @property {"input_kind"} [_input_kind] ImportRequest _input_kind
             * @property {"request_id"} [_request_id] ImportRequest _request_id
             * @property {"source_name"} [_source_name] ImportRequest _source_name
             * @property {"spec"} [_spec] ImportRequest _spec
             * @property {"transformation_note"} [_transformation_note] ImportRequest _transformation_note
             * @property {"upstream_source_id"} [_upstream_source_id] ImportRequest _upstream_source_id
             * @property {"use_basis"} [_use_basis] ImportRequest _use_basis
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of an ImportRequest.
             * @memberof northstar.data_hub
             * @interface IImportRequest
             * @augments northstar.data_hub.ImportRequest.$Properties
             * @deprecated Use northstar.data_hub.ImportRequest.$Properties instead.
             */

            /**
             * Narrowed shape of an ImportRequest.
             * @typedef {{
             *   allow_download?: boolean|null;
             *   allow_retention?: boolean|null;
             *   content_base64?: string|null;
             *   filename?: string|null;
             *   input_kind?: string|null;
             *   request_id?: string|null;
             *   source_name?: string|null;
             *   spec?: google.protobuf.Struct.$Shape|null;
             *   transformation_note?: string|null;
             *   upstream_source_id?: string|null;
             *   use_basis?: string|null;
             *   null_fields?: Array.<string>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _allow_download?: undefined; allow_download?: null }|{ _allow_download?: "allow_download"; allow_download: boolean })
             * ) & (
             *   ({ _allow_retention?: undefined; allow_retention?: null }|{ _allow_retention?: "allow_retention"; allow_retention: boolean })
             * ) & (
             *   ({ _content_base64?: undefined; content_base64?: null }|{ _content_base64?: "content_base64"; content_base64: string })
             * ) & (
             *   ({ _filename?: undefined; filename?: null }|{ _filename?: "filename"; filename: string })
             * ) & (
             *   ({ _input_kind?: undefined; input_kind?: null }|{ _input_kind?: "input_kind"; input_kind: string })
             * ) & (
             *   ({ _request_id?: undefined; request_id?: null }|{ _request_id?: "request_id"; request_id: string })
             * ) & (
             *   ({ _source_name?: undefined; source_name?: null }|{ _source_name?: "source_name"; source_name: string })
             * ) & (
             *   ({ _spec?: undefined; spec?: null }|{ _spec?: "spec"; spec: google.protobuf.Struct.$Shape })
             * ) & (
             *   ({ _transformation_note?: undefined; transformation_note?: null }|{ _transformation_note?: "transformation_note"; transformation_note: string })
             * ) & (
             *   ({ _upstream_source_id?: undefined; upstream_source_id?: null }|{ _upstream_source_id?: "upstream_source_id"; upstream_source_id: string })
             * ) & (
             *   ({ _use_basis?: undefined; use_basis?: null }|{ _use_basis?: "use_basis"; use_basis: string })
             * )} northstar.data_hub.ImportRequest.$Shape
             */

            /**
             * Constructs a new ImportRequest.
             * @memberof northstar.data_hub
             * @classdesc Represents an ImportRequest.
             * @constructor
             * @param {northstar.data_hub.ImportRequest.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const ImportRequest = function (properties) {
                this.null_fields = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * ImportRequest allow_download.
             * @member {boolean|null|undefined} allow_download
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.allow_download = null;

            /**
             * ImportRequest allow_retention.
             * @member {boolean|null|undefined} allow_retention
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.allow_retention = null;

            /**
             * ImportRequest content_base64.
             * @member {string|null|undefined} content_base64
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.content_base64 = null;

            /**
             * ImportRequest filename.
             * @member {string|null|undefined} filename
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.filename = null;

            /**
             * ImportRequest input_kind.
             * @member {string|null|undefined} input_kind
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.input_kind = null;

            /**
             * ImportRequest request_id.
             * @member {string|null|undefined} request_id
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.request_id = null;

            /**
             * ImportRequest source_name.
             * @member {string|null|undefined} source_name
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.source_name = null;

            /**
             * ImportRequest spec.
             * @member {google.protobuf.Struct.$Properties|null|undefined} spec
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.spec = null;

            /**
             * ImportRequest transformation_note.
             * @member {string|null|undefined} transformation_note
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.transformation_note = null;

            /**
             * ImportRequest upstream_source_id.
             * @member {string|null|undefined} upstream_source_id
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.upstream_source_id = null;

            /**
             * ImportRequest use_basis.
             * @member {string|null|undefined} use_basis
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.use_basis = null;

            /**
             * ImportRequest null_fields.
             * @member {Array.<string>} null_fields
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            ImportRequest.prototype.null_fields = $util.emptyArray;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * ImportRequest _allow_download.
             * @member {"allow_download"|undefined} _allow_download
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            $Object.defineProperty(ImportRequest.prototype, "_allow_download", {
                get: $util.oneOfGetter($oneOfFields = ["allow_download"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportRequest _allow_retention.
             * @member {"allow_retention"|undefined} _allow_retention
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            $Object.defineProperty(ImportRequest.prototype, "_allow_retention", {
                get: $util.oneOfGetter($oneOfFields = ["allow_retention"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportRequest _content_base64.
             * @member {"content_base64"|undefined} _content_base64
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            $Object.defineProperty(ImportRequest.prototype, "_content_base64", {
                get: $util.oneOfGetter($oneOfFields = ["content_base64"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportRequest _filename.
             * @member {"filename"|undefined} _filename
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            $Object.defineProperty(ImportRequest.prototype, "_filename", {
                get: $util.oneOfGetter($oneOfFields = ["filename"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportRequest _input_kind.
             * @member {"input_kind"|undefined} _input_kind
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            $Object.defineProperty(ImportRequest.prototype, "_input_kind", {
                get: $util.oneOfGetter($oneOfFields = ["input_kind"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportRequest _request_id.
             * @member {"request_id"|undefined} _request_id
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            $Object.defineProperty(ImportRequest.prototype, "_request_id", {
                get: $util.oneOfGetter($oneOfFields = ["request_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportRequest _source_name.
             * @member {"source_name"|undefined} _source_name
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            $Object.defineProperty(ImportRequest.prototype, "_source_name", {
                get: $util.oneOfGetter($oneOfFields = ["source_name"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportRequest _spec.
             * @member {"spec"|undefined} _spec
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            $Object.defineProperty(ImportRequest.prototype, "_spec", {
                get: $util.oneOfGetter($oneOfFields = ["spec"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportRequest _transformation_note.
             * @member {"transformation_note"|undefined} _transformation_note
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            $Object.defineProperty(ImportRequest.prototype, "_transformation_note", {
                get: $util.oneOfGetter($oneOfFields = ["transformation_note"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportRequest _upstream_source_id.
             * @member {"upstream_source_id"|undefined} _upstream_source_id
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            $Object.defineProperty(ImportRequest.prototype, "_upstream_source_id", {
                get: $util.oneOfGetter($oneOfFields = ["upstream_source_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportRequest _use_basis.
             * @member {"use_basis"|undefined} _use_basis
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             */
            $Object.defineProperty(ImportRequest.prototype, "_use_basis", {
                get: $util.oneOfGetter($oneOfFields = ["use_basis"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new ImportRequest instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.ImportRequest
             * @static
             * @param {northstar.data_hub.ImportRequest.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.ImportRequest} ImportRequest instance
             * @type {{
             *   (properties: northstar.data_hub.ImportRequest.$Shape): northstar.data_hub.ImportRequest & northstar.data_hub.ImportRequest.$Shape;
             *   (properties?: northstar.data_hub.ImportRequest.$Properties): northstar.data_hub.ImportRequest;
             * }}
             */
            ImportRequest.create = function(properties) {
                return new ImportRequest(properties);
            };

            /**
             * Encodes the specified ImportRequest message. Does not implicitly {@link northstar.data_hub.ImportRequest.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.ImportRequest
             * @static
             * @param {northstar.data_hub.ImportRequest.$Properties} message ImportRequest message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            ImportRequest.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.allow_download != null && $Object.hasOwnProperty.call(message, "allow_download"))
                    writer.uint32(/* id 1, wireType 0 =*/8).bool(message.allow_download);
                if (message.allow_retention != null && $Object.hasOwnProperty.call(message, "allow_retention"))
                    writer.uint32(/* id 2, wireType 0 =*/16).bool(message.allow_retention);
                if (message.content_base64 != null && $Object.hasOwnProperty.call(message, "content_base64"))
                    writer.uint32(/* id 3, wireType 2 =*/26).string(message.content_base64);
                if (message.filename != null && $Object.hasOwnProperty.call(message, "filename"))
                    writer.uint32(/* id 4, wireType 2 =*/34).string(message.filename);
                if (message.input_kind != null && $Object.hasOwnProperty.call(message, "input_kind"))
                    writer.uint32(/* id 5, wireType 2 =*/42).string(message.input_kind);
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id"))
                    writer.uint32(/* id 6, wireType 2 =*/50).string(message.request_id);
                if (message.source_name != null && $Object.hasOwnProperty.call(message, "source_name"))
                    writer.uint32(/* id 7, wireType 2 =*/58).string(message.source_name);
                if (message.spec != null && $Object.hasOwnProperty.call(message, "spec"))
                    $root.google.protobuf.Struct.encode(message.spec, writer.uint32(/* id 8, wireType 2 =*/66).fork(), _depth + 1).ldelim();
                if (message.transformation_note != null && $Object.hasOwnProperty.call(message, "transformation_note"))
                    writer.uint32(/* id 9, wireType 2 =*/74).string(message.transformation_note);
                if (message.upstream_source_id != null && $Object.hasOwnProperty.call(message, "upstream_source_id"))
                    writer.uint32(/* id 10, wireType 2 =*/82).string(message.upstream_source_id);
                if (message.use_basis != null && $Object.hasOwnProperty.call(message, "use_basis"))
                    writer.uint32(/* id 11, wireType 2 =*/90).string(message.use_basis);
                if (message.null_fields != null && message.null_fields.length)
                    for (let i = 0; i < message.null_fields.length; ++i)
                        writer.uint32(/* id 2046, wireType 2 =*/16370).string(message.null_fields[i]);
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes an ImportRequest message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.ImportRequest
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.ImportRequest & northstar.data_hub.ImportRequest.$Shape} ImportRequest
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            ImportRequest.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.ImportRequest();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 0)
                                break;
                            message.allow_download = reader.bool();
                            message._allow_download = "allow_download";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 0)
                                break;
                            message.allow_retention = reader.bool();
                            message._allow_retention = "allow_retention";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 2)
                                break;
                            message.content_base64 = reader.stringVerify();
                            message._content_base64 = "content_base64";
                            continue;
                        }
                    case 4: {
                            if (wireType !== 2)
                                break;
                            message.filename = reader.stringVerify();
                            message._filename = "filename";
                            continue;
                        }
                    case 5: {
                            if (wireType !== 2)
                                break;
                            message.input_kind = reader.stringVerify();
                            message._input_kind = "input_kind";
                            continue;
                        }
                    case 6: {
                            if (wireType !== 2)
                                break;
                            message.request_id = reader.stringVerify();
                            message._request_id = "request_id";
                            continue;
                        }
                    case 7: {
                            if (wireType !== 2)
                                break;
                            message.source_name = reader.stringVerify();
                            message._source_name = "source_name";
                            continue;
                        }
                    case 8: {
                            if (wireType !== 2)
                                break;
                            message.spec = $root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1, message.spec);
                            message._spec = "spec";
                            continue;
                        }
                    case 9: {
                            if (wireType !== 2)
                                break;
                            message.transformation_note = reader.stringVerify();
                            message._transformation_note = "transformation_note";
                            continue;
                        }
                    case 10: {
                            if (wireType !== 2)
                                break;
                            message.upstream_source_id = reader.stringVerify();
                            message._upstream_source_id = "upstream_source_id";
                            continue;
                        }
                    case 11: {
                            if (wireType !== 2)
                                break;
                            message.use_basis = reader.stringVerify();
                            message._use_basis = "use_basis";
                            continue;
                        }
                    case 2046: {
                            if (wireType !== 2)
                                break;
                            if (!(message.null_fields && message.null_fields.length))
                                message.null_fields = [];
                            message.null_fields.push(reader.stringVerify());
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies an ImportRequest message.
             * @function verify
             * @memberof northstar.data_hub.ImportRequest
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            ImportRequest.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.allow_download != null && $Object.hasOwnProperty.call(message, "allow_download")) {
                    properties._allow_download = 1;
                    if (typeof message.allow_download !== "boolean")
                        return "allow_download: boolean expected";
                }
                if (message.allow_retention != null && $Object.hasOwnProperty.call(message, "allow_retention")) {
                    properties._allow_retention = 1;
                    if (typeof message.allow_retention !== "boolean")
                        return "allow_retention: boolean expected";
                }
                if (message.content_base64 != null && $Object.hasOwnProperty.call(message, "content_base64")) {
                    properties._content_base64 = 1;
                    if (!$util.isString(message.content_base64))
                        return "content_base64: string expected";
                }
                if (message.filename != null && $Object.hasOwnProperty.call(message, "filename")) {
                    properties._filename = 1;
                    if (!$util.isString(message.filename))
                        return "filename: string expected";
                }
                if (message.input_kind != null && $Object.hasOwnProperty.call(message, "input_kind")) {
                    properties._input_kind = 1;
                    if (!$util.isString(message.input_kind))
                        return "input_kind: string expected";
                }
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    properties._request_id = 1;
                    if (!$util.isString(message.request_id))
                        return "request_id: string expected";
                }
                if (message.source_name != null && $Object.hasOwnProperty.call(message, "source_name")) {
                    properties._source_name = 1;
                    if (!$util.isString(message.source_name))
                        return "source_name: string expected";
                }
                if (message.spec != null && $Object.hasOwnProperty.call(message, "spec")) {
                    properties._spec = 1;
                    {
                        let error = $root.google.protobuf.Struct.verify(message.spec, _depth + 1);
                        if (error)
                            return "spec." + error;
                    }
                }
                if (message.transformation_note != null && $Object.hasOwnProperty.call(message, "transformation_note")) {
                    properties._transformation_note = 1;
                    if (!$util.isString(message.transformation_note))
                        return "transformation_note: string expected";
                }
                if (message.upstream_source_id != null && $Object.hasOwnProperty.call(message, "upstream_source_id")) {
                    properties._upstream_source_id = 1;
                    if (!$util.isString(message.upstream_source_id))
                        return "upstream_source_id: string expected";
                }
                if (message.use_basis != null && $Object.hasOwnProperty.call(message, "use_basis")) {
                    properties._use_basis = 1;
                    if (!$util.isString(message.use_basis))
                        return "use_basis: string expected";
                }
                if (message.null_fields != null && $Object.hasOwnProperty.call(message, "null_fields")) {
                    if (!$Array.isArray(message.null_fields))
                        return "null_fields: array expected";
                    for (let i = 0; i < message.null_fields.length; ++i)
                        if (!$util.isString(message.null_fields[i]))
                            return "null_fields: string[] expected";
                }
                return null;
            };

            /**
             * Creates an ImportRequest message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.ImportRequest
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.ImportRequest} ImportRequest
             */
            ImportRequest.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.ImportRequest)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.ImportRequest: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.ImportRequest();
                if (object.allow_download != null)
                    message.allow_download = $Boolean(object.allow_download);
                if (object.allow_retention != null)
                    message.allow_retention = $Boolean(object.allow_retention);
                if (object.content_base64 != null)
                    message.content_base64 = $String(object.content_base64);
                if (object.filename != null)
                    message.filename = $String(object.filename);
                if (object.input_kind != null)
                    message.input_kind = $String(object.input_kind);
                if (object.request_id != null)
                    message.request_id = $String(object.request_id);
                if (object.source_name != null)
                    message.source_name = $String(object.source_name);
                if (object.spec != null) {
                    if (!$util.isObject(object.spec))
                        throw $TypeError(".northstar.data_hub.ImportRequest.spec: object expected");
                    message.spec = $root.google.protobuf.Struct.fromObject(object.spec, _depth + 1);
                }
                if (object.transformation_note != null)
                    message.transformation_note = $String(object.transformation_note);
                if (object.upstream_source_id != null)
                    message.upstream_source_id = $String(object.upstream_source_id);
                if (object.use_basis != null)
                    message.use_basis = $String(object.use_basis);
                if (object.null_fields) {
                    if (!$Array.isArray(object.null_fields))
                        throw $TypeError(".northstar.data_hub.ImportRequest.null_fields: array expected");
                    message.null_fields = $Array(object.null_fields.length);
                    for (let i = 0; i < object.null_fields.length; ++i)
                        message.null_fields[i] = $String(object.null_fields[i]);
                }
                return message;
            };

            /**
             * Creates a plain object from an ImportRequest message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.ImportRequest
             * @static
             * @param {northstar.data_hub.ImportRequest} message ImportRequest
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            ImportRequest.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults)
                    object.null_fields = [];
                if (message.allow_download != null && $Object.hasOwnProperty.call(message, "allow_download")) {
                    object.allow_download = message.allow_download;
                    if (options.oneofs)
                        object._allow_download = "allow_download";
                }
                if (message.allow_retention != null && $Object.hasOwnProperty.call(message, "allow_retention")) {
                    object.allow_retention = message.allow_retention;
                    if (options.oneofs)
                        object._allow_retention = "allow_retention";
                }
                if (message.content_base64 != null && $Object.hasOwnProperty.call(message, "content_base64")) {
                    object.content_base64 = message.content_base64;
                    if (options.oneofs)
                        object._content_base64 = "content_base64";
                }
                if (message.filename != null && $Object.hasOwnProperty.call(message, "filename")) {
                    object.filename = message.filename;
                    if (options.oneofs)
                        object._filename = "filename";
                }
                if (message.input_kind != null && $Object.hasOwnProperty.call(message, "input_kind")) {
                    object.input_kind = message.input_kind;
                    if (options.oneofs)
                        object._input_kind = "input_kind";
                }
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    object.request_id = message.request_id;
                    if (options.oneofs)
                        object._request_id = "request_id";
                }
                if (message.source_name != null && $Object.hasOwnProperty.call(message, "source_name")) {
                    object.source_name = message.source_name;
                    if (options.oneofs)
                        object._source_name = "source_name";
                }
                if (message.spec != null && $Object.hasOwnProperty.call(message, "spec")) {
                    object.spec = $root.google.protobuf.Struct.toObject(message.spec, options, _depth + 1);
                    if (options.oneofs)
                        object._spec = "spec";
                }
                if (message.transformation_note != null && $Object.hasOwnProperty.call(message, "transformation_note")) {
                    object.transformation_note = message.transformation_note;
                    if (options.oneofs)
                        object._transformation_note = "transformation_note";
                }
                if (message.upstream_source_id != null && $Object.hasOwnProperty.call(message, "upstream_source_id")) {
                    object.upstream_source_id = message.upstream_source_id;
                    if (options.oneofs)
                        object._upstream_source_id = "upstream_source_id";
                }
                if (message.use_basis != null && $Object.hasOwnProperty.call(message, "use_basis")) {
                    object.use_basis = message.use_basis;
                    if (options.oneofs)
                        object._use_basis = "use_basis";
                }
                if (message.null_fields && message.null_fields.length) {
                    object.null_fields = $Array(message.null_fields.length);
                    for (let j = 0; j < message.null_fields.length; ++j)
                        object.null_fields[j] = message.null_fields[j];
                }
                return object;
            };

            /**
             * Converts this ImportRequest to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.ImportRequest
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            ImportRequest.prototype.toJSON = function() {
                return ImportRequest.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for ImportRequest
             * @function getTypeUrl
             * @memberof northstar.data_hub.ImportRequest
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            ImportRequest.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.ImportRequest";
            };

            return ImportRequest;
        })();

        data_hub.ImportSpecification = (function() {

            /**
             * Properties of an ImportSpecification.
             * @typedef {Object} northstar.data_hub.ImportSpecification.$Properties
             * @property {string|null} [availability_basis] ImportSpecification availability_basis
             * @property {string|null} [availability_note] ImportSpecification availability_note
             * @property {string|null} [currency] ImportSpecification currency
             * @property {string|null} [exchange] ImportSpecification exchange
             * @property {string|null} [multiplier] ImportSpecification multiplier
             * @property {string|null} [price_tick] ImportSpecification price_tick
             * @property {string|null} [product] ImportSpecification product
             * @property {string|null} [quantity_unit] ImportSpecification quantity_unit
             * @property {string|null} [session_close] ImportSpecification session_close
             * @property {string|null} [session_open] ImportSpecification session_open
             * @property {string|null} [source_name] ImportSpecification source_name
             * @property {string|null} [source_reference] ImportSpecification source_reference
             * @property {string|null} [symbol] ImportSpecification symbol
             * @property {string|null} [timezone] ImportSpecification timezone
             * @property {string|null} [trading_day] ImportSpecification trading_day
             * @property {"availability_basis"} [_availability_basis] ImportSpecification _availability_basis
             * @property {"availability_note"} [_availability_note] ImportSpecification _availability_note
             * @property {"currency"} [_currency] ImportSpecification _currency
             * @property {"exchange"} [_exchange] ImportSpecification _exchange
             * @property {"multiplier"} [_multiplier] ImportSpecification _multiplier
             * @property {"price_tick"} [_price_tick] ImportSpecification _price_tick
             * @property {"product"} [_product] ImportSpecification _product
             * @property {"quantity_unit"} [_quantity_unit] ImportSpecification _quantity_unit
             * @property {"session_close"} [_session_close] ImportSpecification _session_close
             * @property {"session_open"} [_session_open] ImportSpecification _session_open
             * @property {"source_name"} [_source_name] ImportSpecification _source_name
             * @property {"source_reference"} [_source_reference] ImportSpecification _source_reference
             * @property {"symbol"} [_symbol] ImportSpecification _symbol
             * @property {"timezone"} [_timezone] ImportSpecification _timezone
             * @property {"trading_day"} [_trading_day] ImportSpecification _trading_day
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of an ImportSpecification.
             * @memberof northstar.data_hub
             * @interface IImportSpecification
             * @augments northstar.data_hub.ImportSpecification.$Properties
             * @deprecated Use northstar.data_hub.ImportSpecification.$Properties instead.
             */

            /**
             * Narrowed shape of an ImportSpecification.
             * @typedef {{
             *   availability_basis?: string|null;
             *   availability_note?: string|null;
             *   currency?: string|null;
             *   exchange?: string|null;
             *   multiplier?: string|null;
             *   price_tick?: string|null;
             *   product?: string|null;
             *   quantity_unit?: string|null;
             *   session_close?: string|null;
             *   session_open?: string|null;
             *   source_name?: string|null;
             *   source_reference?: string|null;
             *   symbol?: string|null;
             *   timezone?: string|null;
             *   trading_day?: string|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _availability_basis?: undefined; availability_basis?: null }|{ _availability_basis?: "availability_basis"; availability_basis: string })
             * ) & (
             *   ({ _availability_note?: undefined; availability_note?: null }|{ _availability_note?: "availability_note"; availability_note: string })
             * ) & (
             *   ({ _currency?: undefined; currency?: null }|{ _currency?: "currency"; currency: string })
             * ) & (
             *   ({ _exchange?: undefined; exchange?: null }|{ _exchange?: "exchange"; exchange: string })
             * ) & (
             *   ({ _multiplier?: undefined; multiplier?: null }|{ _multiplier?: "multiplier"; multiplier: string })
             * ) & (
             *   ({ _price_tick?: undefined; price_tick?: null }|{ _price_tick?: "price_tick"; price_tick: string })
             * ) & (
             *   ({ _product?: undefined; product?: null }|{ _product?: "product"; product: string })
             * ) & (
             *   ({ _quantity_unit?: undefined; quantity_unit?: null }|{ _quantity_unit?: "quantity_unit"; quantity_unit: string })
             * ) & (
             *   ({ _session_close?: undefined; session_close?: null }|{ _session_close?: "session_close"; session_close: string })
             * ) & (
             *   ({ _session_open?: undefined; session_open?: null }|{ _session_open?: "session_open"; session_open: string })
             * ) & (
             *   ({ _source_name?: undefined; source_name?: null }|{ _source_name?: "source_name"; source_name: string })
             * ) & (
             *   ({ _source_reference?: undefined; source_reference?: null }|{ _source_reference?: "source_reference"; source_reference: string })
             * ) & (
             *   ({ _symbol?: undefined; symbol?: null }|{ _symbol?: "symbol"; symbol: string })
             * ) & (
             *   ({ _timezone?: undefined; timezone?: null }|{ _timezone?: "timezone"; timezone: string })
             * ) & (
             *   ({ _trading_day?: undefined; trading_day?: null }|{ _trading_day?: "trading_day"; trading_day: string })
             * )} northstar.data_hub.ImportSpecification.$Shape
             */

            /**
             * Constructs a new ImportSpecification.
             * @memberof northstar.data_hub
             * @classdesc Represents an ImportSpecification.
             * @constructor
             * @param {northstar.data_hub.ImportSpecification.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const ImportSpecification = function (properties) {
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * ImportSpecification availability_basis.
             * @member {string|null|undefined} availability_basis
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.availability_basis = null;

            /**
             * ImportSpecification availability_note.
             * @member {string|null|undefined} availability_note
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.availability_note = null;

            /**
             * ImportSpecification currency.
             * @member {string|null|undefined} currency
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.currency = null;

            /**
             * ImportSpecification exchange.
             * @member {string|null|undefined} exchange
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.exchange = null;

            /**
             * ImportSpecification multiplier.
             * @member {string|null|undefined} multiplier
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.multiplier = null;

            /**
             * ImportSpecification price_tick.
             * @member {string|null|undefined} price_tick
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.price_tick = null;

            /**
             * ImportSpecification product.
             * @member {string|null|undefined} product
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.product = null;

            /**
             * ImportSpecification quantity_unit.
             * @member {string|null|undefined} quantity_unit
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.quantity_unit = null;

            /**
             * ImportSpecification session_close.
             * @member {string|null|undefined} session_close
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.session_close = null;

            /**
             * ImportSpecification session_open.
             * @member {string|null|undefined} session_open
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.session_open = null;

            /**
             * ImportSpecification source_name.
             * @member {string|null|undefined} source_name
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.source_name = null;

            /**
             * ImportSpecification source_reference.
             * @member {string|null|undefined} source_reference
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.source_reference = null;

            /**
             * ImportSpecification symbol.
             * @member {string|null|undefined} symbol
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.symbol = null;

            /**
             * ImportSpecification timezone.
             * @member {string|null|undefined} timezone
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.timezone = null;

            /**
             * ImportSpecification trading_day.
             * @member {string|null|undefined} trading_day
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            ImportSpecification.prototype.trading_day = null;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * ImportSpecification _availability_basis.
             * @member {"availability_basis"|undefined} _availability_basis
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_availability_basis", {
                get: $util.oneOfGetter($oneOfFields = ["availability_basis"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _availability_note.
             * @member {"availability_note"|undefined} _availability_note
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_availability_note", {
                get: $util.oneOfGetter($oneOfFields = ["availability_note"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _currency.
             * @member {"currency"|undefined} _currency
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_currency", {
                get: $util.oneOfGetter($oneOfFields = ["currency"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _exchange.
             * @member {"exchange"|undefined} _exchange
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_exchange", {
                get: $util.oneOfGetter($oneOfFields = ["exchange"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _multiplier.
             * @member {"multiplier"|undefined} _multiplier
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_multiplier", {
                get: $util.oneOfGetter($oneOfFields = ["multiplier"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _price_tick.
             * @member {"price_tick"|undefined} _price_tick
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_price_tick", {
                get: $util.oneOfGetter($oneOfFields = ["price_tick"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _product.
             * @member {"product"|undefined} _product
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_product", {
                get: $util.oneOfGetter($oneOfFields = ["product"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _quantity_unit.
             * @member {"quantity_unit"|undefined} _quantity_unit
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_quantity_unit", {
                get: $util.oneOfGetter($oneOfFields = ["quantity_unit"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _session_close.
             * @member {"session_close"|undefined} _session_close
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_session_close", {
                get: $util.oneOfGetter($oneOfFields = ["session_close"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _session_open.
             * @member {"session_open"|undefined} _session_open
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_session_open", {
                get: $util.oneOfGetter($oneOfFields = ["session_open"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _source_name.
             * @member {"source_name"|undefined} _source_name
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_source_name", {
                get: $util.oneOfGetter($oneOfFields = ["source_name"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _source_reference.
             * @member {"source_reference"|undefined} _source_reference
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_source_reference", {
                get: $util.oneOfGetter($oneOfFields = ["source_reference"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _symbol.
             * @member {"symbol"|undefined} _symbol
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_symbol", {
                get: $util.oneOfGetter($oneOfFields = ["symbol"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _timezone.
             * @member {"timezone"|undefined} _timezone
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_timezone", {
                get: $util.oneOfGetter($oneOfFields = ["timezone"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ImportSpecification _trading_day.
             * @member {"trading_day"|undefined} _trading_day
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             */
            $Object.defineProperty(ImportSpecification.prototype, "_trading_day", {
                get: $util.oneOfGetter($oneOfFields = ["trading_day"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new ImportSpecification instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.ImportSpecification
             * @static
             * @param {northstar.data_hub.ImportSpecification.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.ImportSpecification} ImportSpecification instance
             * @type {{
             *   (properties: northstar.data_hub.ImportSpecification.$Shape): northstar.data_hub.ImportSpecification & northstar.data_hub.ImportSpecification.$Shape;
             *   (properties?: northstar.data_hub.ImportSpecification.$Properties): northstar.data_hub.ImportSpecification;
             * }}
             */
            ImportSpecification.create = function(properties) {
                return new ImportSpecification(properties);
            };

            /**
             * Encodes the specified ImportSpecification message. Does not implicitly {@link northstar.data_hub.ImportSpecification.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.ImportSpecification
             * @static
             * @param {northstar.data_hub.ImportSpecification.$Properties} message ImportSpecification message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            ImportSpecification.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.availability_basis != null && $Object.hasOwnProperty.call(message, "availability_basis"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.availability_basis);
                if (message.availability_note != null && $Object.hasOwnProperty.call(message, "availability_note"))
                    writer.uint32(/* id 2, wireType 2 =*/18).string(message.availability_note);
                if (message.currency != null && $Object.hasOwnProperty.call(message, "currency"))
                    writer.uint32(/* id 3, wireType 2 =*/26).string(message.currency);
                if (message.exchange != null && $Object.hasOwnProperty.call(message, "exchange"))
                    writer.uint32(/* id 4, wireType 2 =*/34).string(message.exchange);
                if (message.multiplier != null && $Object.hasOwnProperty.call(message, "multiplier"))
                    writer.uint32(/* id 5, wireType 2 =*/42).string(message.multiplier);
                if (message.price_tick != null && $Object.hasOwnProperty.call(message, "price_tick"))
                    writer.uint32(/* id 6, wireType 2 =*/50).string(message.price_tick);
                if (message.product != null && $Object.hasOwnProperty.call(message, "product"))
                    writer.uint32(/* id 7, wireType 2 =*/58).string(message.product);
                if (message.quantity_unit != null && $Object.hasOwnProperty.call(message, "quantity_unit"))
                    writer.uint32(/* id 8, wireType 2 =*/66).string(message.quantity_unit);
                if (message.session_close != null && $Object.hasOwnProperty.call(message, "session_close"))
                    writer.uint32(/* id 9, wireType 2 =*/74).string(message.session_close);
                if (message.session_open != null && $Object.hasOwnProperty.call(message, "session_open"))
                    writer.uint32(/* id 10, wireType 2 =*/82).string(message.session_open);
                if (message.source_name != null && $Object.hasOwnProperty.call(message, "source_name"))
                    writer.uint32(/* id 11, wireType 2 =*/90).string(message.source_name);
                if (message.source_reference != null && $Object.hasOwnProperty.call(message, "source_reference"))
                    writer.uint32(/* id 12, wireType 2 =*/98).string(message.source_reference);
                if (message.symbol != null && $Object.hasOwnProperty.call(message, "symbol"))
                    writer.uint32(/* id 13, wireType 2 =*/106).string(message.symbol);
                if (message.timezone != null && $Object.hasOwnProperty.call(message, "timezone"))
                    writer.uint32(/* id 14, wireType 2 =*/114).string(message.timezone);
                if (message.trading_day != null && $Object.hasOwnProperty.call(message, "trading_day"))
                    writer.uint32(/* id 15, wireType 2 =*/122).string(message.trading_day);
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes an ImportSpecification message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.ImportSpecification
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.ImportSpecification & northstar.data_hub.ImportSpecification.$Shape} ImportSpecification
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            ImportSpecification.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.ImportSpecification();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.availability_basis = reader.stringVerify();
                            message._availability_basis = "availability_basis";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 2)
                                break;
                            message.availability_note = reader.stringVerify();
                            message._availability_note = "availability_note";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 2)
                                break;
                            message.currency = reader.stringVerify();
                            message._currency = "currency";
                            continue;
                        }
                    case 4: {
                            if (wireType !== 2)
                                break;
                            message.exchange = reader.stringVerify();
                            message._exchange = "exchange";
                            continue;
                        }
                    case 5: {
                            if (wireType !== 2)
                                break;
                            message.multiplier = reader.stringVerify();
                            message._multiplier = "multiplier";
                            continue;
                        }
                    case 6: {
                            if (wireType !== 2)
                                break;
                            message.price_tick = reader.stringVerify();
                            message._price_tick = "price_tick";
                            continue;
                        }
                    case 7: {
                            if (wireType !== 2)
                                break;
                            message.product = reader.stringVerify();
                            message._product = "product";
                            continue;
                        }
                    case 8: {
                            if (wireType !== 2)
                                break;
                            message.quantity_unit = reader.stringVerify();
                            message._quantity_unit = "quantity_unit";
                            continue;
                        }
                    case 9: {
                            if (wireType !== 2)
                                break;
                            message.session_close = reader.stringVerify();
                            message._session_close = "session_close";
                            continue;
                        }
                    case 10: {
                            if (wireType !== 2)
                                break;
                            message.session_open = reader.stringVerify();
                            message._session_open = "session_open";
                            continue;
                        }
                    case 11: {
                            if (wireType !== 2)
                                break;
                            message.source_name = reader.stringVerify();
                            message._source_name = "source_name";
                            continue;
                        }
                    case 12: {
                            if (wireType !== 2)
                                break;
                            message.source_reference = reader.stringVerify();
                            message._source_reference = "source_reference";
                            continue;
                        }
                    case 13: {
                            if (wireType !== 2)
                                break;
                            message.symbol = reader.stringVerify();
                            message._symbol = "symbol";
                            continue;
                        }
                    case 14: {
                            if (wireType !== 2)
                                break;
                            message.timezone = reader.stringVerify();
                            message._timezone = "timezone";
                            continue;
                        }
                    case 15: {
                            if (wireType !== 2)
                                break;
                            message.trading_day = reader.stringVerify();
                            message._trading_day = "trading_day";
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies an ImportSpecification message.
             * @function verify
             * @memberof northstar.data_hub.ImportSpecification
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            ImportSpecification.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.availability_basis != null && $Object.hasOwnProperty.call(message, "availability_basis")) {
                    properties._availability_basis = 1;
                    if (!$util.isString(message.availability_basis))
                        return "availability_basis: string expected";
                }
                if (message.availability_note != null && $Object.hasOwnProperty.call(message, "availability_note")) {
                    properties._availability_note = 1;
                    if (!$util.isString(message.availability_note))
                        return "availability_note: string expected";
                }
                if (message.currency != null && $Object.hasOwnProperty.call(message, "currency")) {
                    properties._currency = 1;
                    if (!$util.isString(message.currency))
                        return "currency: string expected";
                }
                if (message.exchange != null && $Object.hasOwnProperty.call(message, "exchange")) {
                    properties._exchange = 1;
                    if (!$util.isString(message.exchange))
                        return "exchange: string expected";
                }
                if (message.multiplier != null && $Object.hasOwnProperty.call(message, "multiplier")) {
                    properties._multiplier = 1;
                    if (!$util.isString(message.multiplier))
                        return "multiplier: string expected";
                }
                if (message.price_tick != null && $Object.hasOwnProperty.call(message, "price_tick")) {
                    properties._price_tick = 1;
                    if (!$util.isString(message.price_tick))
                        return "price_tick: string expected";
                }
                if (message.product != null && $Object.hasOwnProperty.call(message, "product")) {
                    properties._product = 1;
                    if (!$util.isString(message.product))
                        return "product: string expected";
                }
                if (message.quantity_unit != null && $Object.hasOwnProperty.call(message, "quantity_unit")) {
                    properties._quantity_unit = 1;
                    if (!$util.isString(message.quantity_unit))
                        return "quantity_unit: string expected";
                }
                if (message.session_close != null && $Object.hasOwnProperty.call(message, "session_close")) {
                    properties._session_close = 1;
                    if (!$util.isString(message.session_close))
                        return "session_close: string expected";
                }
                if (message.session_open != null && $Object.hasOwnProperty.call(message, "session_open")) {
                    properties._session_open = 1;
                    if (!$util.isString(message.session_open))
                        return "session_open: string expected";
                }
                if (message.source_name != null && $Object.hasOwnProperty.call(message, "source_name")) {
                    properties._source_name = 1;
                    if (!$util.isString(message.source_name))
                        return "source_name: string expected";
                }
                if (message.source_reference != null && $Object.hasOwnProperty.call(message, "source_reference")) {
                    properties._source_reference = 1;
                    if (!$util.isString(message.source_reference))
                        return "source_reference: string expected";
                }
                if (message.symbol != null && $Object.hasOwnProperty.call(message, "symbol")) {
                    properties._symbol = 1;
                    if (!$util.isString(message.symbol))
                        return "symbol: string expected";
                }
                if (message.timezone != null && $Object.hasOwnProperty.call(message, "timezone")) {
                    properties._timezone = 1;
                    if (!$util.isString(message.timezone))
                        return "timezone: string expected";
                }
                if (message.trading_day != null && $Object.hasOwnProperty.call(message, "trading_day")) {
                    properties._trading_day = 1;
                    if (!$util.isString(message.trading_day))
                        return "trading_day: string expected";
                }
                return null;
            };

            /**
             * Creates an ImportSpecification message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.ImportSpecification
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.ImportSpecification} ImportSpecification
             */
            ImportSpecification.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.ImportSpecification)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.ImportSpecification: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.ImportSpecification();
                if (object.availability_basis != null)
                    message.availability_basis = $String(object.availability_basis);
                if (object.availability_note != null)
                    message.availability_note = $String(object.availability_note);
                if (object.currency != null)
                    message.currency = $String(object.currency);
                if (object.exchange != null)
                    message.exchange = $String(object.exchange);
                if (object.multiplier != null)
                    message.multiplier = $String(object.multiplier);
                if (object.price_tick != null)
                    message.price_tick = $String(object.price_tick);
                if (object.product != null)
                    message.product = $String(object.product);
                if (object.quantity_unit != null)
                    message.quantity_unit = $String(object.quantity_unit);
                if (object.session_close != null)
                    message.session_close = $String(object.session_close);
                if (object.session_open != null)
                    message.session_open = $String(object.session_open);
                if (object.source_name != null)
                    message.source_name = $String(object.source_name);
                if (object.source_reference != null)
                    message.source_reference = $String(object.source_reference);
                if (object.symbol != null)
                    message.symbol = $String(object.symbol);
                if (object.timezone != null)
                    message.timezone = $String(object.timezone);
                if (object.trading_day != null)
                    message.trading_day = $String(object.trading_day);
                return message;
            };

            /**
             * Creates a plain object from an ImportSpecification message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.ImportSpecification
             * @static
             * @param {northstar.data_hub.ImportSpecification} message ImportSpecification
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            ImportSpecification.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (message.availability_basis != null && $Object.hasOwnProperty.call(message, "availability_basis")) {
                    object.availability_basis = message.availability_basis;
                    if (options.oneofs)
                        object._availability_basis = "availability_basis";
                }
                if (message.availability_note != null && $Object.hasOwnProperty.call(message, "availability_note")) {
                    object.availability_note = message.availability_note;
                    if (options.oneofs)
                        object._availability_note = "availability_note";
                }
                if (message.currency != null && $Object.hasOwnProperty.call(message, "currency")) {
                    object.currency = message.currency;
                    if (options.oneofs)
                        object._currency = "currency";
                }
                if (message.exchange != null && $Object.hasOwnProperty.call(message, "exchange")) {
                    object.exchange = message.exchange;
                    if (options.oneofs)
                        object._exchange = "exchange";
                }
                if (message.multiplier != null && $Object.hasOwnProperty.call(message, "multiplier")) {
                    object.multiplier = message.multiplier;
                    if (options.oneofs)
                        object._multiplier = "multiplier";
                }
                if (message.price_tick != null && $Object.hasOwnProperty.call(message, "price_tick")) {
                    object.price_tick = message.price_tick;
                    if (options.oneofs)
                        object._price_tick = "price_tick";
                }
                if (message.product != null && $Object.hasOwnProperty.call(message, "product")) {
                    object.product = message.product;
                    if (options.oneofs)
                        object._product = "product";
                }
                if (message.quantity_unit != null && $Object.hasOwnProperty.call(message, "quantity_unit")) {
                    object.quantity_unit = message.quantity_unit;
                    if (options.oneofs)
                        object._quantity_unit = "quantity_unit";
                }
                if (message.session_close != null && $Object.hasOwnProperty.call(message, "session_close")) {
                    object.session_close = message.session_close;
                    if (options.oneofs)
                        object._session_close = "session_close";
                }
                if (message.session_open != null && $Object.hasOwnProperty.call(message, "session_open")) {
                    object.session_open = message.session_open;
                    if (options.oneofs)
                        object._session_open = "session_open";
                }
                if (message.source_name != null && $Object.hasOwnProperty.call(message, "source_name")) {
                    object.source_name = message.source_name;
                    if (options.oneofs)
                        object._source_name = "source_name";
                }
                if (message.source_reference != null && $Object.hasOwnProperty.call(message, "source_reference")) {
                    object.source_reference = message.source_reference;
                    if (options.oneofs)
                        object._source_reference = "source_reference";
                }
                if (message.symbol != null && $Object.hasOwnProperty.call(message, "symbol")) {
                    object.symbol = message.symbol;
                    if (options.oneofs)
                        object._symbol = "symbol";
                }
                if (message.timezone != null && $Object.hasOwnProperty.call(message, "timezone")) {
                    object.timezone = message.timezone;
                    if (options.oneofs)
                        object._timezone = "timezone";
                }
                if (message.trading_day != null && $Object.hasOwnProperty.call(message, "trading_day")) {
                    object.trading_day = message.trading_day;
                    if (options.oneofs)
                        object._trading_day = "trading_day";
                }
                return object;
            };

            /**
             * Converts this ImportSpecification to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.ImportSpecification
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            ImportSpecification.prototype.toJSON = function() {
                return ImportSpecification.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for ImportSpecification
             * @function getTypeUrl
             * @memberof northstar.data_hub.ImportSpecification
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            ImportSpecification.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.ImportSpecification";
            };

            return ImportSpecification;
        })();

        data_hub.ProcessingAttempt = (function() {

            /**
             * Properties of a ProcessingAttempt.
             * @typedef {Object} northstar.data_hub.ProcessingAttempt.$Properties
             * @property {string|null} [attempt_id] ProcessingAttempt attempt_id
             * @property {string|null} [created_at] ProcessingAttempt created_at
             * @property {string|null} [error] ProcessingAttempt error
             * @property {google.protobuf.Struct.$Properties|null} [parameters] ProcessingAttempt parameters
             * @property {string|null} [snapshot_id] ProcessingAttempt snapshot_id
             * @property {string|null} [source_id] ProcessingAttempt source_id
             * @property {string|null} [stage] ProcessingAttempt stage
             * @property {string|null} [status] ProcessingAttempt status
             * @property {Object.<string,google.protobuf.Value.$Properties>|null} [evidence_fields] ProcessingAttempt evidence_fields
             * @property {Array.<string>|null} [null_fields] ProcessingAttempt null_fields
             * @property {"attempt_id"} [_attempt_id] ProcessingAttempt _attempt_id
             * @property {"created_at"} [_created_at] ProcessingAttempt _created_at
             * @property {"error"} [_error] ProcessingAttempt _error
             * @property {"parameters"} [_parameters] ProcessingAttempt _parameters
             * @property {"snapshot_id"} [_snapshot_id] ProcessingAttempt _snapshot_id
             * @property {"source_id"} [_source_id] ProcessingAttempt _source_id
             * @property {"stage"} [_stage] ProcessingAttempt _stage
             * @property {"status"} [_status] ProcessingAttempt _status
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a ProcessingAttempt.
             * @memberof northstar.data_hub
             * @interface IProcessingAttempt
             * @augments northstar.data_hub.ProcessingAttempt.$Properties
             * @deprecated Use northstar.data_hub.ProcessingAttempt.$Properties instead.
             */

            /**
             * Narrowed shape of a ProcessingAttempt.
             * @typedef {{
             *   attempt_id?: string|null;
             *   created_at?: string|null;
             *   error?: string|null;
             *   parameters?: google.protobuf.Struct.$Shape|null;
             *   snapshot_id?: string|null;
             *   source_id?: string|null;
             *   stage?: string|null;
             *   status?: string|null;
             *   evidence_fields?: Object.<string,google.protobuf.Value.$Shape>|null;
             *   null_fields?: Array.<string>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _attempt_id?: undefined; attempt_id?: null }|{ _attempt_id?: "attempt_id"; attempt_id: string })
             * ) & (
             *   ({ _created_at?: undefined; created_at?: null }|{ _created_at?: "created_at"; created_at: string })
             * ) & (
             *   ({ _error?: undefined; error?: null }|{ _error?: "error"; error: string })
             * ) & (
             *   ({ _parameters?: undefined; parameters?: null }|{ _parameters?: "parameters"; parameters: google.protobuf.Struct.$Shape })
             * ) & (
             *   ({ _snapshot_id?: undefined; snapshot_id?: null }|{ _snapshot_id?: "snapshot_id"; snapshot_id: string })
             * ) & (
             *   ({ _source_id?: undefined; source_id?: null }|{ _source_id?: "source_id"; source_id: string })
             * ) & (
             *   ({ _stage?: undefined; stage?: null }|{ _stage?: "stage"; stage: string })
             * ) & (
             *   ({ _status?: undefined; status?: null }|{ _status?: "status"; status: string })
             * )} northstar.data_hub.ProcessingAttempt.$Shape
             */

            /**
             * Constructs a new ProcessingAttempt.
             * @memberof northstar.data_hub
             * @classdesc Represents a ProcessingAttempt.
             * @constructor
             * @param {northstar.data_hub.ProcessingAttempt.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const ProcessingAttempt = function (properties) {
                this.evidence_fields = {};
                this.null_fields = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * ProcessingAttempt attempt_id.
             * @member {string|null|undefined} attempt_id
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            ProcessingAttempt.prototype.attempt_id = null;

            /**
             * ProcessingAttempt created_at.
             * @member {string|null|undefined} created_at
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            ProcessingAttempt.prototype.created_at = null;

            /**
             * ProcessingAttempt error.
             * @member {string|null|undefined} error
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            ProcessingAttempt.prototype.error = null;

            /**
             * ProcessingAttempt parameters.
             * @member {google.protobuf.Struct.$Properties|null|undefined} parameters
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            ProcessingAttempt.prototype.parameters = null;

            /**
             * ProcessingAttempt snapshot_id.
             * @member {string|null|undefined} snapshot_id
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            ProcessingAttempt.prototype.snapshot_id = null;

            /**
             * ProcessingAttempt source_id.
             * @member {string|null|undefined} source_id
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            ProcessingAttempt.prototype.source_id = null;

            /**
             * ProcessingAttempt stage.
             * @member {string|null|undefined} stage
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            ProcessingAttempt.prototype.stage = null;

            /**
             * ProcessingAttempt status.
             * @member {string|null|undefined} status
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            ProcessingAttempt.prototype.status = null;

            /**
             * ProcessingAttempt evidence_fields.
             * @member {Object.<string,google.protobuf.Value.$Properties>} evidence_fields
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            ProcessingAttempt.prototype.evidence_fields = $util.emptyObject;

            /**
             * ProcessingAttempt null_fields.
             * @member {Array.<string>} null_fields
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            ProcessingAttempt.prototype.null_fields = $util.emptyArray;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * ProcessingAttempt _attempt_id.
             * @member {"attempt_id"|undefined} _attempt_id
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            $Object.defineProperty(ProcessingAttempt.prototype, "_attempt_id", {
                get: $util.oneOfGetter($oneOfFields = ["attempt_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingAttempt _created_at.
             * @member {"created_at"|undefined} _created_at
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            $Object.defineProperty(ProcessingAttempt.prototype, "_created_at", {
                get: $util.oneOfGetter($oneOfFields = ["created_at"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingAttempt _error.
             * @member {"error"|undefined} _error
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            $Object.defineProperty(ProcessingAttempt.prototype, "_error", {
                get: $util.oneOfGetter($oneOfFields = ["error"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingAttempt _parameters.
             * @member {"parameters"|undefined} _parameters
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            $Object.defineProperty(ProcessingAttempt.prototype, "_parameters", {
                get: $util.oneOfGetter($oneOfFields = ["parameters"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingAttempt _snapshot_id.
             * @member {"snapshot_id"|undefined} _snapshot_id
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            $Object.defineProperty(ProcessingAttempt.prototype, "_snapshot_id", {
                get: $util.oneOfGetter($oneOfFields = ["snapshot_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingAttempt _source_id.
             * @member {"source_id"|undefined} _source_id
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            $Object.defineProperty(ProcessingAttempt.prototype, "_source_id", {
                get: $util.oneOfGetter($oneOfFields = ["source_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingAttempt _stage.
             * @member {"stage"|undefined} _stage
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            $Object.defineProperty(ProcessingAttempt.prototype, "_stage", {
                get: $util.oneOfGetter($oneOfFields = ["stage"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingAttempt _status.
             * @member {"status"|undefined} _status
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             */
            $Object.defineProperty(ProcessingAttempt.prototype, "_status", {
                get: $util.oneOfGetter($oneOfFields = ["status"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new ProcessingAttempt instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.ProcessingAttempt
             * @static
             * @param {northstar.data_hub.ProcessingAttempt.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.ProcessingAttempt} ProcessingAttempt instance
             * @type {{
             *   (properties: northstar.data_hub.ProcessingAttempt.$Shape): northstar.data_hub.ProcessingAttempt & northstar.data_hub.ProcessingAttempt.$Shape;
             *   (properties?: northstar.data_hub.ProcessingAttempt.$Properties): northstar.data_hub.ProcessingAttempt;
             * }}
             */
            ProcessingAttempt.create = function(properties) {
                return new ProcessingAttempt(properties);
            };

            /**
             * Encodes the specified ProcessingAttempt message. Does not implicitly {@link northstar.data_hub.ProcessingAttempt.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.ProcessingAttempt
             * @static
             * @param {northstar.data_hub.ProcessingAttempt.$Properties} message ProcessingAttempt message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            ProcessingAttempt.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.attempt_id != null && $Object.hasOwnProperty.call(message, "attempt_id"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.attempt_id);
                if (message.created_at != null && $Object.hasOwnProperty.call(message, "created_at"))
                    writer.uint32(/* id 2, wireType 2 =*/18).string(message.created_at);
                if (message.error != null && $Object.hasOwnProperty.call(message, "error"))
                    writer.uint32(/* id 3, wireType 2 =*/26).string(message.error);
                if (message.parameters != null && $Object.hasOwnProperty.call(message, "parameters"))
                    $root.google.protobuf.Struct.encode(message.parameters, writer.uint32(/* id 4, wireType 2 =*/34).fork(), _depth + 1).ldelim();
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id"))
                    writer.uint32(/* id 5, wireType 2 =*/42).string(message.snapshot_id);
                if (message.source_id != null && $Object.hasOwnProperty.call(message, "source_id"))
                    writer.uint32(/* id 6, wireType 2 =*/50).string(message.source_id);
                if (message.stage != null && $Object.hasOwnProperty.call(message, "stage"))
                    writer.uint32(/* id 7, wireType 2 =*/58).string(message.stage);
                if (message.status != null && $Object.hasOwnProperty.call(message, "status"))
                    writer.uint32(/* id 8, wireType 2 =*/66).string(message.status);
                if (message.evidence_fields != null && $Object.hasOwnProperty.call(message, "evidence_fields"))
                    for (let keys = $Object.keys(message.evidence_fields), i = 0; i < keys.length; ++i) {
                        writer.uint32(/* id 1000, wireType 2 =*/8002).fork().uint32(/* id 1, wireType 2 =*/10).string(keys[i]);
                        $root.google.protobuf.Value.encode(message.evidence_fields[keys[i]], writer.uint32(/* id 2, wireType 2 =*/18).fork(), _depth + 1).ldelim().ldelim();
                    }
                if (message.null_fields != null && message.null_fields.length)
                    for (let i = 0; i < message.null_fields.length; ++i)
                        writer.uint32(/* id 2046, wireType 2 =*/16370).string(message.null_fields[i]);
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a ProcessingAttempt message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.ProcessingAttempt
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.ProcessingAttempt & northstar.data_hub.ProcessingAttempt.$Shape} ProcessingAttempt
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            ProcessingAttempt.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message, key, value;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.ProcessingAttempt();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.attempt_id = reader.stringVerify();
                            message._attempt_id = "attempt_id";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 2)
                                break;
                            message.created_at = reader.stringVerify();
                            message._created_at = "created_at";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 2)
                                break;
                            message.error = reader.stringVerify();
                            message._error = "error";
                            continue;
                        }
                    case 4: {
                            if (wireType !== 2)
                                break;
                            message.parameters = $root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1, message.parameters);
                            message._parameters = "parameters";
                            continue;
                        }
                    case 5: {
                            if (wireType !== 2)
                                break;
                            message.snapshot_id = reader.stringVerify();
                            message._snapshot_id = "snapshot_id";
                            continue;
                        }
                    case 6: {
                            if (wireType !== 2)
                                break;
                            message.source_id = reader.stringVerify();
                            message._source_id = "source_id";
                            continue;
                        }
                    case 7: {
                            if (wireType !== 2)
                                break;
                            message.stage = reader.stringVerify();
                            message._stage = "stage";
                            continue;
                        }
                    case 8: {
                            if (wireType !== 2)
                                break;
                            message.status = reader.stringVerify();
                            message._status = "status";
                            continue;
                        }
                    case 1000: {
                            if (wireType !== 2)
                                break;
                            if (message.evidence_fields === $util.emptyObject)
                                message.evidence_fields = {};
                            let end2 = reader.uint32() + reader.pos;
                            if (end2 > reader.len)
                                throw $RangeError("index out of range");
                            reader.len = end2;
                            key = "";
                            value = null;
                            while (reader.pos < end2) {
                                let tag2 = reader.tag();
                                wireType = tag2 & 7;
                                switch (tag2 >>>= 3) {
                                case 1:
                                    if (wireType !== 2)
                                        break;
                                    key = reader.stringVerify();
                                    continue;
                                case 2:
                                    if (wireType !== 2)
                                        break;
                                    value = $root.google.protobuf.Value.decode(reader, reader.uint32(), $undefined, _depth + 1, value);
                                    continue;
                                }
                                reader.skipType(wireType, _depth, tag2);
                            }
                            if (reader.pos !== end2)
                                throw $RangeError("index out of range");
                            reader.len = end;
                            if (key === "__proto__")
                                $util.makeProp(message.evidence_fields, key);
                            message.evidence_fields[key] = value || new $root.google.protobuf.Value();
                            continue;
                        }
                    case 2046: {
                            if (wireType !== 2)
                                break;
                            if (!(message.null_fields && message.null_fields.length))
                                message.null_fields = [];
                            message.null_fields.push(reader.stringVerify());
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a ProcessingAttempt message.
             * @function verify
             * @memberof northstar.data_hub.ProcessingAttempt
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            ProcessingAttempt.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.attempt_id != null && $Object.hasOwnProperty.call(message, "attempt_id")) {
                    properties._attempt_id = 1;
                    if (!$util.isString(message.attempt_id))
                        return "attempt_id: string expected";
                }
                if (message.created_at != null && $Object.hasOwnProperty.call(message, "created_at")) {
                    properties._created_at = 1;
                    if (!$util.isString(message.created_at))
                        return "created_at: string expected";
                }
                if (message.error != null && $Object.hasOwnProperty.call(message, "error")) {
                    properties._error = 1;
                    if (!$util.isString(message.error))
                        return "error: string expected";
                }
                if (message.parameters != null && $Object.hasOwnProperty.call(message, "parameters")) {
                    properties._parameters = 1;
                    {
                        let error = $root.google.protobuf.Struct.verify(message.parameters, _depth + 1);
                        if (error)
                            return "parameters." + error;
                    }
                }
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id")) {
                    properties._snapshot_id = 1;
                    if (!$util.isString(message.snapshot_id))
                        return "snapshot_id: string expected";
                }
                if (message.source_id != null && $Object.hasOwnProperty.call(message, "source_id")) {
                    properties._source_id = 1;
                    if (!$util.isString(message.source_id))
                        return "source_id: string expected";
                }
                if (message.stage != null && $Object.hasOwnProperty.call(message, "stage")) {
                    properties._stage = 1;
                    if (!$util.isString(message.stage))
                        return "stage: string expected";
                }
                if (message.status != null && $Object.hasOwnProperty.call(message, "status")) {
                    properties._status = 1;
                    if (!$util.isString(message.status))
                        return "status: string expected";
                }
                if (message.evidence_fields != null && $Object.hasOwnProperty.call(message, "evidence_fields")) {
                    if (!$util.isObject(message.evidence_fields))
                        return "evidence_fields: object expected";
                    let key = $Object.keys(message.evidence_fields);
                    for (let i = 0; i < key.length; ++i) {
                        let error = $root.google.protobuf.Value.verify(message.evidence_fields[key[i]], _depth + 1);
                        if (error)
                            return "evidence_fields." + error;
                    }
                }
                if (message.null_fields != null && $Object.hasOwnProperty.call(message, "null_fields")) {
                    if (!$Array.isArray(message.null_fields))
                        return "null_fields: array expected";
                    for (let i = 0; i < message.null_fields.length; ++i)
                        if (!$util.isString(message.null_fields[i]))
                            return "null_fields: string[] expected";
                }
                return null;
            };

            /**
             * Creates a ProcessingAttempt message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.ProcessingAttempt
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.ProcessingAttempt} ProcessingAttempt
             */
            ProcessingAttempt.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.ProcessingAttempt)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.ProcessingAttempt: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.ProcessingAttempt();
                if (object.attempt_id != null)
                    message.attempt_id = $String(object.attempt_id);
                if (object.created_at != null)
                    message.created_at = $String(object.created_at);
                if (object.error != null)
                    message.error = $String(object.error);
                if (object.parameters != null) {
                    if (!$util.isObject(object.parameters))
                        throw $TypeError(".northstar.data_hub.ProcessingAttempt.parameters: object expected");
                    message.parameters = $root.google.protobuf.Struct.fromObject(object.parameters, _depth + 1);
                }
                if (object.snapshot_id != null)
                    message.snapshot_id = $String(object.snapshot_id);
                if (object.source_id != null)
                    message.source_id = $String(object.source_id);
                if (object.stage != null)
                    message.stage = $String(object.stage);
                if (object.status != null)
                    message.status = $String(object.status);
                if (object.evidence_fields) {
                    if (!$util.isObject(object.evidence_fields))
                        throw $TypeError(".northstar.data_hub.ProcessingAttempt.evidence_fields: object expected");
                    message.evidence_fields = {};
                    for (let keys = $Object.keys(object.evidence_fields), i = 0; i < keys.length; ++i) {
                        if (keys[i] === "__proto__")
                            $util.makeProp(message.evidence_fields, keys[i]);
                        if (!$util.isObject(object.evidence_fields[keys[i]]))
                            throw $TypeError(".northstar.data_hub.ProcessingAttempt.evidence_fields: object expected");
                        message.evidence_fields[keys[i]] = $root.google.protobuf.Value.fromObject(object.evidence_fields[keys[i]], _depth + 1);
                    }
                }
                if (object.null_fields) {
                    if (!$Array.isArray(object.null_fields))
                        throw $TypeError(".northstar.data_hub.ProcessingAttempt.null_fields: array expected");
                    message.null_fields = $Array(object.null_fields.length);
                    for (let i = 0; i < object.null_fields.length; ++i)
                        message.null_fields[i] = $String(object.null_fields[i]);
                }
                return message;
            };

            /**
             * Creates a plain object from a ProcessingAttempt message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.ProcessingAttempt
             * @static
             * @param {northstar.data_hub.ProcessingAttempt} message ProcessingAttempt
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            ProcessingAttempt.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults)
                    object.null_fields = [];
                if (options.objects || options.defaults)
                    object.evidence_fields = {};
                if (message.attempt_id != null && $Object.hasOwnProperty.call(message, "attempt_id")) {
                    object.attempt_id = message.attempt_id;
                    if (options.oneofs)
                        object._attempt_id = "attempt_id";
                }
                if (message.created_at != null && $Object.hasOwnProperty.call(message, "created_at")) {
                    object.created_at = message.created_at;
                    if (options.oneofs)
                        object._created_at = "created_at";
                }
                if (message.error != null && $Object.hasOwnProperty.call(message, "error")) {
                    object.error = message.error;
                    if (options.oneofs)
                        object._error = "error";
                }
                if (message.parameters != null && $Object.hasOwnProperty.call(message, "parameters")) {
                    object.parameters = $root.google.protobuf.Struct.toObject(message.parameters, options, _depth + 1);
                    if (options.oneofs)
                        object._parameters = "parameters";
                }
                if (message.snapshot_id != null && $Object.hasOwnProperty.call(message, "snapshot_id")) {
                    object.snapshot_id = message.snapshot_id;
                    if (options.oneofs)
                        object._snapshot_id = "snapshot_id";
                }
                if (message.source_id != null && $Object.hasOwnProperty.call(message, "source_id")) {
                    object.source_id = message.source_id;
                    if (options.oneofs)
                        object._source_id = "source_id";
                }
                if (message.stage != null && $Object.hasOwnProperty.call(message, "stage")) {
                    object.stage = message.stage;
                    if (options.oneofs)
                        object._stage = "stage";
                }
                if (message.status != null && $Object.hasOwnProperty.call(message, "status")) {
                    object.status = message.status;
                    if (options.oneofs)
                        object._status = "status";
                }
                let keys2;
                if (message.evidence_fields && (keys2 = $Object.keys(message.evidence_fields)).length) {
                    object.evidence_fields = {};
                    for (let j = 0; j < keys2.length; ++j) {
                        if (keys2[j] === "__proto__")
                            $util.makeProp(object.evidence_fields, keys2[j]);
                        object.evidence_fields[keys2[j]] = $root.google.protobuf.Value.toObject(message.evidence_fields[keys2[j]], options, _depth + 1);
                    }
                }
                if (message.null_fields && message.null_fields.length) {
                    object.null_fields = $Array(message.null_fields.length);
                    for (let j = 0; j < message.null_fields.length; ++j)
                        object.null_fields[j] = message.null_fields[j];
                }
                return object;
            };

            /**
             * Converts this ProcessingAttempt to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.ProcessingAttempt
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            ProcessingAttempt.prototype.toJSON = function() {
                return ProcessingAttempt.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for ProcessingAttempt
             * @function getTypeUrl
             * @memberof northstar.data_hub.ProcessingAttempt
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            ProcessingAttempt.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.ProcessingAttempt";
            };

            return ProcessingAttempt;
        })();

        data_hub.Readiness = (function() {

            /**
             * Properties of a Readiness.
             * @typedef {Object} northstar.data_hub.Readiness.$Properties
             * @property {string|null} [status] Readiness status
             * @property {"status"} [_status] Readiness _status
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a Readiness.
             * @memberof northstar.data_hub
             * @interface IReadiness
             * @augments northstar.data_hub.Readiness.$Properties
             * @deprecated Use northstar.data_hub.Readiness.$Properties instead.
             */

            /**
             * Narrowed shape of a Readiness.
             * @typedef {{
             *   status?: string|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _status?: undefined; status?: null }|{ _status?: "status"; status: string })
             * )} northstar.data_hub.Readiness.$Shape
             */

            /**
             * Constructs a new Readiness.
             * @memberof northstar.data_hub
             * @classdesc Represents a Readiness.
             * @constructor
             * @param {northstar.data_hub.Readiness.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const Readiness = function (properties) {
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * Readiness status.
             * @member {string|null|undefined} status
             * @memberof northstar.data_hub.Readiness
             * @instance
             */
            Readiness.prototype.status = null;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * Readiness _status.
             * @member {"status"|undefined} _status
             * @memberof northstar.data_hub.Readiness
             * @instance
             */
            $Object.defineProperty(Readiness.prototype, "_status", {
                get: $util.oneOfGetter($oneOfFields = ["status"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new Readiness instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.Readiness
             * @static
             * @param {northstar.data_hub.Readiness.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.Readiness} Readiness instance
             * @type {{
             *   (properties: northstar.data_hub.Readiness.$Shape): northstar.data_hub.Readiness & northstar.data_hub.Readiness.$Shape;
             *   (properties?: northstar.data_hub.Readiness.$Properties): northstar.data_hub.Readiness;
             * }}
             */
            Readiness.create = function(properties) {
                return new Readiness(properties);
            };

            /**
             * Encodes the specified Readiness message. Does not implicitly {@link northstar.data_hub.Readiness.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.Readiness
             * @static
             * @param {northstar.data_hub.Readiness.$Properties} message Readiness message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            Readiness.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.status != null && $Object.hasOwnProperty.call(message, "status"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.status);
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a Readiness message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.Readiness
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.Readiness & northstar.data_hub.Readiness.$Shape} Readiness
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            Readiness.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.Readiness();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.status = reader.stringVerify();
                            message._status = "status";
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a Readiness message.
             * @function verify
             * @memberof northstar.data_hub.Readiness
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            Readiness.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.status != null && $Object.hasOwnProperty.call(message, "status")) {
                    properties._status = 1;
                    if (!$util.isString(message.status))
                        return "status: string expected";
                }
                return null;
            };

            /**
             * Creates a Readiness message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.Readiness
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.Readiness} Readiness
             */
            Readiness.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.Readiness)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.Readiness: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.Readiness();
                if (object.status != null)
                    message.status = $String(object.status);
                return message;
            };

            /**
             * Creates a plain object from a Readiness message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.Readiness
             * @static
             * @param {northstar.data_hub.Readiness} message Readiness
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            Readiness.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (message.status != null && $Object.hasOwnProperty.call(message, "status")) {
                    object.status = message.status;
                    if (options.oneofs)
                        object._status = "status";
                }
                return object;
            };

            /**
             * Converts this Readiness to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.Readiness
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            Readiness.prototype.toJSON = function() {
                return Readiness.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for Readiness
             * @function getTypeUrl
             * @memberof northstar.data_hub.Readiness
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            Readiness.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.Readiness";
            };

            return Readiness;
        })();

        data_hub.ReprocessRequest = (function() {

            /**
             * Properties of a ReprocessRequest.
             * @typedef {Object} northstar.data_hub.ReprocessRequest.$Properties
             * @property {string|null} [request_id] ReprocessRequest request_id
             * @property {google.protobuf.Struct.$Properties|null} [spec] ReprocessRequest spec
             * @property {"request_id"} [_request_id] ReprocessRequest _request_id
             * @property {"spec"} [_spec] ReprocessRequest _spec
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a ReprocessRequest.
             * @memberof northstar.data_hub
             * @interface IReprocessRequest
             * @augments northstar.data_hub.ReprocessRequest.$Properties
             * @deprecated Use northstar.data_hub.ReprocessRequest.$Properties instead.
             */

            /**
             * Narrowed shape of a ReprocessRequest.
             * @typedef {{
             *   request_id?: string|null;
             *   spec?: google.protobuf.Struct.$Shape|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _request_id?: undefined; request_id?: null }|{ _request_id?: "request_id"; request_id: string })
             * ) & (
             *   ({ _spec?: undefined; spec?: null }|{ _spec?: "spec"; spec: google.protobuf.Struct.$Shape })
             * )} northstar.data_hub.ReprocessRequest.$Shape
             */

            /**
             * Constructs a new ReprocessRequest.
             * @memberof northstar.data_hub
             * @classdesc Represents a ReprocessRequest.
             * @constructor
             * @param {northstar.data_hub.ReprocessRequest.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const ReprocessRequest = function (properties) {
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * ReprocessRequest request_id.
             * @member {string|null|undefined} request_id
             * @memberof northstar.data_hub.ReprocessRequest
             * @instance
             */
            ReprocessRequest.prototype.request_id = null;

            /**
             * ReprocessRequest spec.
             * @member {google.protobuf.Struct.$Properties|null|undefined} spec
             * @memberof northstar.data_hub.ReprocessRequest
             * @instance
             */
            ReprocessRequest.prototype.spec = null;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * ReprocessRequest _request_id.
             * @member {"request_id"|undefined} _request_id
             * @memberof northstar.data_hub.ReprocessRequest
             * @instance
             */
            $Object.defineProperty(ReprocessRequest.prototype, "_request_id", {
                get: $util.oneOfGetter($oneOfFields = ["request_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ReprocessRequest _spec.
             * @member {"spec"|undefined} _spec
             * @memberof northstar.data_hub.ReprocessRequest
             * @instance
             */
            $Object.defineProperty(ReprocessRequest.prototype, "_spec", {
                get: $util.oneOfGetter($oneOfFields = ["spec"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new ReprocessRequest instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.ReprocessRequest
             * @static
             * @param {northstar.data_hub.ReprocessRequest.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.ReprocessRequest} ReprocessRequest instance
             * @type {{
             *   (properties: northstar.data_hub.ReprocessRequest.$Shape): northstar.data_hub.ReprocessRequest & northstar.data_hub.ReprocessRequest.$Shape;
             *   (properties?: northstar.data_hub.ReprocessRequest.$Properties): northstar.data_hub.ReprocessRequest;
             * }}
             */
            ReprocessRequest.create = function(properties) {
                return new ReprocessRequest(properties);
            };

            /**
             * Encodes the specified ReprocessRequest message. Does not implicitly {@link northstar.data_hub.ReprocessRequest.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.ReprocessRequest
             * @static
             * @param {northstar.data_hub.ReprocessRequest.$Properties} message ReprocessRequest message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            ReprocessRequest.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.request_id);
                if (message.spec != null && $Object.hasOwnProperty.call(message, "spec"))
                    $root.google.protobuf.Struct.encode(message.spec, writer.uint32(/* id 2, wireType 2 =*/18).fork(), _depth + 1).ldelim();
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a ReprocessRequest message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.ReprocessRequest
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.ReprocessRequest & northstar.data_hub.ReprocessRequest.$Shape} ReprocessRequest
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            ReprocessRequest.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.ReprocessRequest();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.request_id = reader.stringVerify();
                            message._request_id = "request_id";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 2)
                                break;
                            message.spec = $root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1, message.spec);
                            message._spec = "spec";
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a ReprocessRequest message.
             * @function verify
             * @memberof northstar.data_hub.ReprocessRequest
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            ReprocessRequest.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    properties._request_id = 1;
                    if (!$util.isString(message.request_id))
                        return "request_id: string expected";
                }
                if (message.spec != null && $Object.hasOwnProperty.call(message, "spec")) {
                    properties._spec = 1;
                    {
                        let error = $root.google.protobuf.Struct.verify(message.spec, _depth + 1);
                        if (error)
                            return "spec." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a ReprocessRequest message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.ReprocessRequest
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.ReprocessRequest} ReprocessRequest
             */
            ReprocessRequest.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.ReprocessRequest)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.ReprocessRequest: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.ReprocessRequest();
                if (object.request_id != null)
                    message.request_id = $String(object.request_id);
                if (object.spec != null) {
                    if (!$util.isObject(object.spec))
                        throw $TypeError(".northstar.data_hub.ReprocessRequest.spec: object expected");
                    message.spec = $root.google.protobuf.Struct.fromObject(object.spec, _depth + 1);
                }
                return message;
            };

            /**
             * Creates a plain object from a ReprocessRequest message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.ReprocessRequest
             * @static
             * @param {northstar.data_hub.ReprocessRequest} message ReprocessRequest
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            ReprocessRequest.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    object.request_id = message.request_id;
                    if (options.oneofs)
                        object._request_id = "request_id";
                }
                if (message.spec != null && $Object.hasOwnProperty.call(message, "spec")) {
                    object.spec = $root.google.protobuf.Struct.toObject(message.spec, options, _depth + 1);
                    if (options.oneofs)
                        object._spec = "spec";
                }
                return object;
            };

            /**
             * Converts this ReprocessRequest to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.ReprocessRequest
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            ReprocessRequest.prototype.toJSON = function() {
                return ReprocessRequest.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for ReprocessRequest
             * @function getTypeUrl
             * @memberof northstar.data_hub.ReprocessRequest
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            ReprocessRequest.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.ReprocessRequest";
            };

            return ReprocessRequest;
        })();

        data_hub.SourceRecord = (function() {

            /**
             * Properties of a SourceRecord.
             * @typedef {Object} northstar.data_hub.SourceRecord.$Properties
             * @property {boolean|null} [allow_download] SourceRecord allow_download
             * @property {boolean|null} [allow_retention] SourceRecord allow_retention
             * @property {number|Long|null} [byte_count] SourceRecord byte_count
             * @property {string|null} [content_hash] SourceRecord content_hash
             * @property {string|null} [filename] SourceRecord filename
             * @property {string|null} [input_kind] SourceRecord input_kind
             * @property {string|null} [received_at] SourceRecord received_at
             * @property {string|null} [source_id] SourceRecord source_id
             * @property {string|null} [source_name] SourceRecord source_name
             * @property {Object.<string,google.protobuf.Value.$Properties>|null} [evidence_fields] SourceRecord evidence_fields
             * @property {"allow_download"} [_allow_download] SourceRecord _allow_download
             * @property {"allow_retention"} [_allow_retention] SourceRecord _allow_retention
             * @property {"byte_count"} [_byte_count] SourceRecord _byte_count
             * @property {"content_hash"} [_content_hash] SourceRecord _content_hash
             * @property {"filename"} [_filename] SourceRecord _filename
             * @property {"input_kind"} [_input_kind] SourceRecord _input_kind
             * @property {"received_at"} [_received_at] SourceRecord _received_at
             * @property {"source_id"} [_source_id] SourceRecord _source_id
             * @property {"source_name"} [_source_name] SourceRecord _source_name
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a SourceRecord.
             * @memberof northstar.data_hub
             * @interface ISourceRecord
             * @augments northstar.data_hub.SourceRecord.$Properties
             * @deprecated Use northstar.data_hub.SourceRecord.$Properties instead.
             */

            /**
             * Narrowed shape of a SourceRecord.
             * @typedef {{
             *   allow_download?: boolean|null;
             *   allow_retention?: boolean|null;
             *   byte_count?: number|Long|null;
             *   content_hash?: string|null;
             *   filename?: string|null;
             *   input_kind?: string|null;
             *   received_at?: string|null;
             *   source_id?: string|null;
             *   source_name?: string|null;
             *   evidence_fields?: Object.<string,google.protobuf.Value.$Shape>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _allow_download?: undefined; allow_download?: null }|{ _allow_download?: "allow_download"; allow_download: boolean })
             * ) & (
             *   ({ _allow_retention?: undefined; allow_retention?: null }|{ _allow_retention?: "allow_retention"; allow_retention: boolean })
             * ) & (
             *   ({ _byte_count?: undefined; byte_count?: null }|{ _byte_count?: "byte_count"; byte_count: number|Long })
             * ) & (
             *   ({ _content_hash?: undefined; content_hash?: null }|{ _content_hash?: "content_hash"; content_hash: string })
             * ) & (
             *   ({ _filename?: undefined; filename?: null }|{ _filename?: "filename"; filename: string })
             * ) & (
             *   ({ _input_kind?: undefined; input_kind?: null }|{ _input_kind?: "input_kind"; input_kind: string })
             * ) & (
             *   ({ _received_at?: undefined; received_at?: null }|{ _received_at?: "received_at"; received_at: string })
             * ) & (
             *   ({ _source_id?: undefined; source_id?: null }|{ _source_id?: "source_id"; source_id: string })
             * ) & (
             *   ({ _source_name?: undefined; source_name?: null }|{ _source_name?: "source_name"; source_name: string })
             * )} northstar.data_hub.SourceRecord.$Shape
             */

            /**
             * Constructs a new SourceRecord.
             * @memberof northstar.data_hub
             * @classdesc Represents a SourceRecord.
             * @constructor
             * @param {northstar.data_hub.SourceRecord.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const SourceRecord = function (properties) {
                this.evidence_fields = {};
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * SourceRecord allow_download.
             * @member {boolean|null|undefined} allow_download
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            SourceRecord.prototype.allow_download = null;

            /**
             * SourceRecord allow_retention.
             * @member {boolean|null|undefined} allow_retention
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            SourceRecord.prototype.allow_retention = null;

            /**
             * SourceRecord byte_count.
             * @member {number|Long|null|undefined} byte_count
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            SourceRecord.prototype.byte_count = null;

            /**
             * SourceRecord content_hash.
             * @member {string|null|undefined} content_hash
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            SourceRecord.prototype.content_hash = null;

            /**
             * SourceRecord filename.
             * @member {string|null|undefined} filename
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            SourceRecord.prototype.filename = null;

            /**
             * SourceRecord input_kind.
             * @member {string|null|undefined} input_kind
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            SourceRecord.prototype.input_kind = null;

            /**
             * SourceRecord received_at.
             * @member {string|null|undefined} received_at
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            SourceRecord.prototype.received_at = null;

            /**
             * SourceRecord source_id.
             * @member {string|null|undefined} source_id
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            SourceRecord.prototype.source_id = null;

            /**
             * SourceRecord source_name.
             * @member {string|null|undefined} source_name
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            SourceRecord.prototype.source_name = null;

            /**
             * SourceRecord evidence_fields.
             * @member {Object.<string,google.protobuf.Value.$Properties>} evidence_fields
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            SourceRecord.prototype.evidence_fields = $util.emptyObject;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * SourceRecord _allow_download.
             * @member {"allow_download"|undefined} _allow_download
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            $Object.defineProperty(SourceRecord.prototype, "_allow_download", {
                get: $util.oneOfGetter($oneOfFields = ["allow_download"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SourceRecord _allow_retention.
             * @member {"allow_retention"|undefined} _allow_retention
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            $Object.defineProperty(SourceRecord.prototype, "_allow_retention", {
                get: $util.oneOfGetter($oneOfFields = ["allow_retention"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SourceRecord _byte_count.
             * @member {"byte_count"|undefined} _byte_count
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            $Object.defineProperty(SourceRecord.prototype, "_byte_count", {
                get: $util.oneOfGetter($oneOfFields = ["byte_count"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SourceRecord _content_hash.
             * @member {"content_hash"|undefined} _content_hash
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            $Object.defineProperty(SourceRecord.prototype, "_content_hash", {
                get: $util.oneOfGetter($oneOfFields = ["content_hash"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SourceRecord _filename.
             * @member {"filename"|undefined} _filename
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            $Object.defineProperty(SourceRecord.prototype, "_filename", {
                get: $util.oneOfGetter($oneOfFields = ["filename"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SourceRecord _input_kind.
             * @member {"input_kind"|undefined} _input_kind
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            $Object.defineProperty(SourceRecord.prototype, "_input_kind", {
                get: $util.oneOfGetter($oneOfFields = ["input_kind"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SourceRecord _received_at.
             * @member {"received_at"|undefined} _received_at
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            $Object.defineProperty(SourceRecord.prototype, "_received_at", {
                get: $util.oneOfGetter($oneOfFields = ["received_at"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SourceRecord _source_id.
             * @member {"source_id"|undefined} _source_id
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            $Object.defineProperty(SourceRecord.prototype, "_source_id", {
                get: $util.oneOfGetter($oneOfFields = ["source_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SourceRecord _source_name.
             * @member {"source_name"|undefined} _source_name
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             */
            $Object.defineProperty(SourceRecord.prototype, "_source_name", {
                get: $util.oneOfGetter($oneOfFields = ["source_name"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new SourceRecord instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.SourceRecord
             * @static
             * @param {northstar.data_hub.SourceRecord.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.SourceRecord} SourceRecord instance
             * @type {{
             *   (properties: northstar.data_hub.SourceRecord.$Shape): northstar.data_hub.SourceRecord & northstar.data_hub.SourceRecord.$Shape;
             *   (properties?: northstar.data_hub.SourceRecord.$Properties): northstar.data_hub.SourceRecord;
             * }}
             */
            SourceRecord.create = function(properties) {
                return new SourceRecord(properties);
            };

            /**
             * Encodes the specified SourceRecord message. Does not implicitly {@link northstar.data_hub.SourceRecord.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.SourceRecord
             * @static
             * @param {northstar.data_hub.SourceRecord.$Properties} message SourceRecord message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            SourceRecord.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.allow_download != null && $Object.hasOwnProperty.call(message, "allow_download"))
                    writer.uint32(/* id 1, wireType 0 =*/8).bool(message.allow_download);
                if (message.allow_retention != null && $Object.hasOwnProperty.call(message, "allow_retention"))
                    writer.uint32(/* id 2, wireType 0 =*/16).bool(message.allow_retention);
                if (message.byte_count != null && $Object.hasOwnProperty.call(message, "byte_count"))
                    writer.uint32(/* id 3, wireType 0 =*/24).int64(message.byte_count);
                if (message.content_hash != null && $Object.hasOwnProperty.call(message, "content_hash"))
                    writer.uint32(/* id 4, wireType 2 =*/34).string(message.content_hash);
                if (message.filename != null && $Object.hasOwnProperty.call(message, "filename"))
                    writer.uint32(/* id 5, wireType 2 =*/42).string(message.filename);
                if (message.input_kind != null && $Object.hasOwnProperty.call(message, "input_kind"))
                    writer.uint32(/* id 6, wireType 2 =*/50).string(message.input_kind);
                if (message.received_at != null && $Object.hasOwnProperty.call(message, "received_at"))
                    writer.uint32(/* id 7, wireType 2 =*/58).string(message.received_at);
                if (message.source_id != null && $Object.hasOwnProperty.call(message, "source_id"))
                    writer.uint32(/* id 8, wireType 2 =*/66).string(message.source_id);
                if (message.source_name != null && $Object.hasOwnProperty.call(message, "source_name"))
                    writer.uint32(/* id 9, wireType 2 =*/74).string(message.source_name);
                if (message.evidence_fields != null && $Object.hasOwnProperty.call(message, "evidence_fields"))
                    for (let keys = $Object.keys(message.evidence_fields), i = 0; i < keys.length; ++i) {
                        writer.uint32(/* id 1000, wireType 2 =*/8002).fork().uint32(/* id 1, wireType 2 =*/10).string(keys[i]);
                        $root.google.protobuf.Value.encode(message.evidence_fields[keys[i]], writer.uint32(/* id 2, wireType 2 =*/18).fork(), _depth + 1).ldelim().ldelim();
                    }
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a SourceRecord message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.SourceRecord
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.SourceRecord & northstar.data_hub.SourceRecord.$Shape} SourceRecord
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            SourceRecord.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message, key, value;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.SourceRecord();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 0)
                                break;
                            message.allow_download = reader.bool();
                            message._allow_download = "allow_download";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 0)
                                break;
                            message.allow_retention = reader.bool();
                            message._allow_retention = "allow_retention";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 0)
                                break;
                            message.byte_count = reader.int64();
                            message._byte_count = "byte_count";
                            continue;
                        }
                    case 4: {
                            if (wireType !== 2)
                                break;
                            message.content_hash = reader.stringVerify();
                            message._content_hash = "content_hash";
                            continue;
                        }
                    case 5: {
                            if (wireType !== 2)
                                break;
                            message.filename = reader.stringVerify();
                            message._filename = "filename";
                            continue;
                        }
                    case 6: {
                            if (wireType !== 2)
                                break;
                            message.input_kind = reader.stringVerify();
                            message._input_kind = "input_kind";
                            continue;
                        }
                    case 7: {
                            if (wireType !== 2)
                                break;
                            message.received_at = reader.stringVerify();
                            message._received_at = "received_at";
                            continue;
                        }
                    case 8: {
                            if (wireType !== 2)
                                break;
                            message.source_id = reader.stringVerify();
                            message._source_id = "source_id";
                            continue;
                        }
                    case 9: {
                            if (wireType !== 2)
                                break;
                            message.source_name = reader.stringVerify();
                            message._source_name = "source_name";
                            continue;
                        }
                    case 1000: {
                            if (wireType !== 2)
                                break;
                            if (message.evidence_fields === $util.emptyObject)
                                message.evidence_fields = {};
                            let end2 = reader.uint32() + reader.pos;
                            if (end2 > reader.len)
                                throw $RangeError("index out of range");
                            reader.len = end2;
                            key = "";
                            value = null;
                            while (reader.pos < end2) {
                                let tag2 = reader.tag();
                                wireType = tag2 & 7;
                                switch (tag2 >>>= 3) {
                                case 1:
                                    if (wireType !== 2)
                                        break;
                                    key = reader.stringVerify();
                                    continue;
                                case 2:
                                    if (wireType !== 2)
                                        break;
                                    value = $root.google.protobuf.Value.decode(reader, reader.uint32(), $undefined, _depth + 1, value);
                                    continue;
                                }
                                reader.skipType(wireType, _depth, tag2);
                            }
                            if (reader.pos !== end2)
                                throw $RangeError("index out of range");
                            reader.len = end;
                            if (key === "__proto__")
                                $util.makeProp(message.evidence_fields, key);
                            message.evidence_fields[key] = value || new $root.google.protobuf.Value();
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a SourceRecord message.
             * @function verify
             * @memberof northstar.data_hub.SourceRecord
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            SourceRecord.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.allow_download != null && $Object.hasOwnProperty.call(message, "allow_download")) {
                    properties._allow_download = 1;
                    if (typeof message.allow_download !== "boolean")
                        return "allow_download: boolean expected";
                }
                if (message.allow_retention != null && $Object.hasOwnProperty.call(message, "allow_retention")) {
                    properties._allow_retention = 1;
                    if (typeof message.allow_retention !== "boolean")
                        return "allow_retention: boolean expected";
                }
                if (message.byte_count != null && $Object.hasOwnProperty.call(message, "byte_count")) {
                    properties._byte_count = 1;
                    if (!$util.isInteger(message.byte_count) && !(message.byte_count && $util.isInteger(message.byte_count.low) && $util.isInteger(message.byte_count.high)))
                        return "byte_count: integer|Long expected";
                }
                if (message.content_hash != null && $Object.hasOwnProperty.call(message, "content_hash")) {
                    properties._content_hash = 1;
                    if (!$util.isString(message.content_hash))
                        return "content_hash: string expected";
                }
                if (message.filename != null && $Object.hasOwnProperty.call(message, "filename")) {
                    properties._filename = 1;
                    if (!$util.isString(message.filename))
                        return "filename: string expected";
                }
                if (message.input_kind != null && $Object.hasOwnProperty.call(message, "input_kind")) {
                    properties._input_kind = 1;
                    if (!$util.isString(message.input_kind))
                        return "input_kind: string expected";
                }
                if (message.received_at != null && $Object.hasOwnProperty.call(message, "received_at")) {
                    properties._received_at = 1;
                    if (!$util.isString(message.received_at))
                        return "received_at: string expected";
                }
                if (message.source_id != null && $Object.hasOwnProperty.call(message, "source_id")) {
                    properties._source_id = 1;
                    if (!$util.isString(message.source_id))
                        return "source_id: string expected";
                }
                if (message.source_name != null && $Object.hasOwnProperty.call(message, "source_name")) {
                    properties._source_name = 1;
                    if (!$util.isString(message.source_name))
                        return "source_name: string expected";
                }
                if (message.evidence_fields != null && $Object.hasOwnProperty.call(message, "evidence_fields")) {
                    if (!$util.isObject(message.evidence_fields))
                        return "evidence_fields: object expected";
                    let key = $Object.keys(message.evidence_fields);
                    for (let i = 0; i < key.length; ++i) {
                        let error = $root.google.protobuf.Value.verify(message.evidence_fields[key[i]], _depth + 1);
                        if (error)
                            return "evidence_fields." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a SourceRecord message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.SourceRecord
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.SourceRecord} SourceRecord
             */
            SourceRecord.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.SourceRecord)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.SourceRecord: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.SourceRecord();
                if (object.allow_download != null)
                    message.allow_download = $Boolean(object.allow_download);
                if (object.allow_retention != null)
                    message.allow_retention = $Boolean(object.allow_retention);
                if (object.byte_count != null)
                    if ($util.Long)
                        message.byte_count = $util.Long.fromValue(object.byte_count, false);
                    else if (typeof object.byte_count === "string")
                        message.byte_count = $parseInt(object.byte_count, 10);
                    else if (typeof object.byte_count === "number")
                        message.byte_count = object.byte_count;
                    else if (typeof object.byte_count === "object")
                        message.byte_count = new $util.LongBits(object.byte_count.low >>> 0, object.byte_count.high >>> 0).toNumber();
                if (object.content_hash != null)
                    message.content_hash = $String(object.content_hash);
                if (object.filename != null)
                    message.filename = $String(object.filename);
                if (object.input_kind != null)
                    message.input_kind = $String(object.input_kind);
                if (object.received_at != null)
                    message.received_at = $String(object.received_at);
                if (object.source_id != null)
                    message.source_id = $String(object.source_id);
                if (object.source_name != null)
                    message.source_name = $String(object.source_name);
                if (object.evidence_fields) {
                    if (!$util.isObject(object.evidence_fields))
                        throw $TypeError(".northstar.data_hub.SourceRecord.evidence_fields: object expected");
                    message.evidence_fields = {};
                    for (let keys = $Object.keys(object.evidence_fields), i = 0; i < keys.length; ++i) {
                        if (keys[i] === "__proto__")
                            $util.makeProp(message.evidence_fields, keys[i]);
                        if (!$util.isObject(object.evidence_fields[keys[i]]))
                            throw $TypeError(".northstar.data_hub.SourceRecord.evidence_fields: object expected");
                        message.evidence_fields[keys[i]] = $root.google.protobuf.Value.fromObject(object.evidence_fields[keys[i]], _depth + 1);
                    }
                }
                return message;
            };

            /**
             * Creates a plain object from a SourceRecord message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.SourceRecord
             * @static
             * @param {northstar.data_hub.SourceRecord} message SourceRecord
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            SourceRecord.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.objects || options.defaults)
                    object.evidence_fields = {};
                if (message.allow_download != null && $Object.hasOwnProperty.call(message, "allow_download")) {
                    object.allow_download = message.allow_download;
                    if (options.oneofs)
                        object._allow_download = "allow_download";
                }
                if (message.allow_retention != null && $Object.hasOwnProperty.call(message, "allow_retention")) {
                    object.allow_retention = message.allow_retention;
                    if (options.oneofs)
                        object._allow_retention = "allow_retention";
                }
                if (message.byte_count != null && $Object.hasOwnProperty.call(message, "byte_count")) {
                    if (typeof $BigInt !== "undefined" && options.longs === $BigInt)
                        object.byte_count = typeof message.byte_count === "number" ? $BigInt(message.byte_count) : $util.Long.fromBits(message.byte_count.low >>> 0, message.byte_count.high >>> 0, false).toBigInt();
                    else if (typeof message.byte_count === "number")
                        object.byte_count = options.longs === $String ? $String(message.byte_count) : message.byte_count;
                    else
                        object.byte_count = options.longs === $String ? $util.Long.prototype.toString.call(message.byte_count) : options.longs === $Number ? new $util.LongBits(message.byte_count.low >>> 0, message.byte_count.high >>> 0).toNumber() : message.byte_count;
                    if (options.oneofs)
                        object._byte_count = "byte_count";
                }
                if (message.content_hash != null && $Object.hasOwnProperty.call(message, "content_hash")) {
                    object.content_hash = message.content_hash;
                    if (options.oneofs)
                        object._content_hash = "content_hash";
                }
                if (message.filename != null && $Object.hasOwnProperty.call(message, "filename")) {
                    object.filename = message.filename;
                    if (options.oneofs)
                        object._filename = "filename";
                }
                if (message.input_kind != null && $Object.hasOwnProperty.call(message, "input_kind")) {
                    object.input_kind = message.input_kind;
                    if (options.oneofs)
                        object._input_kind = "input_kind";
                }
                if (message.received_at != null && $Object.hasOwnProperty.call(message, "received_at")) {
                    object.received_at = message.received_at;
                    if (options.oneofs)
                        object._received_at = "received_at";
                }
                if (message.source_id != null && $Object.hasOwnProperty.call(message, "source_id")) {
                    object.source_id = message.source_id;
                    if (options.oneofs)
                        object._source_id = "source_id";
                }
                if (message.source_name != null && $Object.hasOwnProperty.call(message, "source_name")) {
                    object.source_name = message.source_name;
                    if (options.oneofs)
                        object._source_name = "source_name";
                }
                let keys2;
                if (message.evidence_fields && (keys2 = $Object.keys(message.evidence_fields)).length) {
                    object.evidence_fields = {};
                    for (let j = 0; j < keys2.length; ++j) {
                        if (keys2[j] === "__proto__")
                            $util.makeProp(object.evidence_fields, keys2[j]);
                        object.evidence_fields[keys2[j]] = $root.google.protobuf.Value.toObject(message.evidence_fields[keys2[j]], options, _depth + 1);
                    }
                }
                return object;
            };

            /**
             * Converts this SourceRecord to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.SourceRecord
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            SourceRecord.prototype.toJSON = function() {
                return SourceRecord.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for SourceRecord
             * @function getTypeUrl
             * @memberof northstar.data_hub.SourceRecord
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            SourceRecord.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.SourceRecord";
            };

            return SourceRecord;
        })();

        data_hub.GetApiAttemptsResponse = (function() {

            /**
             * Properties of a GetApiAttemptsResponse.
             * @typedef {Object} northstar.data_hub.GetApiAttemptsResponse.$Properties
             * @property {Array.<northstar.data_hub.ProcessingAttempt.$Properties>|null} [items] GetApiAttemptsResponse items
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a GetApiAttemptsResponse.
             * @memberof northstar.data_hub
             * @interface IGetApiAttemptsResponse
             * @augments northstar.data_hub.GetApiAttemptsResponse.$Properties
             * @deprecated Use northstar.data_hub.GetApiAttemptsResponse.$Properties instead.
             */

            /**
             * Shape of a GetApiAttemptsResponse.
             * @typedef {{
             *   items?: Array.<northstar.data_hub.ProcessingAttempt.$Shape>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * }} northstar.data_hub.GetApiAttemptsResponse.$Shape
             */

            /**
             * Constructs a new GetApiAttemptsResponse.
             * @memberof northstar.data_hub
             * @classdesc Represents a GetApiAttemptsResponse.
             * @constructor
             * @param {northstar.data_hub.GetApiAttemptsResponse.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const GetApiAttemptsResponse = function (properties) {
                this.items = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * GetApiAttemptsResponse items.
             * @member {Array.<northstar.data_hub.ProcessingAttempt.$Properties>} items
             * @memberof northstar.data_hub.GetApiAttemptsResponse
             * @instance
             */
            GetApiAttemptsResponse.prototype.items = $util.emptyArray;

            /**
             * Creates a new GetApiAttemptsResponse instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.GetApiAttemptsResponse
             * @static
             * @param {northstar.data_hub.GetApiAttemptsResponse.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.GetApiAttemptsResponse} GetApiAttemptsResponse instance
             * @type {{
             *   (properties: northstar.data_hub.GetApiAttemptsResponse.$Shape): northstar.data_hub.GetApiAttemptsResponse & northstar.data_hub.GetApiAttemptsResponse.$Shape;
             *   (properties?: northstar.data_hub.GetApiAttemptsResponse.$Properties): northstar.data_hub.GetApiAttemptsResponse;
             * }}
             */
            GetApiAttemptsResponse.create = function(properties) {
                return new GetApiAttemptsResponse(properties);
            };

            /**
             * Encodes the specified GetApiAttemptsResponse message. Does not implicitly {@link northstar.data_hub.GetApiAttemptsResponse.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.GetApiAttemptsResponse
             * @static
             * @param {northstar.data_hub.GetApiAttemptsResponse.$Properties} message GetApiAttemptsResponse message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            GetApiAttemptsResponse.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.items != null && message.items.length)
                    for (let i = 0; i < message.items.length; ++i)
                        $root.northstar.data_hub.ProcessingAttempt.encode(message.items[i], writer.uint32(/* id 1, wireType 2 =*/10).fork(), _depth + 1).ldelim();
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a GetApiAttemptsResponse message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.GetApiAttemptsResponse
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.GetApiAttemptsResponse & northstar.data_hub.GetApiAttemptsResponse.$Shape} GetApiAttemptsResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            GetApiAttemptsResponse.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.GetApiAttemptsResponse();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            if (!(message.items && message.items.length))
                                message.items = [];
                            message.items.push($root.northstar.data_hub.ProcessingAttempt.decode(reader, reader.uint32(), $undefined, _depth + 1));
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a GetApiAttemptsResponse message.
             * @function verify
             * @memberof northstar.data_hub.GetApiAttemptsResponse
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            GetApiAttemptsResponse.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                if (message.items != null && $Object.hasOwnProperty.call(message, "items")) {
                    if (!$Array.isArray(message.items))
                        return "items: array expected";
                    for (let i = 0; i < message.items.length; ++i) {
                        let error = $root.northstar.data_hub.ProcessingAttempt.verify(message.items[i], _depth + 1);
                        if (error)
                            return "items." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a GetApiAttemptsResponse message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.GetApiAttemptsResponse
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.GetApiAttemptsResponse} GetApiAttemptsResponse
             */
            GetApiAttemptsResponse.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.GetApiAttemptsResponse)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.GetApiAttemptsResponse: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.GetApiAttemptsResponse();
                if (object.items) {
                    if (!$Array.isArray(object.items))
                        throw $TypeError(".northstar.data_hub.GetApiAttemptsResponse.items: array expected");
                    message.items = $Array(object.items.length);
                    for (let i = 0; i < object.items.length; ++i) {
                        if (!$util.isObject(object.items[i]))
                            throw $TypeError(".northstar.data_hub.GetApiAttemptsResponse.items: object expected");
                        message.items[i] = $root.northstar.data_hub.ProcessingAttempt.fromObject(object.items[i], _depth + 1);
                    }
                }
                return message;
            };

            /**
             * Creates a plain object from a GetApiAttemptsResponse message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.GetApiAttemptsResponse
             * @static
             * @param {northstar.data_hub.GetApiAttemptsResponse} message GetApiAttemptsResponse
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            GetApiAttemptsResponse.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults)
                    object.items = [];
                if (message.items && message.items.length) {
                    object.items = $Array(message.items.length);
                    for (let j = 0; j < message.items.length; ++j)
                        object.items[j] = $root.northstar.data_hub.ProcessingAttempt.toObject(message.items[j], options, _depth + 1);
                }
                return object;
            };

            /**
             * Converts this GetApiAttemptsResponse to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.GetApiAttemptsResponse
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            GetApiAttemptsResponse.prototype.toJSON = function() {
                return GetApiAttemptsResponse.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for GetApiAttemptsResponse
             * @function getTypeUrl
             * @memberof northstar.data_hub.GetApiAttemptsResponse
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            GetApiAttemptsResponse.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.GetApiAttemptsResponse";
            };

            return GetApiAttemptsResponse;
        })();

        data_hub.GetApiDatasetsResponse = (function() {

            /**
             * Properties of a GetApiDatasetsResponse.
             * @typedef {Object} northstar.data_hub.GetApiDatasetsResponse.$Properties
             * @property {Array.<northstar.data_hub.DatasetSummary.$Properties>|null} [items] GetApiDatasetsResponse items
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a GetApiDatasetsResponse.
             * @memberof northstar.data_hub
             * @interface IGetApiDatasetsResponse
             * @augments northstar.data_hub.GetApiDatasetsResponse.$Properties
             * @deprecated Use northstar.data_hub.GetApiDatasetsResponse.$Properties instead.
             */

            /**
             * Shape of a GetApiDatasetsResponse.
             * @typedef {{
             *   items?: Array.<northstar.data_hub.DatasetSummary.$Shape>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * }} northstar.data_hub.GetApiDatasetsResponse.$Shape
             */

            /**
             * Constructs a new GetApiDatasetsResponse.
             * @memberof northstar.data_hub
             * @classdesc Represents a GetApiDatasetsResponse.
             * @constructor
             * @param {northstar.data_hub.GetApiDatasetsResponse.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const GetApiDatasetsResponse = function (properties) {
                this.items = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * GetApiDatasetsResponse items.
             * @member {Array.<northstar.data_hub.DatasetSummary.$Properties>} items
             * @memberof northstar.data_hub.GetApiDatasetsResponse
             * @instance
             */
            GetApiDatasetsResponse.prototype.items = $util.emptyArray;

            /**
             * Creates a new GetApiDatasetsResponse instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.GetApiDatasetsResponse
             * @static
             * @param {northstar.data_hub.GetApiDatasetsResponse.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.GetApiDatasetsResponse} GetApiDatasetsResponse instance
             * @type {{
             *   (properties: northstar.data_hub.GetApiDatasetsResponse.$Shape): northstar.data_hub.GetApiDatasetsResponse & northstar.data_hub.GetApiDatasetsResponse.$Shape;
             *   (properties?: northstar.data_hub.GetApiDatasetsResponse.$Properties): northstar.data_hub.GetApiDatasetsResponse;
             * }}
             */
            GetApiDatasetsResponse.create = function(properties) {
                return new GetApiDatasetsResponse(properties);
            };

            /**
             * Encodes the specified GetApiDatasetsResponse message. Does not implicitly {@link northstar.data_hub.GetApiDatasetsResponse.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.GetApiDatasetsResponse
             * @static
             * @param {northstar.data_hub.GetApiDatasetsResponse.$Properties} message GetApiDatasetsResponse message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            GetApiDatasetsResponse.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.items != null && message.items.length)
                    for (let i = 0; i < message.items.length; ++i)
                        $root.northstar.data_hub.DatasetSummary.encode(message.items[i], writer.uint32(/* id 1, wireType 2 =*/10).fork(), _depth + 1).ldelim();
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a GetApiDatasetsResponse message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.GetApiDatasetsResponse
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.GetApiDatasetsResponse & northstar.data_hub.GetApiDatasetsResponse.$Shape} GetApiDatasetsResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            GetApiDatasetsResponse.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.GetApiDatasetsResponse();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            if (!(message.items && message.items.length))
                                message.items = [];
                            message.items.push($root.northstar.data_hub.DatasetSummary.decode(reader, reader.uint32(), $undefined, _depth + 1));
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a GetApiDatasetsResponse message.
             * @function verify
             * @memberof northstar.data_hub.GetApiDatasetsResponse
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            GetApiDatasetsResponse.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                if (message.items != null && $Object.hasOwnProperty.call(message, "items")) {
                    if (!$Array.isArray(message.items))
                        return "items: array expected";
                    for (let i = 0; i < message.items.length; ++i) {
                        let error = $root.northstar.data_hub.DatasetSummary.verify(message.items[i], _depth + 1);
                        if (error)
                            return "items." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a GetApiDatasetsResponse message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.GetApiDatasetsResponse
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.GetApiDatasetsResponse} GetApiDatasetsResponse
             */
            GetApiDatasetsResponse.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.GetApiDatasetsResponse)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.GetApiDatasetsResponse: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.GetApiDatasetsResponse();
                if (object.items) {
                    if (!$Array.isArray(object.items))
                        throw $TypeError(".northstar.data_hub.GetApiDatasetsResponse.items: array expected");
                    message.items = $Array(object.items.length);
                    for (let i = 0; i < object.items.length; ++i) {
                        if (!$util.isObject(object.items[i]))
                            throw $TypeError(".northstar.data_hub.GetApiDatasetsResponse.items: object expected");
                        message.items[i] = $root.northstar.data_hub.DatasetSummary.fromObject(object.items[i], _depth + 1);
                    }
                }
                return message;
            };

            /**
             * Creates a plain object from a GetApiDatasetsResponse message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.GetApiDatasetsResponse
             * @static
             * @param {northstar.data_hub.GetApiDatasetsResponse} message GetApiDatasetsResponse
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            GetApiDatasetsResponse.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults)
                    object.items = [];
                if (message.items && message.items.length) {
                    object.items = $Array(message.items.length);
                    for (let j = 0; j < message.items.length; ++j)
                        object.items[j] = $root.northstar.data_hub.DatasetSummary.toObject(message.items[j], options, _depth + 1);
                }
                return object;
            };

            /**
             * Converts this GetApiDatasetsResponse to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.GetApiDatasetsResponse
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            GetApiDatasetsResponse.prototype.toJSON = function() {
                return GetApiDatasetsResponse.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for GetApiDatasetsResponse
             * @function getTypeUrl
             * @memberof northstar.data_hub.GetApiDatasetsResponse
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            GetApiDatasetsResponse.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.GetApiDatasetsResponse";
            };

            return GetApiDatasetsResponse;
        })();

        data_hub.GetApiRejectionsResponse = (function() {

            /**
             * Properties of a GetApiRejectionsResponse.
             * @typedef {Object} northstar.data_hub.GetApiRejectionsResponse.$Properties
             * @property {Array.<northstar.data_hub.AdmissionRejection.$Properties>|null} [items] GetApiRejectionsResponse items
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a GetApiRejectionsResponse.
             * @memberof northstar.data_hub
             * @interface IGetApiRejectionsResponse
             * @augments northstar.data_hub.GetApiRejectionsResponse.$Properties
             * @deprecated Use northstar.data_hub.GetApiRejectionsResponse.$Properties instead.
             */

            /**
             * Shape of a GetApiRejectionsResponse.
             * @typedef {{
             *   items?: Array.<northstar.data_hub.AdmissionRejection.$Shape>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * }} northstar.data_hub.GetApiRejectionsResponse.$Shape
             */

            /**
             * Constructs a new GetApiRejectionsResponse.
             * @memberof northstar.data_hub
             * @classdesc Represents a GetApiRejectionsResponse.
             * @constructor
             * @param {northstar.data_hub.GetApiRejectionsResponse.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const GetApiRejectionsResponse = function (properties) {
                this.items = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * GetApiRejectionsResponse items.
             * @member {Array.<northstar.data_hub.AdmissionRejection.$Properties>} items
             * @memberof northstar.data_hub.GetApiRejectionsResponse
             * @instance
             */
            GetApiRejectionsResponse.prototype.items = $util.emptyArray;

            /**
             * Creates a new GetApiRejectionsResponse instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.GetApiRejectionsResponse
             * @static
             * @param {northstar.data_hub.GetApiRejectionsResponse.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.GetApiRejectionsResponse} GetApiRejectionsResponse instance
             * @type {{
             *   (properties: northstar.data_hub.GetApiRejectionsResponse.$Shape): northstar.data_hub.GetApiRejectionsResponse & northstar.data_hub.GetApiRejectionsResponse.$Shape;
             *   (properties?: northstar.data_hub.GetApiRejectionsResponse.$Properties): northstar.data_hub.GetApiRejectionsResponse;
             * }}
             */
            GetApiRejectionsResponse.create = function(properties) {
                return new GetApiRejectionsResponse(properties);
            };

            /**
             * Encodes the specified GetApiRejectionsResponse message. Does not implicitly {@link northstar.data_hub.GetApiRejectionsResponse.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.GetApiRejectionsResponse
             * @static
             * @param {northstar.data_hub.GetApiRejectionsResponse.$Properties} message GetApiRejectionsResponse message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            GetApiRejectionsResponse.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.items != null && message.items.length)
                    for (let i = 0; i < message.items.length; ++i)
                        $root.northstar.data_hub.AdmissionRejection.encode(message.items[i], writer.uint32(/* id 1, wireType 2 =*/10).fork(), _depth + 1).ldelim();
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a GetApiRejectionsResponse message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.GetApiRejectionsResponse
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.GetApiRejectionsResponse & northstar.data_hub.GetApiRejectionsResponse.$Shape} GetApiRejectionsResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            GetApiRejectionsResponse.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.GetApiRejectionsResponse();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            if (!(message.items && message.items.length))
                                message.items = [];
                            message.items.push($root.northstar.data_hub.AdmissionRejection.decode(reader, reader.uint32(), $undefined, _depth + 1));
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a GetApiRejectionsResponse message.
             * @function verify
             * @memberof northstar.data_hub.GetApiRejectionsResponse
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            GetApiRejectionsResponse.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                if (message.items != null && $Object.hasOwnProperty.call(message, "items")) {
                    if (!$Array.isArray(message.items))
                        return "items: array expected";
                    for (let i = 0; i < message.items.length; ++i) {
                        let error = $root.northstar.data_hub.AdmissionRejection.verify(message.items[i], _depth + 1);
                        if (error)
                            return "items." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a GetApiRejectionsResponse message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.GetApiRejectionsResponse
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.GetApiRejectionsResponse} GetApiRejectionsResponse
             */
            GetApiRejectionsResponse.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.GetApiRejectionsResponse)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.GetApiRejectionsResponse: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.GetApiRejectionsResponse();
                if (object.items) {
                    if (!$Array.isArray(object.items))
                        throw $TypeError(".northstar.data_hub.GetApiRejectionsResponse.items: array expected");
                    message.items = $Array(object.items.length);
                    for (let i = 0; i < object.items.length; ++i) {
                        if (!$util.isObject(object.items[i]))
                            throw $TypeError(".northstar.data_hub.GetApiRejectionsResponse.items: object expected");
                        message.items[i] = $root.northstar.data_hub.AdmissionRejection.fromObject(object.items[i], _depth + 1);
                    }
                }
                return message;
            };

            /**
             * Creates a plain object from a GetApiRejectionsResponse message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.GetApiRejectionsResponse
             * @static
             * @param {northstar.data_hub.GetApiRejectionsResponse} message GetApiRejectionsResponse
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            GetApiRejectionsResponse.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults)
                    object.items = [];
                if (message.items && message.items.length) {
                    object.items = $Array(message.items.length);
                    for (let j = 0; j < message.items.length; ++j)
                        object.items[j] = $root.northstar.data_hub.AdmissionRejection.toObject(message.items[j], options, _depth + 1);
                }
                return object;
            };

            /**
             * Converts this GetApiRejectionsResponse to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.GetApiRejectionsResponse
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            GetApiRejectionsResponse.prototype.toJSON = function() {
                return GetApiRejectionsResponse.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for GetApiRejectionsResponse
             * @function getTypeUrl
             * @memberof northstar.data_hub.GetApiRejectionsResponse
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            GetApiRejectionsResponse.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.GetApiRejectionsResponse";
            };

            return GetApiRejectionsResponse;
        })();

        data_hub.GetApiSourcesResponse = (function() {

            /**
             * Properties of a GetApiSourcesResponse.
             * @typedef {Object} northstar.data_hub.GetApiSourcesResponse.$Properties
             * @property {Array.<northstar.data_hub.SourceRecord.$Properties>|null} [items] GetApiSourcesResponse items
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a GetApiSourcesResponse.
             * @memberof northstar.data_hub
             * @interface IGetApiSourcesResponse
             * @augments northstar.data_hub.GetApiSourcesResponse.$Properties
             * @deprecated Use northstar.data_hub.GetApiSourcesResponse.$Properties instead.
             */

            /**
             * Shape of a GetApiSourcesResponse.
             * @typedef {{
             *   items?: Array.<northstar.data_hub.SourceRecord.$Shape>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * }} northstar.data_hub.GetApiSourcesResponse.$Shape
             */

            /**
             * Constructs a new GetApiSourcesResponse.
             * @memberof northstar.data_hub
             * @classdesc Represents a GetApiSourcesResponse.
             * @constructor
             * @param {northstar.data_hub.GetApiSourcesResponse.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const GetApiSourcesResponse = function (properties) {
                this.items = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * GetApiSourcesResponse items.
             * @member {Array.<northstar.data_hub.SourceRecord.$Properties>} items
             * @memberof northstar.data_hub.GetApiSourcesResponse
             * @instance
             */
            GetApiSourcesResponse.prototype.items = $util.emptyArray;

            /**
             * Creates a new GetApiSourcesResponse instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.GetApiSourcesResponse
             * @static
             * @param {northstar.data_hub.GetApiSourcesResponse.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.GetApiSourcesResponse} GetApiSourcesResponse instance
             * @type {{
             *   (properties: northstar.data_hub.GetApiSourcesResponse.$Shape): northstar.data_hub.GetApiSourcesResponse & northstar.data_hub.GetApiSourcesResponse.$Shape;
             *   (properties?: northstar.data_hub.GetApiSourcesResponse.$Properties): northstar.data_hub.GetApiSourcesResponse;
             * }}
             */
            GetApiSourcesResponse.create = function(properties) {
                return new GetApiSourcesResponse(properties);
            };

            /**
             * Encodes the specified GetApiSourcesResponse message. Does not implicitly {@link northstar.data_hub.GetApiSourcesResponse.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.GetApiSourcesResponse
             * @static
             * @param {northstar.data_hub.GetApiSourcesResponse.$Properties} message GetApiSourcesResponse message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            GetApiSourcesResponse.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.items != null && message.items.length)
                    for (let i = 0; i < message.items.length; ++i)
                        $root.northstar.data_hub.SourceRecord.encode(message.items[i], writer.uint32(/* id 1, wireType 2 =*/10).fork(), _depth + 1).ldelim();
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a GetApiSourcesResponse message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.GetApiSourcesResponse
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.GetApiSourcesResponse & northstar.data_hub.GetApiSourcesResponse.$Shape} GetApiSourcesResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            GetApiSourcesResponse.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.GetApiSourcesResponse();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            if (!(message.items && message.items.length))
                                message.items = [];
                            message.items.push($root.northstar.data_hub.SourceRecord.decode(reader, reader.uint32(), $undefined, _depth + 1));
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a GetApiSourcesResponse message.
             * @function verify
             * @memberof northstar.data_hub.GetApiSourcesResponse
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            GetApiSourcesResponse.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                if (message.items != null && $Object.hasOwnProperty.call(message, "items")) {
                    if (!$Array.isArray(message.items))
                        return "items: array expected";
                    for (let i = 0; i < message.items.length; ++i) {
                        let error = $root.northstar.data_hub.SourceRecord.verify(message.items[i], _depth + 1);
                        if (error)
                            return "items." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a GetApiSourcesResponse message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.GetApiSourcesResponse
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.GetApiSourcesResponse} GetApiSourcesResponse
             */
            GetApiSourcesResponse.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.GetApiSourcesResponse)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.GetApiSourcesResponse: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.GetApiSourcesResponse();
                if (object.items) {
                    if (!$Array.isArray(object.items))
                        throw $TypeError(".northstar.data_hub.GetApiSourcesResponse.items: array expected");
                    message.items = $Array(object.items.length);
                    for (let i = 0; i < object.items.length; ++i) {
                        if (!$util.isObject(object.items[i]))
                            throw $TypeError(".northstar.data_hub.GetApiSourcesResponse.items: object expected");
                        message.items[i] = $root.northstar.data_hub.SourceRecord.fromObject(object.items[i], _depth + 1);
                    }
                }
                return message;
            };

            /**
             * Creates a plain object from a GetApiSourcesResponse message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.GetApiSourcesResponse
             * @static
             * @param {northstar.data_hub.GetApiSourcesResponse} message GetApiSourcesResponse
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            GetApiSourcesResponse.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults)
                    object.items = [];
                if (message.items && message.items.length) {
                    object.items = $Array(message.items.length);
                    for (let j = 0; j < message.items.length; ++j)
                        object.items[j] = $root.northstar.data_hub.SourceRecord.toObject(message.items[j], options, _depth + 1);
                }
                return object;
            };

            /**
             * Converts this GetApiSourcesResponse to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.GetApiSourcesResponse
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            GetApiSourcesResponse.prototype.toJSON = function() {
                return GetApiSourcesResponse.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for GetApiSourcesResponse
             * @function getTypeUrl
             * @memberof northstar.data_hub.GetApiSourcesResponse
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            GetApiSourcesResponse.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.GetApiSourcesResponse";
            };

            return GetApiSourcesResponse;
        })();

        data_hub.ProcessingQueueStatus = (function() {

            /**
             * Properties of a ProcessingQueueStatus.
             * @typedef {Object} northstar.data_hub.ProcessingQueueStatus.$Properties
             * @property {string|null} [observed_at] ProcessingQueueStatus observed_at
             * @property {number|Long|null} [total] ProcessingQueueStatus total
             * @property {number|Long|null} [pending] ProcessingQueueStatus pending
             * @property {number|Long|null} [running] ProcessingQueueStatus running
             * @property {number|Long|null} [published] ProcessingQueueStatus published
             * @property {number|Long|null} [failed] ProcessingQueueStatus failed
             * @property {string|null} [oldest_pending_id] ProcessingQueueStatus oldest_pending_id
             * @property {string|null} [oldest_pending_at] ProcessingQueueStatus oldest_pending_at
             * @property {number|Long|null} [oldest_pending_seconds] ProcessingQueueStatus oldest_pending_seconds
             * @property {Array.<string>|null} [null_fields] ProcessingQueueStatus null_fields
             * @property {"observed_at"} [_observed_at] ProcessingQueueStatus _observed_at
             * @property {"total"} [_total] ProcessingQueueStatus _total
             * @property {"pending"} [_pending] ProcessingQueueStatus _pending
             * @property {"running"} [_running] ProcessingQueueStatus _running
             * @property {"published"} [_published] ProcessingQueueStatus _published
             * @property {"failed"} [_failed] ProcessingQueueStatus _failed
             * @property {"oldest_pending_id"} [_oldest_pending_id] ProcessingQueueStatus _oldest_pending_id
             * @property {"oldest_pending_at"} [_oldest_pending_at] ProcessingQueueStatus _oldest_pending_at
             * @property {"oldest_pending_seconds"} [_oldest_pending_seconds] ProcessingQueueStatus _oldest_pending_seconds
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a ProcessingQueueStatus.
             * @memberof northstar.data_hub
             * @interface IProcessingQueueStatus
             * @augments northstar.data_hub.ProcessingQueueStatus.$Properties
             * @deprecated Use northstar.data_hub.ProcessingQueueStatus.$Properties instead.
             */

            /**
             * Narrowed shape of a ProcessingQueueStatus.
             * @typedef {{
             *   observed_at?: string|null;
             *   total?: number|Long|null;
             *   pending?: number|Long|null;
             *   running?: number|Long|null;
             *   published?: number|Long|null;
             *   failed?: number|Long|null;
             *   oldest_pending_id?: string|null;
             *   oldest_pending_at?: string|null;
             *   oldest_pending_seconds?: number|Long|null;
             *   null_fields?: Array.<string>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _observed_at?: undefined; observed_at?: null }|{ _observed_at?: "observed_at"; observed_at: string })
             * ) & (
             *   ({ _total?: undefined; total?: null }|{ _total?: "total"; total: number|Long })
             * ) & (
             *   ({ _pending?: undefined; pending?: null }|{ _pending?: "pending"; pending: number|Long })
             * ) & (
             *   ({ _running?: undefined; running?: null }|{ _running?: "running"; running: number|Long })
             * ) & (
             *   ({ _published?: undefined; published?: null }|{ _published?: "published"; published: number|Long })
             * ) & (
             *   ({ _failed?: undefined; failed?: null }|{ _failed?: "failed"; failed: number|Long })
             * ) & (
             *   ({ _oldest_pending_id?: undefined; oldest_pending_id?: null }|{ _oldest_pending_id?: "oldest_pending_id"; oldest_pending_id: string })
             * ) & (
             *   ({ _oldest_pending_at?: undefined; oldest_pending_at?: null }|{ _oldest_pending_at?: "oldest_pending_at"; oldest_pending_at: string })
             * ) & (
             *   ({ _oldest_pending_seconds?: undefined; oldest_pending_seconds?: null }|{ _oldest_pending_seconds?: "oldest_pending_seconds"; oldest_pending_seconds: number|Long })
             * )} northstar.data_hub.ProcessingQueueStatus.$Shape
             */

            /**
             * Constructs a new ProcessingQueueStatus.
             * @memberof northstar.data_hub
             * @classdesc Represents a ProcessingQueueStatus.
             * @constructor
             * @param {northstar.data_hub.ProcessingQueueStatus.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const ProcessingQueueStatus = function (properties) {
                this.null_fields = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * ProcessingQueueStatus observed_at.
             * @member {string|null|undefined} observed_at
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            ProcessingQueueStatus.prototype.observed_at = null;

            /**
             * ProcessingQueueStatus total.
             * @member {number|Long|null|undefined} total
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            ProcessingQueueStatus.prototype.total = null;

            /**
             * ProcessingQueueStatus pending.
             * @member {number|Long|null|undefined} pending
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            ProcessingQueueStatus.prototype.pending = null;

            /**
             * ProcessingQueueStatus running.
             * @member {number|Long|null|undefined} running
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            ProcessingQueueStatus.prototype.running = null;

            /**
             * ProcessingQueueStatus published.
             * @member {number|Long|null|undefined} published
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            ProcessingQueueStatus.prototype.published = null;

            /**
             * ProcessingQueueStatus failed.
             * @member {number|Long|null|undefined} failed
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            ProcessingQueueStatus.prototype.failed = null;

            /**
             * ProcessingQueueStatus oldest_pending_id.
             * @member {string|null|undefined} oldest_pending_id
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            ProcessingQueueStatus.prototype.oldest_pending_id = null;

            /**
             * ProcessingQueueStatus oldest_pending_at.
             * @member {string|null|undefined} oldest_pending_at
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            ProcessingQueueStatus.prototype.oldest_pending_at = null;

            /**
             * ProcessingQueueStatus oldest_pending_seconds.
             * @member {number|Long|null|undefined} oldest_pending_seconds
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            ProcessingQueueStatus.prototype.oldest_pending_seconds = null;

            /**
             * ProcessingQueueStatus null_fields.
             * @member {Array.<string>} null_fields
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            ProcessingQueueStatus.prototype.null_fields = $util.emptyArray;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * ProcessingQueueStatus _observed_at.
             * @member {"observed_at"|undefined} _observed_at
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            $Object.defineProperty(ProcessingQueueStatus.prototype, "_observed_at", {
                get: $util.oneOfGetter($oneOfFields = ["observed_at"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingQueueStatus _total.
             * @member {"total"|undefined} _total
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            $Object.defineProperty(ProcessingQueueStatus.prototype, "_total", {
                get: $util.oneOfGetter($oneOfFields = ["total"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingQueueStatus _pending.
             * @member {"pending"|undefined} _pending
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            $Object.defineProperty(ProcessingQueueStatus.prototype, "_pending", {
                get: $util.oneOfGetter($oneOfFields = ["pending"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingQueueStatus _running.
             * @member {"running"|undefined} _running
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            $Object.defineProperty(ProcessingQueueStatus.prototype, "_running", {
                get: $util.oneOfGetter($oneOfFields = ["running"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingQueueStatus _published.
             * @member {"published"|undefined} _published
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            $Object.defineProperty(ProcessingQueueStatus.prototype, "_published", {
                get: $util.oneOfGetter($oneOfFields = ["published"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingQueueStatus _failed.
             * @member {"failed"|undefined} _failed
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            $Object.defineProperty(ProcessingQueueStatus.prototype, "_failed", {
                get: $util.oneOfGetter($oneOfFields = ["failed"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingQueueStatus _oldest_pending_id.
             * @member {"oldest_pending_id"|undefined} _oldest_pending_id
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            $Object.defineProperty(ProcessingQueueStatus.prototype, "_oldest_pending_id", {
                get: $util.oneOfGetter($oneOfFields = ["oldest_pending_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingQueueStatus _oldest_pending_at.
             * @member {"oldest_pending_at"|undefined} _oldest_pending_at
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            $Object.defineProperty(ProcessingQueueStatus.prototype, "_oldest_pending_at", {
                get: $util.oneOfGetter($oneOfFields = ["oldest_pending_at"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * ProcessingQueueStatus _oldest_pending_seconds.
             * @member {"oldest_pending_seconds"|undefined} _oldest_pending_seconds
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             */
            $Object.defineProperty(ProcessingQueueStatus.prototype, "_oldest_pending_seconds", {
                get: $util.oneOfGetter($oneOfFields = ["oldest_pending_seconds"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new ProcessingQueueStatus instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @static
             * @param {northstar.data_hub.ProcessingQueueStatus.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.ProcessingQueueStatus} ProcessingQueueStatus instance
             * @type {{
             *   (properties: northstar.data_hub.ProcessingQueueStatus.$Shape): northstar.data_hub.ProcessingQueueStatus & northstar.data_hub.ProcessingQueueStatus.$Shape;
             *   (properties?: northstar.data_hub.ProcessingQueueStatus.$Properties): northstar.data_hub.ProcessingQueueStatus;
             * }}
             */
            ProcessingQueueStatus.create = function(properties) {
                return new ProcessingQueueStatus(properties);
            };

            /**
             * Encodes the specified ProcessingQueueStatus message. Does not implicitly {@link northstar.data_hub.ProcessingQueueStatus.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @static
             * @param {northstar.data_hub.ProcessingQueueStatus.$Properties} message ProcessingQueueStatus message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            ProcessingQueueStatus.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.observed_at != null && $Object.hasOwnProperty.call(message, "observed_at"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.observed_at);
                if (message.total != null && $Object.hasOwnProperty.call(message, "total"))
                    writer.uint32(/* id 2, wireType 0 =*/16).int64(message.total);
                if (message.pending != null && $Object.hasOwnProperty.call(message, "pending"))
                    writer.uint32(/* id 3, wireType 0 =*/24).int64(message.pending);
                if (message.running != null && $Object.hasOwnProperty.call(message, "running"))
                    writer.uint32(/* id 4, wireType 0 =*/32).int64(message.running);
                if (message.published != null && $Object.hasOwnProperty.call(message, "published"))
                    writer.uint32(/* id 5, wireType 0 =*/40).int64(message.published);
                if (message.failed != null && $Object.hasOwnProperty.call(message, "failed"))
                    writer.uint32(/* id 6, wireType 0 =*/48).int64(message.failed);
                if (message.oldest_pending_id != null && $Object.hasOwnProperty.call(message, "oldest_pending_id"))
                    writer.uint32(/* id 7, wireType 2 =*/58).string(message.oldest_pending_id);
                if (message.oldest_pending_at != null && $Object.hasOwnProperty.call(message, "oldest_pending_at"))
                    writer.uint32(/* id 8, wireType 2 =*/66).string(message.oldest_pending_at);
                if (message.oldest_pending_seconds != null && $Object.hasOwnProperty.call(message, "oldest_pending_seconds"))
                    writer.uint32(/* id 9, wireType 0 =*/72).int64(message.oldest_pending_seconds);
                if (message.null_fields != null && message.null_fields.length)
                    for (let i = 0; i < message.null_fields.length; ++i)
                        writer.uint32(/* id 2046, wireType 2 =*/16370).string(message.null_fields[i]);
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a ProcessingQueueStatus message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.ProcessingQueueStatus & northstar.data_hub.ProcessingQueueStatus.$Shape} ProcessingQueueStatus
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            ProcessingQueueStatus.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.ProcessingQueueStatus();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.observed_at = reader.stringVerify();
                            message._observed_at = "observed_at";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 0)
                                break;
                            message.total = reader.int64();
                            message._total = "total";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 0)
                                break;
                            message.pending = reader.int64();
                            message._pending = "pending";
                            continue;
                        }
                    case 4: {
                            if (wireType !== 0)
                                break;
                            message.running = reader.int64();
                            message._running = "running";
                            continue;
                        }
                    case 5: {
                            if (wireType !== 0)
                                break;
                            message.published = reader.int64();
                            message._published = "published";
                            continue;
                        }
                    case 6: {
                            if (wireType !== 0)
                                break;
                            message.failed = reader.int64();
                            message._failed = "failed";
                            continue;
                        }
                    case 7: {
                            if (wireType !== 2)
                                break;
                            message.oldest_pending_id = reader.stringVerify();
                            message._oldest_pending_id = "oldest_pending_id";
                            continue;
                        }
                    case 8: {
                            if (wireType !== 2)
                                break;
                            message.oldest_pending_at = reader.stringVerify();
                            message._oldest_pending_at = "oldest_pending_at";
                            continue;
                        }
                    case 9: {
                            if (wireType !== 0)
                                break;
                            message.oldest_pending_seconds = reader.int64();
                            message._oldest_pending_seconds = "oldest_pending_seconds";
                            continue;
                        }
                    case 2046: {
                            if (wireType !== 2)
                                break;
                            if (!(message.null_fields && message.null_fields.length))
                                message.null_fields = [];
                            message.null_fields.push(reader.stringVerify());
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a ProcessingQueueStatus message.
             * @function verify
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            ProcessingQueueStatus.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.observed_at != null && $Object.hasOwnProperty.call(message, "observed_at")) {
                    properties._observed_at = 1;
                    if (!$util.isString(message.observed_at))
                        return "observed_at: string expected";
                }
                if (message.total != null && $Object.hasOwnProperty.call(message, "total")) {
                    properties._total = 1;
                    if (!$util.isInteger(message.total) && !(message.total && $util.isInteger(message.total.low) && $util.isInteger(message.total.high)))
                        return "total: integer|Long expected";
                }
                if (message.pending != null && $Object.hasOwnProperty.call(message, "pending")) {
                    properties._pending = 1;
                    if (!$util.isInteger(message.pending) && !(message.pending && $util.isInteger(message.pending.low) && $util.isInteger(message.pending.high)))
                        return "pending: integer|Long expected";
                }
                if (message.running != null && $Object.hasOwnProperty.call(message, "running")) {
                    properties._running = 1;
                    if (!$util.isInteger(message.running) && !(message.running && $util.isInteger(message.running.low) && $util.isInteger(message.running.high)))
                        return "running: integer|Long expected";
                }
                if (message.published != null && $Object.hasOwnProperty.call(message, "published")) {
                    properties._published = 1;
                    if (!$util.isInteger(message.published) && !(message.published && $util.isInteger(message.published.low) && $util.isInteger(message.published.high)))
                        return "published: integer|Long expected";
                }
                if (message.failed != null && $Object.hasOwnProperty.call(message, "failed")) {
                    properties._failed = 1;
                    if (!$util.isInteger(message.failed) && !(message.failed && $util.isInteger(message.failed.low) && $util.isInteger(message.failed.high)))
                        return "failed: integer|Long expected";
                }
                if (message.oldest_pending_id != null && $Object.hasOwnProperty.call(message, "oldest_pending_id")) {
                    properties._oldest_pending_id = 1;
                    if (!$util.isString(message.oldest_pending_id))
                        return "oldest_pending_id: string expected";
                }
                if (message.oldest_pending_at != null && $Object.hasOwnProperty.call(message, "oldest_pending_at")) {
                    properties._oldest_pending_at = 1;
                    if (!$util.isString(message.oldest_pending_at))
                        return "oldest_pending_at: string expected";
                }
                if (message.oldest_pending_seconds != null && $Object.hasOwnProperty.call(message, "oldest_pending_seconds")) {
                    properties._oldest_pending_seconds = 1;
                    if (!$util.isInteger(message.oldest_pending_seconds) && !(message.oldest_pending_seconds && $util.isInteger(message.oldest_pending_seconds.low) && $util.isInteger(message.oldest_pending_seconds.high)))
                        return "oldest_pending_seconds: integer|Long expected";
                }
                if (message.null_fields != null && $Object.hasOwnProperty.call(message, "null_fields")) {
                    if (!$Array.isArray(message.null_fields))
                        return "null_fields: array expected";
                    for (let i = 0; i < message.null_fields.length; ++i)
                        if (!$util.isString(message.null_fields[i]))
                            return "null_fields: string[] expected";
                }
                return null;
            };

            /**
             * Creates a ProcessingQueueStatus message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.ProcessingQueueStatus} ProcessingQueueStatus
             */
            ProcessingQueueStatus.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.ProcessingQueueStatus)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.ProcessingQueueStatus: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.ProcessingQueueStatus();
                if (object.observed_at != null)
                    message.observed_at = $String(object.observed_at);
                if (object.total != null)
                    if ($util.Long)
                        message.total = $util.Long.fromValue(object.total, false);
                    else if (typeof object.total === "string")
                        message.total = $parseInt(object.total, 10);
                    else if (typeof object.total === "number")
                        message.total = object.total;
                    else if (typeof object.total === "object")
                        message.total = new $util.LongBits(object.total.low >>> 0, object.total.high >>> 0).toNumber();
                if (object.pending != null)
                    if ($util.Long)
                        message.pending = $util.Long.fromValue(object.pending, false);
                    else if (typeof object.pending === "string")
                        message.pending = $parseInt(object.pending, 10);
                    else if (typeof object.pending === "number")
                        message.pending = object.pending;
                    else if (typeof object.pending === "object")
                        message.pending = new $util.LongBits(object.pending.low >>> 0, object.pending.high >>> 0).toNumber();
                if (object.running != null)
                    if ($util.Long)
                        message.running = $util.Long.fromValue(object.running, false);
                    else if (typeof object.running === "string")
                        message.running = $parseInt(object.running, 10);
                    else if (typeof object.running === "number")
                        message.running = object.running;
                    else if (typeof object.running === "object")
                        message.running = new $util.LongBits(object.running.low >>> 0, object.running.high >>> 0).toNumber();
                if (object.published != null)
                    if ($util.Long)
                        message.published = $util.Long.fromValue(object.published, false);
                    else if (typeof object.published === "string")
                        message.published = $parseInt(object.published, 10);
                    else if (typeof object.published === "number")
                        message.published = object.published;
                    else if (typeof object.published === "object")
                        message.published = new $util.LongBits(object.published.low >>> 0, object.published.high >>> 0).toNumber();
                if (object.failed != null)
                    if ($util.Long)
                        message.failed = $util.Long.fromValue(object.failed, false);
                    else if (typeof object.failed === "string")
                        message.failed = $parseInt(object.failed, 10);
                    else if (typeof object.failed === "number")
                        message.failed = object.failed;
                    else if (typeof object.failed === "object")
                        message.failed = new $util.LongBits(object.failed.low >>> 0, object.failed.high >>> 0).toNumber();
                if (object.oldest_pending_id != null)
                    message.oldest_pending_id = $String(object.oldest_pending_id);
                if (object.oldest_pending_at != null)
                    message.oldest_pending_at = $String(object.oldest_pending_at);
                if (object.oldest_pending_seconds != null)
                    if ($util.Long)
                        message.oldest_pending_seconds = $util.Long.fromValue(object.oldest_pending_seconds, false);
                    else if (typeof object.oldest_pending_seconds === "string")
                        message.oldest_pending_seconds = $parseInt(object.oldest_pending_seconds, 10);
                    else if (typeof object.oldest_pending_seconds === "number")
                        message.oldest_pending_seconds = object.oldest_pending_seconds;
                    else if (typeof object.oldest_pending_seconds === "object")
                        message.oldest_pending_seconds = new $util.LongBits(object.oldest_pending_seconds.low >>> 0, object.oldest_pending_seconds.high >>> 0).toNumber();
                if (object.null_fields) {
                    if (!$Array.isArray(object.null_fields))
                        throw $TypeError(".northstar.data_hub.ProcessingQueueStatus.null_fields: array expected");
                    message.null_fields = $Array(object.null_fields.length);
                    for (let i = 0; i < object.null_fields.length; ++i)
                        message.null_fields[i] = $String(object.null_fields[i]);
                }
                return message;
            };

            /**
             * Creates a plain object from a ProcessingQueueStatus message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @static
             * @param {northstar.data_hub.ProcessingQueueStatus} message ProcessingQueueStatus
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            ProcessingQueueStatus.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults)
                    object.null_fields = [];
                if (message.observed_at != null && $Object.hasOwnProperty.call(message, "observed_at")) {
                    object.observed_at = message.observed_at;
                    if (options.oneofs)
                        object._observed_at = "observed_at";
                }
                if (message.total != null && $Object.hasOwnProperty.call(message, "total")) {
                    if (typeof $BigInt !== "undefined" && options.longs === $BigInt)
                        object.total = typeof message.total === "number" ? $BigInt(message.total) : $util.Long.fromBits(message.total.low >>> 0, message.total.high >>> 0, false).toBigInt();
                    else if (typeof message.total === "number")
                        object.total = options.longs === $String ? $String(message.total) : message.total;
                    else
                        object.total = options.longs === $String ? $util.Long.prototype.toString.call(message.total) : options.longs === $Number ? new $util.LongBits(message.total.low >>> 0, message.total.high >>> 0).toNumber() : message.total;
                    if (options.oneofs)
                        object._total = "total";
                }
                if (message.pending != null && $Object.hasOwnProperty.call(message, "pending")) {
                    if (typeof $BigInt !== "undefined" && options.longs === $BigInt)
                        object.pending = typeof message.pending === "number" ? $BigInt(message.pending) : $util.Long.fromBits(message.pending.low >>> 0, message.pending.high >>> 0, false).toBigInt();
                    else if (typeof message.pending === "number")
                        object.pending = options.longs === $String ? $String(message.pending) : message.pending;
                    else
                        object.pending = options.longs === $String ? $util.Long.prototype.toString.call(message.pending) : options.longs === $Number ? new $util.LongBits(message.pending.low >>> 0, message.pending.high >>> 0).toNumber() : message.pending;
                    if (options.oneofs)
                        object._pending = "pending";
                }
                if (message.running != null && $Object.hasOwnProperty.call(message, "running")) {
                    if (typeof $BigInt !== "undefined" && options.longs === $BigInt)
                        object.running = typeof message.running === "number" ? $BigInt(message.running) : $util.Long.fromBits(message.running.low >>> 0, message.running.high >>> 0, false).toBigInt();
                    else if (typeof message.running === "number")
                        object.running = options.longs === $String ? $String(message.running) : message.running;
                    else
                        object.running = options.longs === $String ? $util.Long.prototype.toString.call(message.running) : options.longs === $Number ? new $util.LongBits(message.running.low >>> 0, message.running.high >>> 0).toNumber() : message.running;
                    if (options.oneofs)
                        object._running = "running";
                }
                if (message.published != null && $Object.hasOwnProperty.call(message, "published")) {
                    if (typeof $BigInt !== "undefined" && options.longs === $BigInt)
                        object.published = typeof message.published === "number" ? $BigInt(message.published) : $util.Long.fromBits(message.published.low >>> 0, message.published.high >>> 0, false).toBigInt();
                    else if (typeof message.published === "number")
                        object.published = options.longs === $String ? $String(message.published) : message.published;
                    else
                        object.published = options.longs === $String ? $util.Long.prototype.toString.call(message.published) : options.longs === $Number ? new $util.LongBits(message.published.low >>> 0, message.published.high >>> 0).toNumber() : message.published;
                    if (options.oneofs)
                        object._published = "published";
                }
                if (message.failed != null && $Object.hasOwnProperty.call(message, "failed")) {
                    if (typeof $BigInt !== "undefined" && options.longs === $BigInt)
                        object.failed = typeof message.failed === "number" ? $BigInt(message.failed) : $util.Long.fromBits(message.failed.low >>> 0, message.failed.high >>> 0, false).toBigInt();
                    else if (typeof message.failed === "number")
                        object.failed = options.longs === $String ? $String(message.failed) : message.failed;
                    else
                        object.failed = options.longs === $String ? $util.Long.prototype.toString.call(message.failed) : options.longs === $Number ? new $util.LongBits(message.failed.low >>> 0, message.failed.high >>> 0).toNumber() : message.failed;
                    if (options.oneofs)
                        object._failed = "failed";
                }
                if (message.oldest_pending_id != null && $Object.hasOwnProperty.call(message, "oldest_pending_id")) {
                    object.oldest_pending_id = message.oldest_pending_id;
                    if (options.oneofs)
                        object._oldest_pending_id = "oldest_pending_id";
                }
                if (message.oldest_pending_at != null && $Object.hasOwnProperty.call(message, "oldest_pending_at")) {
                    object.oldest_pending_at = message.oldest_pending_at;
                    if (options.oneofs)
                        object._oldest_pending_at = "oldest_pending_at";
                }
                if (message.oldest_pending_seconds != null && $Object.hasOwnProperty.call(message, "oldest_pending_seconds")) {
                    if (typeof $BigInt !== "undefined" && options.longs === $BigInt)
                        object.oldest_pending_seconds = typeof message.oldest_pending_seconds === "number" ? $BigInt(message.oldest_pending_seconds) : $util.Long.fromBits(message.oldest_pending_seconds.low >>> 0, message.oldest_pending_seconds.high >>> 0, false).toBigInt();
                    else if (typeof message.oldest_pending_seconds === "number")
                        object.oldest_pending_seconds = options.longs === $String ? $String(message.oldest_pending_seconds) : message.oldest_pending_seconds;
                    else
                        object.oldest_pending_seconds = options.longs === $String ? $util.Long.prototype.toString.call(message.oldest_pending_seconds) : options.longs === $Number ? new $util.LongBits(message.oldest_pending_seconds.low >>> 0, message.oldest_pending_seconds.high >>> 0).toNumber() : message.oldest_pending_seconds;
                    if (options.oneofs)
                        object._oldest_pending_seconds = "oldest_pending_seconds";
                }
                if (message.null_fields && message.null_fields.length) {
                    object.null_fields = $Array(message.null_fields.length);
                    for (let j = 0; j < message.null_fields.length; ++j)
                        object.null_fields[j] = message.null_fields[j];
                }
                return object;
            };

            /**
             * Converts this ProcessingQueueStatus to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            ProcessingQueueStatus.prototype.toJSON = function() {
                return ProcessingQueueStatus.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for ProcessingQueueStatus
             * @function getTypeUrl
             * @memberof northstar.data_hub.ProcessingQueueStatus
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            ProcessingQueueStatus.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.ProcessingQueueStatus";
            };

            return ProcessingQueueStatus;
        })();

        data_hub.SyncRequest = (function() {

            /**
             * Properties of a SyncRequest.
             * @typedef {Object} northstar.data_hub.SyncRequest.$Properties
             * @property {string|null} [request_id] SyncRequest request_id
             * @property {google.protobuf.Struct.$Properties|null} [spec] SyncRequest spec
             * @property {"request_id"} [_request_id] SyncRequest _request_id
             * @property {"spec"} [_spec] SyncRequest _spec
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a SyncRequest.
             * @memberof northstar.data_hub
             * @interface ISyncRequest
             * @augments northstar.data_hub.SyncRequest.$Properties
             * @deprecated Use northstar.data_hub.SyncRequest.$Properties instead.
             */

            /**
             * Narrowed shape of a SyncRequest.
             * @typedef {{
             *   request_id?: string|null;
             *   spec?: google.protobuf.Struct.$Shape|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _request_id?: undefined; request_id?: null }|{ _request_id?: "request_id"; request_id: string })
             * ) & (
             *   ({ _spec?: undefined; spec?: null }|{ _spec?: "spec"; spec: google.protobuf.Struct.$Shape })
             * )} northstar.data_hub.SyncRequest.$Shape
             */

            /**
             * Constructs a new SyncRequest.
             * @memberof northstar.data_hub
             * @classdesc Represents a SyncRequest.
             * @constructor
             * @param {northstar.data_hub.SyncRequest.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const SyncRequest = function (properties) {
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * SyncRequest request_id.
             * @member {string|null|undefined} request_id
             * @memberof northstar.data_hub.SyncRequest
             * @instance
             */
            SyncRequest.prototype.request_id = null;

            /**
             * SyncRequest spec.
             * @member {google.protobuf.Struct.$Properties|null|undefined} spec
             * @memberof northstar.data_hub.SyncRequest
             * @instance
             */
            SyncRequest.prototype.spec = null;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * SyncRequest _request_id.
             * @member {"request_id"|undefined} _request_id
             * @memberof northstar.data_hub.SyncRequest
             * @instance
             */
            $Object.defineProperty(SyncRequest.prototype, "_request_id", {
                get: $util.oneOfGetter($oneOfFields = ["request_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SyncRequest _spec.
             * @member {"spec"|undefined} _spec
             * @memberof northstar.data_hub.SyncRequest
             * @instance
             */
            $Object.defineProperty(SyncRequest.prototype, "_spec", {
                get: $util.oneOfGetter($oneOfFields = ["spec"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new SyncRequest instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.SyncRequest
             * @static
             * @param {northstar.data_hub.SyncRequest.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.SyncRequest} SyncRequest instance
             * @type {{
             *   (properties: northstar.data_hub.SyncRequest.$Shape): northstar.data_hub.SyncRequest & northstar.data_hub.SyncRequest.$Shape;
             *   (properties?: northstar.data_hub.SyncRequest.$Properties): northstar.data_hub.SyncRequest;
             * }}
             */
            SyncRequest.create = function(properties) {
                return new SyncRequest(properties);
            };

            /**
             * Encodes the specified SyncRequest message. Does not implicitly {@link northstar.data_hub.SyncRequest.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.SyncRequest
             * @static
             * @param {northstar.data_hub.SyncRequest.$Properties} message SyncRequest message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            SyncRequest.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.request_id);
                if (message.spec != null && $Object.hasOwnProperty.call(message, "spec"))
                    $root.google.protobuf.Struct.encode(message.spec, writer.uint32(/* id 2, wireType 2 =*/18).fork(), _depth + 1).ldelim();
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a SyncRequest message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.SyncRequest
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.SyncRequest & northstar.data_hub.SyncRequest.$Shape} SyncRequest
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            SyncRequest.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.SyncRequest();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.request_id = reader.stringVerify();
                            message._request_id = "request_id";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 2)
                                break;
                            message.spec = $root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1, message.spec);
                            message._spec = "spec";
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a SyncRequest message.
             * @function verify
             * @memberof northstar.data_hub.SyncRequest
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            SyncRequest.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    properties._request_id = 1;
                    if (!$util.isString(message.request_id))
                        return "request_id: string expected";
                }
                if (message.spec != null && $Object.hasOwnProperty.call(message, "spec")) {
                    properties._spec = 1;
                    {
                        let error = $root.google.protobuf.Struct.verify(message.spec, _depth + 1);
                        if (error)
                            return "spec." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a SyncRequest message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.SyncRequest
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.SyncRequest} SyncRequest
             */
            SyncRequest.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.SyncRequest)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.SyncRequest: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.SyncRequest();
                if (object.request_id != null)
                    message.request_id = $String(object.request_id);
                if (object.spec != null) {
                    if (!$util.isObject(object.spec))
                        throw $TypeError(".northstar.data_hub.SyncRequest.spec: object expected");
                    message.spec = $root.google.protobuf.Struct.fromObject(object.spec, _depth + 1);
                }
                return message;
            };

            /**
             * Creates a plain object from a SyncRequest message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.SyncRequest
             * @static
             * @param {northstar.data_hub.SyncRequest} message SyncRequest
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            SyncRequest.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    object.request_id = message.request_id;
                    if (options.oneofs)
                        object._request_id = "request_id";
                }
                if (message.spec != null && $Object.hasOwnProperty.call(message, "spec")) {
                    object.spec = $root.google.protobuf.Struct.toObject(message.spec, options, _depth + 1);
                    if (options.oneofs)
                        object._spec = "spec";
                }
                return object;
            };

            /**
             * Converts this SyncRequest to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.SyncRequest
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            SyncRequest.prototype.toJSON = function() {
                return SyncRequest.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for SyncRequest
             * @function getTypeUrl
             * @memberof northstar.data_hub.SyncRequest
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            SyncRequest.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.SyncRequest";
            };

            return SyncRequest;
        })();

        data_hub.SyncJob = (function() {

            /**
             * Properties of a SyncJob.
             * @typedef {Object} northstar.data_hub.SyncJob.$Properties
             * @property {string|null} [request_id] SyncJob request_id
             * @property {string|null} [request_hash] SyncJob request_hash
             * @property {google.protobuf.Struct.$Properties|null} [parameters] SyncJob parameters
             * @property {string|null} [code_revision] SyncJob code_revision
             * @property {string|null} [status] SyncJob status
             * @property {string|null} [attempt_id] SyncJob attempt_id
             * @property {string|null} [error] SyncJob error
             * @property {string|null} [created_at] SyncJob created_at
             * @property {string|null} [updated_at] SyncJob updated_at
             * @property {Array.<string>|null} [null_fields] SyncJob null_fields
             * @property {"request_id"} [_request_id] SyncJob _request_id
             * @property {"request_hash"} [_request_hash] SyncJob _request_hash
             * @property {"parameters"} [_parameters] SyncJob _parameters
             * @property {"code_revision"} [_code_revision] SyncJob _code_revision
             * @property {"status"} [_status] SyncJob _status
             * @property {"attempt_id"} [_attempt_id] SyncJob _attempt_id
             * @property {"error"} [_error] SyncJob _error
             * @property {"created_at"} [_created_at] SyncJob _created_at
             * @property {"updated_at"} [_updated_at] SyncJob _updated_at
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a SyncJob.
             * @memberof northstar.data_hub
             * @interface ISyncJob
             * @augments northstar.data_hub.SyncJob.$Properties
             * @deprecated Use northstar.data_hub.SyncJob.$Properties instead.
             */

            /**
             * Narrowed shape of a SyncJob.
             * @typedef {{
             *   request_id?: string|null;
             *   request_hash?: string|null;
             *   parameters?: google.protobuf.Struct.$Shape|null;
             *   code_revision?: string|null;
             *   status?: string|null;
             *   attempt_id?: string|null;
             *   error?: string|null;
             *   created_at?: string|null;
             *   updated_at?: string|null;
             *   null_fields?: Array.<string>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _request_id?: undefined; request_id?: null }|{ _request_id?: "request_id"; request_id: string })
             * ) & (
             *   ({ _request_hash?: undefined; request_hash?: null }|{ _request_hash?: "request_hash"; request_hash: string })
             * ) & (
             *   ({ _parameters?: undefined; parameters?: null }|{ _parameters?: "parameters"; parameters: google.protobuf.Struct.$Shape })
             * ) & (
             *   ({ _code_revision?: undefined; code_revision?: null }|{ _code_revision?: "code_revision"; code_revision: string })
             * ) & (
             *   ({ _status?: undefined; status?: null }|{ _status?: "status"; status: string })
             * ) & (
             *   ({ _attempt_id?: undefined; attempt_id?: null }|{ _attempt_id?: "attempt_id"; attempt_id: string })
             * ) & (
             *   ({ _error?: undefined; error?: null }|{ _error?: "error"; error: string })
             * ) & (
             *   ({ _created_at?: undefined; created_at?: null }|{ _created_at?: "created_at"; created_at: string })
             * ) & (
             *   ({ _updated_at?: undefined; updated_at?: null }|{ _updated_at?: "updated_at"; updated_at: string })
             * )} northstar.data_hub.SyncJob.$Shape
             */

            /**
             * Constructs a new SyncJob.
             * @memberof northstar.data_hub
             * @classdesc Represents a SyncJob.
             * @constructor
             * @param {northstar.data_hub.SyncJob.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const SyncJob = function (properties) {
                this.null_fields = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * SyncJob request_id.
             * @member {string|null|undefined} request_id
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            SyncJob.prototype.request_id = null;

            /**
             * SyncJob request_hash.
             * @member {string|null|undefined} request_hash
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            SyncJob.prototype.request_hash = null;

            /**
             * SyncJob parameters.
             * @member {google.protobuf.Struct.$Properties|null|undefined} parameters
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            SyncJob.prototype.parameters = null;

            /**
             * SyncJob code_revision.
             * @member {string|null|undefined} code_revision
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            SyncJob.prototype.code_revision = null;

            /**
             * SyncJob status.
             * @member {string|null|undefined} status
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            SyncJob.prototype.status = null;

            /**
             * SyncJob attempt_id.
             * @member {string|null|undefined} attempt_id
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            SyncJob.prototype.attempt_id = null;

            /**
             * SyncJob error.
             * @member {string|null|undefined} error
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            SyncJob.prototype.error = null;

            /**
             * SyncJob created_at.
             * @member {string|null|undefined} created_at
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            SyncJob.prototype.created_at = null;

            /**
             * SyncJob updated_at.
             * @member {string|null|undefined} updated_at
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            SyncJob.prototype.updated_at = null;

            /**
             * SyncJob null_fields.
             * @member {Array.<string>} null_fields
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            SyncJob.prototype.null_fields = $util.emptyArray;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * SyncJob _request_id.
             * @member {"request_id"|undefined} _request_id
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            $Object.defineProperty(SyncJob.prototype, "_request_id", {
                get: $util.oneOfGetter($oneOfFields = ["request_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SyncJob _request_hash.
             * @member {"request_hash"|undefined} _request_hash
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            $Object.defineProperty(SyncJob.prototype, "_request_hash", {
                get: $util.oneOfGetter($oneOfFields = ["request_hash"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SyncJob _parameters.
             * @member {"parameters"|undefined} _parameters
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            $Object.defineProperty(SyncJob.prototype, "_parameters", {
                get: $util.oneOfGetter($oneOfFields = ["parameters"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SyncJob _code_revision.
             * @member {"code_revision"|undefined} _code_revision
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            $Object.defineProperty(SyncJob.prototype, "_code_revision", {
                get: $util.oneOfGetter($oneOfFields = ["code_revision"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SyncJob _status.
             * @member {"status"|undefined} _status
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            $Object.defineProperty(SyncJob.prototype, "_status", {
                get: $util.oneOfGetter($oneOfFields = ["status"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SyncJob _attempt_id.
             * @member {"attempt_id"|undefined} _attempt_id
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            $Object.defineProperty(SyncJob.prototype, "_attempt_id", {
                get: $util.oneOfGetter($oneOfFields = ["attempt_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SyncJob _error.
             * @member {"error"|undefined} _error
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            $Object.defineProperty(SyncJob.prototype, "_error", {
                get: $util.oneOfGetter($oneOfFields = ["error"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SyncJob _created_at.
             * @member {"created_at"|undefined} _created_at
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            $Object.defineProperty(SyncJob.prototype, "_created_at", {
                get: $util.oneOfGetter($oneOfFields = ["created_at"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * SyncJob _updated_at.
             * @member {"updated_at"|undefined} _updated_at
             * @memberof northstar.data_hub.SyncJob
             * @instance
             */
            $Object.defineProperty(SyncJob.prototype, "_updated_at", {
                get: $util.oneOfGetter($oneOfFields = ["updated_at"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new SyncJob instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.SyncJob
             * @static
             * @param {northstar.data_hub.SyncJob.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.SyncJob} SyncJob instance
             * @type {{
             *   (properties: northstar.data_hub.SyncJob.$Shape): northstar.data_hub.SyncJob & northstar.data_hub.SyncJob.$Shape;
             *   (properties?: northstar.data_hub.SyncJob.$Properties): northstar.data_hub.SyncJob;
             * }}
             */
            SyncJob.create = function(properties) {
                return new SyncJob(properties);
            };

            /**
             * Encodes the specified SyncJob message. Does not implicitly {@link northstar.data_hub.SyncJob.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.SyncJob
             * @static
             * @param {northstar.data_hub.SyncJob.$Properties} message SyncJob message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            SyncJob.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.request_id);
                if (message.request_hash != null && $Object.hasOwnProperty.call(message, "request_hash"))
                    writer.uint32(/* id 2, wireType 2 =*/18).string(message.request_hash);
                if (message.parameters != null && $Object.hasOwnProperty.call(message, "parameters"))
                    $root.google.protobuf.Struct.encode(message.parameters, writer.uint32(/* id 3, wireType 2 =*/26).fork(), _depth + 1).ldelim();
                if (message.code_revision != null && $Object.hasOwnProperty.call(message, "code_revision"))
                    writer.uint32(/* id 4, wireType 2 =*/34).string(message.code_revision);
                if (message.status != null && $Object.hasOwnProperty.call(message, "status"))
                    writer.uint32(/* id 5, wireType 2 =*/42).string(message.status);
                if (message.attempt_id != null && $Object.hasOwnProperty.call(message, "attempt_id"))
                    writer.uint32(/* id 6, wireType 2 =*/50).string(message.attempt_id);
                if (message.error != null && $Object.hasOwnProperty.call(message, "error"))
                    writer.uint32(/* id 7, wireType 2 =*/58).string(message.error);
                if (message.created_at != null && $Object.hasOwnProperty.call(message, "created_at"))
                    writer.uint32(/* id 8, wireType 2 =*/66).string(message.created_at);
                if (message.updated_at != null && $Object.hasOwnProperty.call(message, "updated_at"))
                    writer.uint32(/* id 9, wireType 2 =*/74).string(message.updated_at);
                if (message.null_fields != null && message.null_fields.length)
                    for (let i = 0; i < message.null_fields.length; ++i)
                        writer.uint32(/* id 2046, wireType 2 =*/16370).string(message.null_fields[i]);
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a SyncJob message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.SyncJob
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.SyncJob & northstar.data_hub.SyncJob.$Shape} SyncJob
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            SyncJob.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.SyncJob();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.request_id = reader.stringVerify();
                            message._request_id = "request_id";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 2)
                                break;
                            message.request_hash = reader.stringVerify();
                            message._request_hash = "request_hash";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 2)
                                break;
                            message.parameters = $root.google.protobuf.Struct.decode(reader, reader.uint32(), $undefined, _depth + 1, message.parameters);
                            message._parameters = "parameters";
                            continue;
                        }
                    case 4: {
                            if (wireType !== 2)
                                break;
                            message.code_revision = reader.stringVerify();
                            message._code_revision = "code_revision";
                            continue;
                        }
                    case 5: {
                            if (wireType !== 2)
                                break;
                            message.status = reader.stringVerify();
                            message._status = "status";
                            continue;
                        }
                    case 6: {
                            if (wireType !== 2)
                                break;
                            message.attempt_id = reader.stringVerify();
                            message._attempt_id = "attempt_id";
                            continue;
                        }
                    case 7: {
                            if (wireType !== 2)
                                break;
                            message.error = reader.stringVerify();
                            message._error = "error";
                            continue;
                        }
                    case 8: {
                            if (wireType !== 2)
                                break;
                            message.created_at = reader.stringVerify();
                            message._created_at = "created_at";
                            continue;
                        }
                    case 9: {
                            if (wireType !== 2)
                                break;
                            message.updated_at = reader.stringVerify();
                            message._updated_at = "updated_at";
                            continue;
                        }
                    case 2046: {
                            if (wireType !== 2)
                                break;
                            if (!(message.null_fields && message.null_fields.length))
                                message.null_fields = [];
                            message.null_fields.push(reader.stringVerify());
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a SyncJob message.
             * @function verify
             * @memberof northstar.data_hub.SyncJob
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            SyncJob.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    properties._request_id = 1;
                    if (!$util.isString(message.request_id))
                        return "request_id: string expected";
                }
                if (message.request_hash != null && $Object.hasOwnProperty.call(message, "request_hash")) {
                    properties._request_hash = 1;
                    if (!$util.isString(message.request_hash))
                        return "request_hash: string expected";
                }
                if (message.parameters != null && $Object.hasOwnProperty.call(message, "parameters")) {
                    properties._parameters = 1;
                    {
                        let error = $root.google.protobuf.Struct.verify(message.parameters, _depth + 1);
                        if (error)
                            return "parameters." + error;
                    }
                }
                if (message.code_revision != null && $Object.hasOwnProperty.call(message, "code_revision")) {
                    properties._code_revision = 1;
                    if (!$util.isString(message.code_revision))
                        return "code_revision: string expected";
                }
                if (message.status != null && $Object.hasOwnProperty.call(message, "status")) {
                    properties._status = 1;
                    if (!$util.isString(message.status))
                        return "status: string expected";
                }
                if (message.attempt_id != null && $Object.hasOwnProperty.call(message, "attempt_id")) {
                    properties._attempt_id = 1;
                    if (!$util.isString(message.attempt_id))
                        return "attempt_id: string expected";
                }
                if (message.error != null && $Object.hasOwnProperty.call(message, "error")) {
                    properties._error = 1;
                    if (!$util.isString(message.error))
                        return "error: string expected";
                }
                if (message.created_at != null && $Object.hasOwnProperty.call(message, "created_at")) {
                    properties._created_at = 1;
                    if (!$util.isString(message.created_at))
                        return "created_at: string expected";
                }
                if (message.updated_at != null && $Object.hasOwnProperty.call(message, "updated_at")) {
                    properties._updated_at = 1;
                    if (!$util.isString(message.updated_at))
                        return "updated_at: string expected";
                }
                if (message.null_fields != null && $Object.hasOwnProperty.call(message, "null_fields")) {
                    if (!$Array.isArray(message.null_fields))
                        return "null_fields: array expected";
                    for (let i = 0; i < message.null_fields.length; ++i)
                        if (!$util.isString(message.null_fields[i]))
                            return "null_fields: string[] expected";
                }
                return null;
            };

            /**
             * Creates a SyncJob message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.SyncJob
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.SyncJob} SyncJob
             */
            SyncJob.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.SyncJob)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.SyncJob: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.SyncJob();
                if (object.request_id != null)
                    message.request_id = $String(object.request_id);
                if (object.request_hash != null)
                    message.request_hash = $String(object.request_hash);
                if (object.parameters != null) {
                    if (!$util.isObject(object.parameters))
                        throw $TypeError(".northstar.data_hub.SyncJob.parameters: object expected");
                    message.parameters = $root.google.protobuf.Struct.fromObject(object.parameters, _depth + 1);
                }
                if (object.code_revision != null)
                    message.code_revision = $String(object.code_revision);
                if (object.status != null)
                    message.status = $String(object.status);
                if (object.attempt_id != null)
                    message.attempt_id = $String(object.attempt_id);
                if (object.error != null)
                    message.error = $String(object.error);
                if (object.created_at != null)
                    message.created_at = $String(object.created_at);
                if (object.updated_at != null)
                    message.updated_at = $String(object.updated_at);
                if (object.null_fields) {
                    if (!$Array.isArray(object.null_fields))
                        throw $TypeError(".northstar.data_hub.SyncJob.null_fields: array expected");
                    message.null_fields = $Array(object.null_fields.length);
                    for (let i = 0; i < object.null_fields.length; ++i)
                        message.null_fields[i] = $String(object.null_fields[i]);
                }
                return message;
            };

            /**
             * Creates a plain object from a SyncJob message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.SyncJob
             * @static
             * @param {northstar.data_hub.SyncJob} message SyncJob
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            SyncJob.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults)
                    object.null_fields = [];
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    object.request_id = message.request_id;
                    if (options.oneofs)
                        object._request_id = "request_id";
                }
                if (message.request_hash != null && $Object.hasOwnProperty.call(message, "request_hash")) {
                    object.request_hash = message.request_hash;
                    if (options.oneofs)
                        object._request_hash = "request_hash";
                }
                if (message.parameters != null && $Object.hasOwnProperty.call(message, "parameters")) {
                    object.parameters = $root.google.protobuf.Struct.toObject(message.parameters, options, _depth + 1);
                    if (options.oneofs)
                        object._parameters = "parameters";
                }
                if (message.code_revision != null && $Object.hasOwnProperty.call(message, "code_revision")) {
                    object.code_revision = message.code_revision;
                    if (options.oneofs)
                        object._code_revision = "code_revision";
                }
                if (message.status != null && $Object.hasOwnProperty.call(message, "status")) {
                    object.status = message.status;
                    if (options.oneofs)
                        object._status = "status";
                }
                if (message.attempt_id != null && $Object.hasOwnProperty.call(message, "attempt_id")) {
                    object.attempt_id = message.attempt_id;
                    if (options.oneofs)
                        object._attempt_id = "attempt_id";
                }
                if (message.error != null && $Object.hasOwnProperty.call(message, "error")) {
                    object.error = message.error;
                    if (options.oneofs)
                        object._error = "error";
                }
                if (message.created_at != null && $Object.hasOwnProperty.call(message, "created_at")) {
                    object.created_at = message.created_at;
                    if (options.oneofs)
                        object._created_at = "created_at";
                }
                if (message.updated_at != null && $Object.hasOwnProperty.call(message, "updated_at")) {
                    object.updated_at = message.updated_at;
                    if (options.oneofs)
                        object._updated_at = "updated_at";
                }
                if (message.null_fields && message.null_fields.length) {
                    object.null_fields = $Array(message.null_fields.length);
                    for (let j = 0; j < message.null_fields.length; ++j)
                        object.null_fields[j] = message.null_fields[j];
                }
                return object;
            };

            /**
             * Converts this SyncJob to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.SyncJob
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            SyncJob.prototype.toJSON = function() {
                return SyncJob.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for SyncJob
             * @function getTypeUrl
             * @memberof northstar.data_hub.SyncJob
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            SyncJob.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.SyncJob";
            };

            return SyncJob;
        })();

        data_hub.GetApiSyncResponse = (function() {

            /**
             * Properties of a GetApiSyncResponse.
             * @typedef {Object} northstar.data_hub.GetApiSyncResponse.$Properties
             * @property {Array.<northstar.data_hub.SyncJob.$Properties>|null} [items] GetApiSyncResponse items
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of a GetApiSyncResponse.
             * @memberof northstar.data_hub
             * @interface IGetApiSyncResponse
             * @augments northstar.data_hub.GetApiSyncResponse.$Properties
             * @deprecated Use northstar.data_hub.GetApiSyncResponse.$Properties instead.
             */

            /**
             * Shape of a GetApiSyncResponse.
             * @typedef {{
             *   items?: Array.<northstar.data_hub.SyncJob.$Shape>|null;
             *   $unknowns?: Array.<Uint8Array>;
             * }} northstar.data_hub.GetApiSyncResponse.$Shape
             */

            /**
             * Constructs a new GetApiSyncResponse.
             * @memberof northstar.data_hub
             * @classdesc Represents a GetApiSyncResponse.
             * @constructor
             * @param {northstar.data_hub.GetApiSyncResponse.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const GetApiSyncResponse = function (properties) {
                this.items = [];
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * GetApiSyncResponse items.
             * @member {Array.<northstar.data_hub.SyncJob.$Properties>} items
             * @memberof northstar.data_hub.GetApiSyncResponse
             * @instance
             */
            GetApiSyncResponse.prototype.items = $util.emptyArray;

            /**
             * Creates a new GetApiSyncResponse instance using the specified properties.
             * @function create
             * @memberof northstar.data_hub.GetApiSyncResponse
             * @static
             * @param {northstar.data_hub.GetApiSyncResponse.$Properties=} [properties] Properties to set
             * @returns {northstar.data_hub.GetApiSyncResponse} GetApiSyncResponse instance
             * @type {{
             *   (properties: northstar.data_hub.GetApiSyncResponse.$Shape): northstar.data_hub.GetApiSyncResponse & northstar.data_hub.GetApiSyncResponse.$Shape;
             *   (properties?: northstar.data_hub.GetApiSyncResponse.$Properties): northstar.data_hub.GetApiSyncResponse;
             * }}
             */
            GetApiSyncResponse.create = function(properties) {
                return new GetApiSyncResponse(properties);
            };

            /**
             * Encodes the specified GetApiSyncResponse message. Does not implicitly {@link northstar.data_hub.GetApiSyncResponse.verify|verify} messages.
             * @function encode
             * @memberof northstar.data_hub.GetApiSyncResponse
             * @static
             * @param {northstar.data_hub.GetApiSyncResponse.$Properties} message GetApiSyncResponse message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            GetApiSyncResponse.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.items != null && message.items.length)
                    for (let i = 0; i < message.items.length; ++i)
                        $root.northstar.data_hub.SyncJob.encode(message.items[i], writer.uint32(/* id 1, wireType 2 =*/10).fork(), _depth + 1).ldelim();
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes a GetApiSyncResponse message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.data_hub.GetApiSyncResponse
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.data_hub.GetApiSyncResponse & northstar.data_hub.GetApiSyncResponse.$Shape} GetApiSyncResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            GetApiSyncResponse.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.data_hub.GetApiSyncResponse();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            if (!(message.items && message.items.length))
                                message.items = [];
                            message.items.push($root.northstar.data_hub.SyncJob.decode(reader, reader.uint32(), $undefined, _depth + 1));
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies a GetApiSyncResponse message.
             * @function verify
             * @memberof northstar.data_hub.GetApiSyncResponse
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            GetApiSyncResponse.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                if (message.items != null && $Object.hasOwnProperty.call(message, "items")) {
                    if (!$Array.isArray(message.items))
                        return "items: array expected";
                    for (let i = 0; i < message.items.length; ++i) {
                        let error = $root.northstar.data_hub.SyncJob.verify(message.items[i], _depth + 1);
                        if (error)
                            return "items." + error;
                    }
                }
                return null;
            };

            /**
             * Creates a GetApiSyncResponse message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.data_hub.GetApiSyncResponse
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.data_hub.GetApiSyncResponse} GetApiSyncResponse
             */
            GetApiSyncResponse.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.data_hub.GetApiSyncResponse)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.data_hub.GetApiSyncResponse: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.data_hub.GetApiSyncResponse();
                if (object.items) {
                    if (!$Array.isArray(object.items))
                        throw $TypeError(".northstar.data_hub.GetApiSyncResponse.items: array expected");
                    message.items = $Array(object.items.length);
                    for (let i = 0; i < object.items.length; ++i) {
                        if (!$util.isObject(object.items[i]))
                            throw $TypeError(".northstar.data_hub.GetApiSyncResponse.items: object expected");
                        message.items[i] = $root.northstar.data_hub.SyncJob.fromObject(object.items[i], _depth + 1);
                    }
                }
                return message;
            };

            /**
             * Creates a plain object from a GetApiSyncResponse message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.data_hub.GetApiSyncResponse
             * @static
             * @param {northstar.data_hub.GetApiSyncResponse} message GetApiSyncResponse
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            GetApiSyncResponse.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (options.arrays || options.defaults)
                    object.items = [];
                if (message.items && message.items.length) {
                    object.items = $Array(message.items.length);
                    for (let j = 0; j < message.items.length; ++j)
                        object.items[j] = $root.northstar.data_hub.SyncJob.toObject(message.items[j], options, _depth + 1);
                }
                return object;
            };

            /**
             * Converts this GetApiSyncResponse to JSON.
             * @function toJSON
             * @memberof northstar.data_hub.GetApiSyncResponse
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            GetApiSyncResponse.prototype.toJSON = function() {
                return GetApiSyncResponse.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for GetApiSyncResponse
             * @function getTypeUrl
             * @memberof northstar.data_hub.GetApiSyncResponse
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            GetApiSyncResponse.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.data_hub.GetApiSyncResponse";
            };

            return GetApiSyncResponse;
        })();

        return data_hub;
    })();

    northstar.web = (function() {

        /**
         * Namespace web.
         * @memberof northstar
         * @namespace
         */
        const web = {};

        web.Empty = (function() {

            /**
             * Properties of an Empty.
             * @typedef {Object} northstar.web.Empty.$Properties
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of an Empty.
             * @memberof northstar.web
             * @interface IEmpty
             * @augments northstar.web.Empty.$Properties
             * @deprecated Use northstar.web.Empty.$Properties instead.
             */

            /**
             * Shape of an Empty.
             * @typedef {northstar.web.Empty.$Properties} northstar.web.Empty.$Shape
             */

            /**
             * Constructs a new Empty.
             * @memberof northstar.web
             * @classdesc Represents an Empty.
             * @constructor
             * @param {northstar.web.Empty.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const Empty = function (properties) {
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * Creates a new Empty instance using the specified properties.
             * @function create
             * @memberof northstar.web.Empty
             * @static
             * @param {northstar.web.Empty.$Properties=} [properties] Properties to set
             * @returns {northstar.web.Empty} Empty instance
             * @type {{
             *   (properties: northstar.web.Empty.$Shape): northstar.web.Empty & northstar.web.Empty.$Shape;
             *   (properties?: northstar.web.Empty.$Properties): northstar.web.Empty;
             * }}
             */
            Empty.create = function(properties) {
                return new Empty(properties);
            };

            /**
             * Encodes the specified Empty message. Does not implicitly {@link northstar.web.Empty.verify|verify} messages.
             * @function encode
             * @memberof northstar.web.Empty
             * @static
             * @param {northstar.web.Empty.$Properties} message Empty message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            Empty.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes an Empty message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.web.Empty
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.web.Empty & northstar.web.Empty.$Shape} Empty
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            Empty.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.web.Empty();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    reader.skipType(tag & 7, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies an Empty message.
             * @function verify
             * @memberof northstar.web.Empty
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            Empty.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                return null;
            };

            /**
             * Creates an Empty message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.web.Empty
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.web.Empty} Empty
             */
            Empty.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.web.Empty)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.web.Empty: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                return new $root.northstar.web.Empty();
            };

            /**
             * Creates a plain object from an Empty message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.web.Empty
             * @static
             * @param {northstar.web.Empty} message Empty
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            Empty.toObject = function () {
                return {};
            };

            /**
             * Converts this Empty to JSON.
             * @function toJSON
             * @memberof northstar.web.Empty
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            Empty.prototype.toJSON = function() {
                return Empty.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for Empty
             * @function getTypeUrl
             * @memberof northstar.web.Empty
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            Empty.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.web.Empty";
            };

            return Empty;
        })();

        web.Error = (function() {

            /**
             * Properties of an Error.
             * @typedef {Object} northstar.web.Error.$Properties
             * @property {string|null} [detail] Error detail
             * @property {string|null} [status] Error status
             * @property {string|null} [request_id] Error request_id
             * @property {string|null} [runtime_id] Error runtime_id
             * @property {string|null} [url] Error url
             * @property {string|null} [rejection_id] Error rejection_id
             * @property {"detail"} [_detail] Error _detail
             * @property {"status"} [_status] Error _status
             * @property {"request_id"} [_request_id] Error _request_id
             * @property {"runtime_id"} [_runtime_id] Error _runtime_id
             * @property {"url"} [_url] Error _url
             * @property {"rejection_id"} [_rejection_id] Error _rejection_id
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */

            /**
             * Properties of an Error.
             * @memberof northstar.web
             * @interface IError
             * @augments northstar.web.Error.$Properties
             * @deprecated Use northstar.web.Error.$Properties instead.
             */

            /**
             * Narrowed shape of an Error.
             * @typedef {{
             *   detail?: string|null;
             *   status?: string|null;
             *   request_id?: string|null;
             *   runtime_id?: string|null;
             *   url?: string|null;
             *   rejection_id?: string|null;
             *   $unknowns?: Array.<Uint8Array>;
             * } & (
             *   ({ _detail?: undefined; detail?: null }|{ _detail?: "detail"; detail: string })
             * ) & (
             *   ({ _status?: undefined; status?: null }|{ _status?: "status"; status: string })
             * ) & (
             *   ({ _request_id?: undefined; request_id?: null }|{ _request_id?: "request_id"; request_id: string })
             * ) & (
             *   ({ _runtime_id?: undefined; runtime_id?: null }|{ _runtime_id?: "runtime_id"; runtime_id: string })
             * ) & (
             *   ({ _url?: undefined; url?: null }|{ _url?: "url"; url: string })
             * ) & (
             *   ({ _rejection_id?: undefined; rejection_id?: null }|{ _rejection_id?: "rejection_id"; rejection_id: string })
             * )} northstar.web.Error.$Shape
             */

            /**
             * Constructs a new Error.
             * @memberof northstar.web
             * @classdesc Represents an Error.
             * @constructor
             * @param {northstar.web.Error.$Properties=} [properties] Properties to set
             * @property {Array.<Uint8Array>} [$unknowns] Unknown fields preserved while decoding when enabled
             */
            const Error = function (properties) {
                if (properties)
                    for (let keys = $Object.keys(properties), i = 0; i < keys.length; ++i)
                        if (properties[keys[i]] != null && keys[i] !== "__proto__")
                            this[keys[i]] = properties[keys[i]];
            };

            /**
             * Error detail.
             * @member {string|null|undefined} detail
             * @memberof northstar.web.Error
             * @instance
             */
            Error.prototype.detail = null;

            /**
             * Error status.
             * @member {string|null|undefined} status
             * @memberof northstar.web.Error
             * @instance
             */
            Error.prototype.status = null;

            /**
             * Error request_id.
             * @member {string|null|undefined} request_id
             * @memberof northstar.web.Error
             * @instance
             */
            Error.prototype.request_id = null;

            /**
             * Error runtime_id.
             * @member {string|null|undefined} runtime_id
             * @memberof northstar.web.Error
             * @instance
             */
            Error.prototype.runtime_id = null;

            /**
             * Error url.
             * @member {string|null|undefined} url
             * @memberof northstar.web.Error
             * @instance
             */
            Error.prototype.url = null;

            /**
             * Error rejection_id.
             * @member {string|null|undefined} rejection_id
             * @memberof northstar.web.Error
             * @instance
             */
            Error.prototype.rejection_id = null;

            // OneOf field names bound to virtual getters and setters
            let $oneOfFields;

            /**
             * Error _detail.
             * @member {"detail"|undefined} _detail
             * @memberof northstar.web.Error
             * @instance
             */
            $Object.defineProperty(Error.prototype, "_detail", {
                get: $util.oneOfGetter($oneOfFields = ["detail"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Error _status.
             * @member {"status"|undefined} _status
             * @memberof northstar.web.Error
             * @instance
             */
            $Object.defineProperty(Error.prototype, "_status", {
                get: $util.oneOfGetter($oneOfFields = ["status"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Error _request_id.
             * @member {"request_id"|undefined} _request_id
             * @memberof northstar.web.Error
             * @instance
             */
            $Object.defineProperty(Error.prototype, "_request_id", {
                get: $util.oneOfGetter($oneOfFields = ["request_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Error _runtime_id.
             * @member {"runtime_id"|undefined} _runtime_id
             * @memberof northstar.web.Error
             * @instance
             */
            $Object.defineProperty(Error.prototype, "_runtime_id", {
                get: $util.oneOfGetter($oneOfFields = ["runtime_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Error _url.
             * @member {"url"|undefined} _url
             * @memberof northstar.web.Error
             * @instance
             */
            $Object.defineProperty(Error.prototype, "_url", {
                get: $util.oneOfGetter($oneOfFields = ["url"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Error _rejection_id.
             * @member {"rejection_id"|undefined} _rejection_id
             * @memberof northstar.web.Error
             * @instance
             */
            $Object.defineProperty(Error.prototype, "_rejection_id", {
                get: $util.oneOfGetter($oneOfFields = ["rejection_id"]),
                set: $util.oneOfSetter($oneOfFields)
            });

            /**
             * Creates a new Error instance using the specified properties.
             * @function create
             * @memberof northstar.web.Error
             * @static
             * @param {northstar.web.Error.$Properties=} [properties] Properties to set
             * @returns {northstar.web.Error} Error instance
             * @type {{
             *   (properties: northstar.web.Error.$Shape): northstar.web.Error & northstar.web.Error.$Shape;
             *   (properties?: northstar.web.Error.$Properties): northstar.web.Error;
             * }}
             */
            Error.create = function(properties) {
                return new Error(properties);
            };

            /**
             * Encodes the specified Error message. Does not implicitly {@link northstar.web.Error.verify|verify} messages.
             * @function encode
             * @memberof northstar.web.Error
             * @static
             * @param {northstar.web.Error.$Properties} message Error message or plain object to encode
             * @param {$protobuf.Writer} [writer] Writer to encode to
             * @returns {$protobuf.Writer} Writer
             */
            Error.encode = function (message, writer, _depth) {
                if (!writer)
                    writer = $Writer.create();
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                if (message.detail != null && $Object.hasOwnProperty.call(message, "detail"))
                    writer.uint32(/* id 1, wireType 2 =*/10).string(message.detail);
                if (message.status != null && $Object.hasOwnProperty.call(message, "status"))
                    writer.uint32(/* id 2, wireType 2 =*/18).string(message.status);
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id"))
                    writer.uint32(/* id 3, wireType 2 =*/26).string(message.request_id);
                if (message.runtime_id != null && $Object.hasOwnProperty.call(message, "runtime_id"))
                    writer.uint32(/* id 4, wireType 2 =*/34).string(message.runtime_id);
                if (message.url != null && $Object.hasOwnProperty.call(message, "url"))
                    writer.uint32(/* id 5, wireType 2 =*/42).string(message.url);
                if (message.rejection_id != null && $Object.hasOwnProperty.call(message, "rejection_id"))
                    writer.uint32(/* id 6, wireType 2 =*/50).string(message.rejection_id);
                if (message.$unknowns != null && $Object.hasOwnProperty.call(message, "$unknowns"))
                    for (let i = 0; i < message.$unknowns.length; ++i)
                        writer.raw(message.$unknowns[i]);
                return writer;
            };

            /**
             * Decodes an Error message from the specified reader or buffer.
             * @function decode
             * @memberof northstar.web.Error
             * @static
             * @param {$protobuf.Reader|Uint8Array} reader Reader or buffer to decode from
             * @param {number} [length] Message length if known beforehand
             * @returns {northstar.web.Error & northstar.web.Error.$Shape} Error
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            Error.decode = function (reader, length, _end, _depth, _target) {
                if (!(reader instanceof $Reader))
                    reader = $Reader.create(reader);
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $Reader.recursionLimit)
                    throw $Error("max depth exceeded");
                let end, message;
                if (length === $undefined)
                    end = reader.len;
                else {
                    end = reader.pos + length;
                    if (end > reader.len)
                        throw $RangeError("index out of range");
                    length = reader.len;
                    reader.len = end;
                }
                message = _target || new $root.northstar.web.Error();
                while (reader.pos < end) {
                    let start = reader.pos;
                    let tag = reader.tag();
                    if (tag === _end) {
                        _end = $undefined;
                        break;
                    }
                    let wireType = tag & 7;
                    switch (tag >>>= 3) {
                    case 1: {
                            if (wireType !== 2)
                                break;
                            message.detail = reader.stringVerify();
                            message._detail = "detail";
                            continue;
                        }
                    case 2: {
                            if (wireType !== 2)
                                break;
                            message.status = reader.stringVerify();
                            message._status = "status";
                            continue;
                        }
                    case 3: {
                            if (wireType !== 2)
                                break;
                            message.request_id = reader.stringVerify();
                            message._request_id = "request_id";
                            continue;
                        }
                    case 4: {
                            if (wireType !== 2)
                                break;
                            message.runtime_id = reader.stringVerify();
                            message._runtime_id = "runtime_id";
                            continue;
                        }
                    case 5: {
                            if (wireType !== 2)
                                break;
                            message.url = reader.stringVerify();
                            message._url = "url";
                            continue;
                        }
                    case 6: {
                            if (wireType !== 2)
                                break;
                            message.rejection_id = reader.stringVerify();
                            message._rejection_id = "rejection_id";
                            continue;
                        }
                    }
                    reader.skipType(wireType, _depth, tag);
                    if (!reader.discardUnknown) {
                        $util.makeProp(message, "$unknowns", false);
                        (message.$unknowns || (message.$unknowns = [])).push(reader.raw(start, reader.pos));
                    }
                }
                if (length !== $undefined) {
                    if (reader.pos !== end)
                        throw $RangeError("index out of range");
                    reader.len = length;
                }
                if (_end !== $undefined)
                    throw $Error("missing end group");
                return message;
            };

            /**
             * Verifies an Error message.
             * @function verify
             * @memberof northstar.web.Error
             * @static
             * @param {Object.<string,*>} message Plain object to verify
             * @returns {string|null} `null` if valid, otherwise the reason why it is not
             */
            Error.verify = function (message, _depth) {
                if (typeof message !== "object" || message === null)
                    return "object expected";
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    return "max depth exceeded";
                let properties = {};
                if (message.detail != null && $Object.hasOwnProperty.call(message, "detail")) {
                    properties._detail = 1;
                    if (!$util.isString(message.detail))
                        return "detail: string expected";
                }
                if (message.status != null && $Object.hasOwnProperty.call(message, "status")) {
                    properties._status = 1;
                    if (!$util.isString(message.status))
                        return "status: string expected";
                }
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    properties._request_id = 1;
                    if (!$util.isString(message.request_id))
                        return "request_id: string expected";
                }
                if (message.runtime_id != null && $Object.hasOwnProperty.call(message, "runtime_id")) {
                    properties._runtime_id = 1;
                    if (!$util.isString(message.runtime_id))
                        return "runtime_id: string expected";
                }
                if (message.url != null && $Object.hasOwnProperty.call(message, "url")) {
                    properties._url = 1;
                    if (!$util.isString(message.url))
                        return "url: string expected";
                }
                if (message.rejection_id != null && $Object.hasOwnProperty.call(message, "rejection_id")) {
                    properties._rejection_id = 1;
                    if (!$util.isString(message.rejection_id))
                        return "rejection_id: string expected";
                }
                return null;
            };

            /**
             * Creates an Error message from a plain object. Also converts values to their respective internal types.
             * @function fromObject
             * @memberof northstar.web.Error
             * @static
             * @param {Object.<string,*>} object Plain object
             * @returns {northstar.web.Error} Error
             */
            Error.fromObject = function (object, _depth) {
                if (object instanceof $root.northstar.web.Error)
                    return object;
                if (!$util.isObject(object))
                    throw $TypeError(".northstar.web.Error: object expected");
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let message = new $root.northstar.web.Error();
                if (object.detail != null)
                    message.detail = $String(object.detail);
                if (object.status != null)
                    message.status = $String(object.status);
                if (object.request_id != null)
                    message.request_id = $String(object.request_id);
                if (object.runtime_id != null)
                    message.runtime_id = $String(object.runtime_id);
                if (object.url != null)
                    message.url = $String(object.url);
                if (object.rejection_id != null)
                    message.rejection_id = $String(object.rejection_id);
                return message;
            };

            /**
             * Creates a plain object from an Error message. Also converts values to other types if specified.
             * @function toObject
             * @memberof northstar.web.Error
             * @static
             * @param {northstar.web.Error} message Error
             * @param {$protobuf.IConversionOptions} [options] Conversion options
             * @returns {Object.<string,*>} Plain object
             */
            Error.toObject = function (message, options, _depth) {
                if (!options)
                    options = {};
                if (_depth === $undefined)
                    _depth = 0;
                if (_depth > $util.recursionLimit)
                    throw $Error("max depth exceeded");
                let object = {};
                if (message.detail != null && $Object.hasOwnProperty.call(message, "detail")) {
                    object.detail = message.detail;
                    if (options.oneofs)
                        object._detail = "detail";
                }
                if (message.status != null && $Object.hasOwnProperty.call(message, "status")) {
                    object.status = message.status;
                    if (options.oneofs)
                        object._status = "status";
                }
                if (message.request_id != null && $Object.hasOwnProperty.call(message, "request_id")) {
                    object.request_id = message.request_id;
                    if (options.oneofs)
                        object._request_id = "request_id";
                }
                if (message.runtime_id != null && $Object.hasOwnProperty.call(message, "runtime_id")) {
                    object.runtime_id = message.runtime_id;
                    if (options.oneofs)
                        object._runtime_id = "runtime_id";
                }
                if (message.url != null && $Object.hasOwnProperty.call(message, "url")) {
                    object.url = message.url;
                    if (options.oneofs)
                        object._url = "url";
                }
                if (message.rejection_id != null && $Object.hasOwnProperty.call(message, "rejection_id")) {
                    object.rejection_id = message.rejection_id;
                    if (options.oneofs)
                        object._rejection_id = "rejection_id";
                }
                return object;
            };

            /**
             * Converts this Error to JSON.
             * @function toJSON
             * @memberof northstar.web.Error
             * @instance
             * @returns {Object.<string,*>} JSON object
             */
            Error.prototype.toJSON = function() {
                return Error.toObject(this, $protobuf.util.toJSONOptions);
            };

            /**
             * Gets the type url for Error
             * @function getTypeUrl
             * @memberof northstar.web.Error
             * @static
             * @param {string} [prefix] Custom type url prefix, defaults to `"type.googleapis.com"`
             * @returns {string} The type url
             */
            Error.getTypeUrl = function(prefix) {
                if (prefix === $undefined)
                    prefix = "type.googleapis.com";
                return prefix + "/northstar.web.Error";
            };

            return Error;
        })();

        return web;
    })();

    return northstar;
})();

export {
  $root as default
};
