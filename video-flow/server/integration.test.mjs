import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, rmSync, readFileSync, existsSync} from 'node:fs';
import path from 'node:path';
import {tmpdir} from 'node:os';
import {setTimeout as pause} from 'node:timers/promises';
import http from 'node:http';
import {configuration} from './config.mjs';
import {createApp} from './app.mjs';
import {openStore} from './database.mjs';
import {generationService} from './generation.mjs';
import {createProject, importRows, scriptRows, patchProject} from './domain.mjs';
import {processFile, ffmpeg, probe} from './media.mjs';
import {atomic, within, hash} from './core.mjs';
import {buildTimeline} from './timeline.mjs';
import {exportArchive, restoreArchive} from './archive.mjs';

async function settle(service) {for(let i=0;i<300;i++){if(service.stats().every((q)=>q.active===0&&q.pending===0))return;await pause(10);}throw new Error('Queue failed to settle');}
function fixture(t) {const root=mkdtempSync(path.join(tmpdir(),'flow-integration-')),resources=[];t.after(async()=>{for(const cleanup of resources.reverse())await cleanup();rmSync(root,{recursive:true,force:true});});const config=configuration({DATA_DIR:root},root);config.poll=1;config.jobTimeout=1000;return {root,config,cleanup:(callback)=>resources.push(callback)};}
async function media(root) {
  const image=path.join(root,'source.png'),audio=path.join(root,'source.mp3');
  await processFile(ffmpeg,['-y','-f','lavfi','-i','color=c=navy:s=320x180:d=0.3','-frames:v','1','-threads','1',image]);
  await processFile(ffmpeg,['-y','-f','lavfi','-i','sine=frequency=440:duration=0.4','-c:a','libmp3lame',audio]);
  return {image:readFileSync(image),audio:readFileSync(audio)};
}
test('HTTP CRUD, CSRF/host protections, conflicts and upload validation',async(t)=>{
  const {root,config,cleanup}=fixture(t), runtime=createApp(config);
  const server=runtime.app.listen(0,'127.0.0.1');await new Promise((resolve)=>server.once('listening',resolve));
  cleanup(async()=>{await new Promise((resolve)=>server.close(resolve));await runtime.close();});
  const base=`http://127.0.0.1:${server.address().port}`;
  let response=await fetch(`${base}/api/state`), state=await response.json();assert.equal(state.projects.length,0);
  response=await fetch(`${base}/api/projects`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'blocked'})});assert.equal(response.status,403);assert.ok((await response.json()).requestId);
  response=await fetch(`${base}/api/state`,{headers:{Origin:'https://malicious.example'}});assert.equal(response.status,403);
  const hostileStatus=await new Promise((resolve,reject)=>{http.get(`${base}/api/state`,{headers:{Host:'attacker.example'}},(reply)=>{reply.resume();resolve(reply.statusCode);}).on('error',reject);});assert.equal(hostileStatus,403);
  const send=(route,body,method='POST')=>fetch(base+route,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(body)});
  response=await send('/api/projects',{title:'Три сцены',script:'Один.\n\nДва.\n\nТри.'});assert.equal(response.status,201);const project=(await response.json()).project;
  response=await send(`/api/projects/${project.id}`,{revision:1,title:'Обновлено'},'PATCH');assert.equal(response.status,200);
  response=await send(`/api/projects/${project.id}`,{revision:1,title:'Устарело'},'PATCH');assert.equal(response.status,409);
  response=await send(`/api/projects/${project.id}/audio`,{data:Buffer.from('not audio').toString('base64')});assert.equal(response.status,422);
  const source=await media(root);response=await send(`/api/projects/${project.id}/audio`,{data:source.audio.toString('base64')});assert.equal(response.status,200);const withAudio=await response.json();assert.ok(withAudio.uploads.project.duration>0);
  response=await fetch(`${base}/files/${withAudio.uploads.project.path}`);assert.equal(response.status,200);
  response=await fetch(`${base}/api/missing`);assert.equal(response.status,404);assert.ok(!(await response.text()).includes('stack'));
  response=await send(`/api/projects/${project.id}/render`,{});assert.equal(response.status,409);assert.ok((await response.json()).details.missing.length===3);
  response=await fetch(`${base}/api/integrity`);assert.equal(response.status,200);
});
test('durable generation reuses external tasks; changed input cannot accept late output',async(t)=>{
  const {root,config,cleanup}=fixture(t), source=await media(root),store=openStore(root);cleanup(()=>store.close());
  config.providers.fastgen.key='test';let creates=0,statuses=0;
  const clients={fastgen:{create:async()=>({id:`external-${++creates}`}),status:async()=>{statuses++;return {status:'succeeded',usage:{credits:1}};},result:async()=>source.image}};
  let service=generationService(store,clients,config);
  let project=importRows(createProject(),scriptRows('Первый.\n\nВторой.\n\nТретий.'));
  project.frames.forEach((f)=>{f.imagePrompt='Landscape';f.approved=true;});project=store.put(project,null);
  const first=service.enqueue(project.id,'image',project.frames[0].id);service.enqueue(project.id,'image',project.frames[0].id);await settle(service);
  assert.equal(creates,1);assert.equal(store.get(project.id).cost.length,1);assert.ok(store.get(project.id).frames[0].image);
  service.enqueue(project.id,'image',project.frames[0].id);await settle(service);assert.equal(creates,1);
  // Crash-recovery simulation: an accepted external ID with no local asset is polled, never recreated.
  const op=store.operations.get(first.key);store.operations.put({...op,status:'running'});store.mutate(project.id,(p)=>{p.frames[0].image=null;return p;});
  await service.close();service=generationService(store,clients,config);service.enqueue(project.id,'image',project.frames[0].id);await settle(service);assert.equal(creates,1);assert.equal(statuses,2);assert.equal(store.get(project.id).cost.length,1);
  service.enqueue(project.id,'image',project.frames[0].id,{newTake:true});await settle(service);assert.equal(creates,2);assert.equal(store.get(project.id).cost.length,2);
  // Keep a provider response in flight while changing the prompt.
  let release;const gate=new Promise((resolve)=>{release=resolve;});clients.fastgen.result=async()=>{await gate;return source.image;};
  service.enqueue(project.id,'image',project.frames[1].id);await pause(20);
  store.mutate(project.id,(p)=>patchProject(p,{frames:[{id:project.frames[1].id,imagePrompt:'Changed'}]}));release();await settle(service);
  assert.equal(store.get(project.id).frames[1].image,null);assert.ok(store.operations.list().some((entry)=>entry.status==='error'&&entry.error));
  await service.close();
});
test('ambiguous paid POST blocks automatic retries; explicit acknowledged new take only',async(t)=>{
  const {config,cleanup}=fixture(t),store=openStore(config.root);cleanup(()=>store.close());config.providers.elevenlabs.key='test';let sent=0;
  const service=generationService(store,{elevenlabs:{speech:async()=>{sent++;throw new Error('response lost');}}},config);
  const project=importRows(createProject(),scriptRows('Текст'));project.models.voiceId='voice';store.put(project,null);
  const op=service.enqueue(project.id,'voice',project.blocks[0].id);await settle(service);assert.equal(store.operations.get(op.key).status,'uncertain');
  assert.throws(()=>service.enqueue(project.id,'voice',project.blocks[0].id),{status:409});assert.equal(sent,1);
  assert.throws(()=>service.enqueue(project.id,'voice',project.blocks[0].id,{newTake:true}),{status:409});
  service.enqueue(project.id,'voice',project.blocks[0].id,{newTake:true,acknowledgeUncertain:true});await settle(service);assert.equal(sent,2);await service.close();
});
test('archive restores media, not secrets; 202-frame timeline remains deterministic',async(t)=>{
  const {root,cleanup}=fixture(t),store=openStore(root);cleanup(()=>store.close());const source=await media(root);
  let project=importRows(createProject({title:'Архив'}),scriptRows('Один.\n\nДва.\n\nТри.'));project.options.voiceoverSkipped=true;
  for(const frame of project.frames){const relative=`${project.id}/images/${frame.id}.png`;atomic(within(store.projectsDir,relative),source.image);frame.image={path:relative,duration:null,provider:'upload'};}
  project=store.put(project,null);const zip=await exportArchive(store,project.id),restored=await restoreArchive(store,zip);
  assert.notEqual(restored.id,project.id);assert.equal(restored.frames.length,3);assert.ok(restored.frames.every((f)=>existsSync(within(store.projectsDir,f.image.path))));assert.equal(buildTimeline(restored).durationInFrames,360);
  const many=importRows(createProject(),scriptRows(Array.from({length:202},(_,i)=>`Текст ${i}`).join('\n\n')));many.options.voiceoverSkipped=true;many.frames.forEach((f)=>{f.image={path:'test.png'};});
  assert.equal(buildTimeline(many).visuals.length,202);assert.equal(buildTimeline(many).durationInFrames,202*120);assert.equal(hash(buildTimeline(many)),hash(buildTimeline(many)));
});
test('ffprobe reads measured audio duration without trusting filename',async(t)=>{
  const {root}=fixture(t),source=await media(root),file=path.join(root,'audio.unknown');atomic(file,source.audio);const result=await probe(file);assert.ok(result.duration>=.4&&result.duration<.6);
});
