import {configuration} from '../server/config.mjs';
import Database from 'better-sqlite3';
import {artifacts} from '../server/archive.mjs';
import {within} from '../server/core.mjs';
import {existsSync} from 'node:fs';
import path from 'node:path';

const config=configuration(),database=new Database(path.join(config.root,'database.sqlite'),{readonly:true,fileMustExist:true});
try{
  const projects=database.prepare('SELECT payload FROM projects').all().map((row)=>JSON.parse(row.payload));
  const missing=projects.flatMap((p)=>artifacts(p).filter((asset)=>!existsSync(within(path.join(config.root,'projects'),asset.path))).map((asset)=>({projectId:p.id,path:asset.path})));
  const integrity=database.pragma('integrity_check',{simple:true});
  process.stdout.write(JSON.stringify({database:integrity,missing},null,2)+'\n');if(missing.length||integrity!=='ok')process.exitCode=1;
}finally{database.close();}
