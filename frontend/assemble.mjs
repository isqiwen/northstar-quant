// Complete each independently deployable Next.js standalone artifact.
import { cp, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
const roles=process.argv.slice(2);
for(const role of roles.length ? roles : ['data_hub','research','live']) {
 if(!['data_hub','research','live'].includes(role)) throw new Error('Unknown application');
 const build=join(import.meta.dirname,'apps',role,'.next');
 const destination=join(build,'standalone','apps',role,'.next');
 await mkdir(destination,{recursive:true});
 await cp(join(build,'static'),join(destination,'static'),{recursive:true});
}
