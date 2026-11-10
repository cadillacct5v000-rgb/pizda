import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, readFileSync, rmSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import JSZip from 'jszip';
import {createProject, importRows, scriptRows, patchProject, progress} from './domain.mjs';
import {within, decode64, redact, atomic} from './core.mjs';
import {configuration} from './config.mjs';
import {openStore} from './database.mjs';
import {allocateFrames, buildTimeline, subtitles, timecode} from './timeline.mjs';
import {createGenerationQueue} from './queue.mjs';
import {parseDocx, mergePrompts} from './docx.mjs';
import {alignBlocks} from './alignment.mjs';
import {checkDownloadURL, providerClients, poll} from './providers.mjs';

const temporary = (t) => {const root = mkdtempSync(path.join(tmpdir(),'video-flow-')); t.after(() => rmSync(root,{recursive:true,force:true})); return root;};
const project = () => importRows(createProject({title:'Тест'}),scriptRows('Первый блок.\n\nВторой блок.\n\nТретий блок.'));
test('safe storage rejects traversal, absolute paths and NUL', (t) => {
  const root = temporary(t);
  for (const unsafe of ['../key','/etc/passwd','a/../../key','a\\key','a\0b','']) assert.throws(() => within(root,unsafe));
  atomic(within(root,'valid/file.json'),'valid'); assert.equal(readFileSync(within(root,'valid/file.json'),'utf8'),'valid');
  assert.throws(() => decode64('YWJj\n')); assert.throws(() => decode64('YWJj',2)); assert.equal(decode64('YWJj').toString(),'abc');
});
test('config vault keeps secrets server-side; validation and exact redaction', (t) => {
  const root = temporary(t), cfg = configuration({},root); cfg.saveKey('elevenlabs','fake-key-for-contract-test');
  assert.equal(configuration({},root).providers.elevenlabs.key,'fake-key-for-contract-test');
  assert.throws(() => cfg.saveKey('elevenlabs','key\nEVIL=1'));
  assert.throws(() => configuration({PORT:'garbage'},root));
  assert.throws(() => configuration({SYNTX_BASE_URL:'http://localhost'},root));
  assert.ok(!redact('request fake-key-for-contract-test Bearer fake-bearer',['fake-key-for-contract-test']).includes('fake-key'));
});
test('SQLite revision conflicts, persistence, mirror recovery, backup and cascade', async (t) => {
  const root = temporary(t); let store = openStore(root), saved = store.put(project(),null);
  assert.equal(store.db.pragma('journal_mode',{simple:true}),'wal');
  saved = store.put({...saved,title:'Новый'},saved.revision); assert.equal(saved.revision,2);
  assert.throws(() => store.put({...saved,title:'Stale'},1),{status:409});
  store.operations.put({key:'op',projectId:saved.id,status:'paused'});
  const backup = await store.backup(); assert.ok(existsSync(backup));
  atomic(within(store.projectsDir,`${saved.id}/manifest.json`),'broken'); store.close(); store = openStore(root);
  assert.equal(store.get(saved.id).title,'Новый'); assert.equal(JSON.parse(readFileSync(within(store.projectsDir,`${saved.id}/manifest.json`),'utf8')).revision,2);
  store.delete(saved.id); assert.equal(store.operations.list().length,0); assert.equal(store.integrity(),'ok'); store.close();
});
test('domain selectively invalidates downstream artifacts', () => {
  const p = project(); p.frames.forEach((f) => {f.image={path:'image'}; f.video={path:'video'}; f.approved=true;}); p.blocks.forEach((b) => {b.audio={path:'audio'};}); p.render={path:'render'};
  const imageEdit = patchProject(p,{frames:[{id:p.frames[0].id,imagePrompt:'Новый промпт'}]});
  assert.equal(imageEdit.frames[0].image,null); assert.equal(imageEdit.frames[1].image.path,'image'); assert.equal(imageEdit.frames[0].approved,false);
  const motionEdit = patchProject(p,{frames:[{id:p.frames[0].id,motionPrompt:'Pan'}]}); assert.equal(motionEdit.frames[0].image.path,'image'); assert.equal(motionEdit.frames[0].video,null);
  const narrationEdit = patchProject(p,{blocks:[{id:p.blocks[0].id,text:'Другой текст'}]}); assert.equal(narrationEdit.blocks[0].audio,null); assert.equal(narrationEdit.blocks[1].audio.path,'audio'); assert.equal(narrationEdit.frames[0].image.path,'image');
  assert.throws(() => patchProject(p,{frames:[{id:p.frames[0].id,image:{path:'/secrets'}}]}));
  assert.throws(() => patchProject(p,{options:{silentFrameDuration:NaN}}));
  assert.equal(progress({...p,render:{path:'r'}}),100); assert.equal(progress(createProject()),0);
});
test('integer timeline has no gaps, honors voice priority and rounds once', () => {
  for (let count=1;count<250;count++) {const result=allocateFrames(1001,Array.from({length:count},(_,i)=>i+1)); assert.equal(result.reduce((a,b)=>a+b,0),1001); assert.ok(result.every((n)=>n>=1));}
  const p=project(); p.frames.forEach((f)=>{f.image={path:`${p.id}/images/${f.id}.png`};});
  p.uploads.project={path:`${p.id}/audio/common.mp3`,duration:10.09};
  p.blocks.forEach((b)=>{b.audio={path:'ignored',duration:500};});
  const timeline=buildTimeline(p); assert.equal(timeline.audio.length,1); assert.equal(timeline.durationInFrames,303); assert.equal(timeline.visuals.at(-1).from+timeline.visuals.at(-1).durationInFrames,303);
  assert.equal(timeline.audio[0].path,p.uploads.project.path); assert.equal(timeline.timingFallback,true);
  p.options.voiceoverSkipped=true; const silent=buildTimeline(p); assert.equal(silent.audio.length,0); assert.equal(silent.durationInFrames,360);
  p.options.animationEnabled=true; assert.throws(()=>buildTimeline(p),{status:409});
});
test('subtitles use correct hours and UTF-8; no negative or overlapping cues', () => {
  assert.equal(timecode(3661.9996),'01:01:02,000'); assert.equal(timecode(60,true),'00:01:00.000');
  const cues=[{start:-1,end:1,text:'Привет <script> --> мир'},{start:.5,end:3,text:'Второй текст'}];
  const srt=subtitles(cues); assert.ok(srt.includes('00:00:01,000 --> 00:00:03,000')); assert.ok(!srt.includes('<script>')); assert.ok(subtitles(cues,true).startsWith('WEBVTT\n\n'));
});
test('alignment is monotonic for punctuation, repeats, missing words and CJK', () => {
  const blocks=[{id:'1',text:'Привет, мир! Мир.'},{id:'2',text:'Мир снова.'}];
  const timings=alignBlocks(blocks,[{text:'Привет мир мир мир снова',timestamp:[0,5]}],5);
  assert.equal(timings[0].confidence,1); assert.equal(timings[1].confidence,1); assert.ok(timings[1].start>=timings[0].end);
  const missing=alignBlocks([{id:'x',text:'несуществующий'}],[{text:'мир',timestamp:[0,1]}],1); assert.equal(missing[0].confidence,0);
  assert.equal(alignBlocks([{id:'z',text:'你好世界'}],[{text:'你好世界',timestamp:[0,2]}],2)[0].confidence,1);
});
test('queue enforces FIFO/concurrency and survives failure and queued cancellation', async () => {
  const q=createGenerationQueue({name:'test',limit:1}), order=[]; let release;
  const gate=new Promise((resolve)=>{release=resolve;});
  const first=q.enqueue('first',async()=>{order.push('first'); await gate;});
  const joined=q.enqueue('first',()=>{throw new Error('must not run');}); assert.equal(first,joined);
  const cancelled=q.enqueue('cancel',()=>{order.push('cancel');}); q.cancel('cancel'); await assert.rejects(cancelled,{name:'AbortError'});
  const failed=q.enqueue('fail',()=>{order.push('fail');throw new Error('expected');});
  const last=q.enqueue('last',()=>{order.push('last');}); release();
  await first; await assert.rejects(failed,/expected/); await last; await q.close();
  assert.deepEqual(order,['first','fail','last']); assert.equal(q.stats().active,0); assert.equal(q.stats().pending,0);
});
test('DOCX runs merge, Cyrillic, ID-based prompts, duplicates and damaged ZIP',async()=>{
  const document=async(rows)=>{const zip=new JSZip();zip.file('word/document.xml',`<w:document xmlns:w="w"><w:body><w:tbl>${rows.map((cells)=>`<w:tr>${cells.map((value)=>`<w:tc><w:p><w:r><w:t>${value}</w:t></w:r></w:p></w:tc>`).join('')}</w:tr>`).join('')}</w:tbl></w:body></w:document>`);return zip.generateAsync({type:'nodebuffer'});};
  const first=await parseDocx(await document([['ID','Текст','Визуал'],['S01.01','При</w:t></w:r><w:r><w:t>вет','Море'],['S01.02','','Горы']]));
  assert.equal(first.rows[0].narration,'Привет'); assert.equal(first.rows[1].narration,'');
  const prompts=await parseDocx(await document([['S01.02','Mountains'],['S01.01','Ocean']]),true);
  assert.deepEqual(mergePrompts(first,prompts).rows.map((r)=>r.prompt),['Ocean','Mountains']);
  await assert.rejects(parseDocx(await document([['S01.01','a'],['S01.01','b']])),/повтор/);
  await assert.rejects(parseDocx(Buffer.from('bad')),/Поврежд/);
  const many=await parseDocx(await document(Array.from({length:202},(_,i)=>[`S01.${String(i+1).padStart(2,'0')}`,'Текст','Визуал']))); assert.equal(many.rows.length,202);
});
test('download rejects loopback/private addresses, IPv6-mapped and non-HTTPS',async()=>{
  for(const url of ['http://example.com/a','https://127.0.0.1/a','https://[::1]/a','https://user:pass@example.com/a','file:///tmp/a'])await assert.rejects(checkDownloadURL(url));
  for(const address of ['127.0.0.1','10.1.2.3','169.254.169.254','192.168.1.1','100.64.0.1','::ffff:127.0.0.1','fc00::1'])await assert.rejects(checkDownloadURL('https://example.com',async()=>[{address,family:address.includes(':')?6:4}]));
  assert.equal((await checkDownloadURL('https://example.com/path',async()=>[{address:'93.184.216.34',family:4}])).pathname,'/path');
});
test('provider contracts: auth, Lumean order, ElevenLabs voice, SYNTX durable chat',async(t)=>{
  const config=configuration({},temporary(t));for(const provider of Object.values(config.providers))provider.key='contract-key';
  const calls=[], responses=[{data:{id:42}},Buffer.from('mp3'),{id:4,uuid:'chat-uuid'},{id:12}];
  const clients=providerClients(config,async(url,options)=>{calls.push({url,options});const body=responses.shift();return body instanceof Buffer?new Response(body):Response.json(body);});
  assert.equal((await clients.lumean.create({template_id:5,input_text:'тест'})).id,42);assert.equal(calls[0].options.headers['X-API-KEY'],'contract-key');
  await clients.elevenlabs.speech({voiceId:'v',model:'eleven_multilingual_v2',text:'тест'}); assert.equal(calls[1].options.headers['xi-api-key'],'contract-key'); assert.ok(!calls[1].options.body.includes('language_code'));
  let checkpoint;
  const accepted=await clients.syntx.create({stage:'image',model:'gpt-image-2',prompt:'landscape'},undefined,(value)=>{checkpoint=value;assert.equal(calls.length,3);});
  assert.equal(JSON.parse(checkpoint).chatId,4);assert.equal(JSON.parse(accepted.id).messageId,12);assert.ok(calls[3].url.endsWith('/api/v1/design/generate?ai_name=sora-images'));assert.equal(calls[3].options.headers.Authorization,'Bearer contract-key');
  const wire=JSON.parse(calls[3].options.body);assert.equal(wire.settings.model_type,'gpt-image-2');assert.equal(wire.chat_uuid,'chat-uuid');
  const denied=providerClients(config,async()=>Response.json({echo:'contract-key'},{status:401})); await assert.rejects(denied.fastgen.check(),(error)=>!JSON.stringify(error).includes('contract-key')&&error.status===401);
});
test('poll never creates; terminal partial and cancellation have bounded behavior',async()=>{
  let calls=0;const client={status:async()=>({status:++calls===2?'succeeded':'running'})};
  const result=await poll(client,'saved',{jobTimeout:200,poll:1},new AbortController().signal);assert.equal(result.status,'succeeded');assert.equal(calls,2);
  await assert.rejects(poll({status:async()=>({status:'partial'})},'saved',{jobTimeout:200,poll:1},new AbortController().signal),/partial/);
  const controller=new AbortController();controller.abort();await assert.rejects(poll(client,'saved',{jobTimeout:200,poll:1},controller.signal),{name:'AbortError'});
});
