// Developer-only rebuild. The desktop application never downloads or executes scene code.
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const upstream=path.resolve(process.argv[2]||'');
const expected='1cba8ad4dbec08b4346034c0655de0db9382f234';
const note=path.join(upstream,'UPSTREAM_COMMIT');
const actual=fs.existsSync(note)?fs.readFileSync(note,'utf8').trim():spawnSync('git',['-C',upstream,'rev-parse','HEAD'],{encoding:'utf8'}).stdout?.trim();
if(actual!==expected)throw Error('Use the pinned Sketchling source commit '+expected);
const entry=path.join(upstream,'kadrogen-entry.js');
fs.copyFileSync(path.join(root,'drawing_vendor/sketchling/adapter.js'),entry);
const result=spawnSync(process.execPath,[path.join(upstream,'node_modules/esbuild/bin/esbuild'),entry,'--bundle','--minify','--format=iife','--legal-comments=linked','--outfile='+path.join(root,'assets/drawing/sketchling.js')],{stdio:'inherit',cwd:upstream});
if(result.status!==0)process.exit(result.status||1);
console.log('Rebuilt the bounded 2D adapter. Keep upstream and dependency license notices.');
