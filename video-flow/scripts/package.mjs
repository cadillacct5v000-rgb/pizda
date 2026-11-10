import {readFileSync, mkdirSync, readdirSync, statSync, rmSync} from 'node:fs';
import path from 'node:path';
import JSZip from 'jszip';
import {createHash} from 'node:crypto';
import {appDir} from '../server/config.mjs';

const version=JSON.parse(readFileSync(path.join(appDir,'package.json'),'utf8')).version,zip=new JSZip(),checksums={};
const sha=(bytes)=>createHash('sha256').update(bytes).digest('hex');
const folders=['src','server','remotion','scripts','docs','windows'];
const allowedFiles=['package.json','package-lock.json','index.html','vite.config.ts','tsconfig.json','.env.example','.gitignore','.editorconfig','.gitattributes','.oxlintrc.json','README.md','CONTRIBUTING.md'];
function add(name,bytes){zip.file(name,bytes);checksums[name]=sha(bytes);}
function walk(folder){for(const entry of readdirSync(folder)){const file=path.join(folder,entry),relative=path.relative(appDir,file).split(path.sep).join('/');if(statSync(file).isDirectory())walk(file);else add(`app/${relative}`,readFileSync(file));}}
for(const name of allowedFiles)add(`app/${name}`,readFileSync(path.join(appDir,name)));
for(const folder of folders)walk(path.join(appDir,folder));
const nodeVersion='v24.22.2',nodeName=`node-${nodeVersion}-win-x64.zip`,base=`https://nodejs.org/dist/${nodeVersion}/`;
const checksumResponse=await fetch(`${base}SHASUMS256.txt`);if(!checksumResponse.ok)throw new Error('Node checksum download failed');
const expected=(await checksumResponse.text()).split('\n').find((line)=>line.endsWith(`  ${nodeName}`))?.split(/\s+/)[0];if(!expected)throw new Error('Node checksum missing');
const response=await fetch(base+nodeName);if(!response.ok)throw new Error('Node download failed');const node=Buffer.from(await response.arrayBuffer());if(sha(node)!==expected)throw new Error('Node checksum mismatch');
add(`assets/${nodeName}`,node);
for(const [name,mode] of Object.entries({'Install Video AI Auto.bat':'Install','Start.bat':'Start','Stop.bat':'Stop','Check Backend.bat':'Check','Backend Monitor.bat':'Monitor','Apply Update.bat':'Update'}))add(name,Buffer.from(`@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\\windows\\Manage.ps1" -Action ${mode}\r\nif errorlevel 1 pause\r\n`));
zip.file('package-manifest.json',JSON.stringify({version,nodeVersion,nodeArchive:`assets/${nodeName}`,createdAt:new Date().toISOString(),files:checksums},null,2));
const directory=path.join(appDir,'release');mkdirSync(directory,{recursive:true});
const filename=path.join(directory,`VideoFlow-${version}-Windows-x64.zip`),{writeFileSync}=await import('node:fs');
writeFileSync(filename,await zip.generateAsync({type:'nodebuffer',compression:'DEFLATE',compressionOptions:{level:6}}));
writeFileSync(`${filename}.sha256`,`${sha(readFileSync(filename))}  ${path.basename(filename)}\n`);
// Never put a previous package inside a new package.
for(const name of readdirSync(directory))if(name.endsWith('.tmp'))rmSync(path.join(directory,name));
process.stdout.write(`${path.basename(filename)}: source, lockfile, checked Node ${nodeVersion}; no user data, keys or node_modules.\n`);
