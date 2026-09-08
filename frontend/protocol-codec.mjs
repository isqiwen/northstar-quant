// Compile runtime codecs ahead of time: browsers must never evaluate generated code.
import protobuf from 'protobufjs';
import pbjs from 'protobufjs-cli/pbjs.js';
import { readFile, writeFile, unlink } from 'node:fs/promises';
import { join, basename, dirname } from 'node:path';
const directory = process.argv[2];
const protocol = JSON.parse(await readFile(join(directory,'protocol.json'),'utf8'));
const root=protobuf.Root.fromJSON(protobuf.common.get('google/protobuf/struct.proto'));
for (const [name, definition] of Object.entries(protocol.messages)) {
 const dot=name.lastIndexOf('.');
 const oneofs=Object.fromEntries(Object.entries(definition.fields).filter(([,field])=>field.presence).map(([key])=>['_'+key,{oneof:[key]}]));
 root.define(name.slice(0,dot)).addJSON({[name.slice(dot+1)]:{fields:definition.fields,oneofs}});
}
const intermediate=join(directory,'codec-input.json');
await writeFile(intermediate,JSON.stringify(root.toJSON()));
await new Promise((resolve,reject)=>pbjs.main(['-r','northstar_'+basename(dirname(directory)),'-t','static-module','-w','es6','--no-services','--no-delimited','-o',join(directory,'codec.js'),intermediate],error=>error?reject(error):resolve()));
await unlink(intermediate);
await writeFile(join(directory,'codec.d.ts'),'declare const codec: object;\nexport default codec;\n');
