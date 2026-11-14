// Syntax, Python bridge contract, and UI recovery checks; no browser/network needed.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const html = fs.readFileSync(root + 'ui.html', 'utf8');
const app = fs.readFileSync(root + 'app.py', 'utf8');
const externalScripts=[...html.matchAll(/<script\b[^>]*src="([^"]+)"[^>]*><\/script>/gi)].map(m=>fs.readFileSync(root+m[1],'utf8'));
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]).concat(externalScripts);
assert.ok(scripts.length, 'No UI JavaScript found');
for (const [i, script] of scripts.entries()) new vm.Script(script, {filename: `ui-script-${i}.js`});
const methods = new Set([...app.matchAll(/^    def (\w+)\(/gm)].map(m => m[1]));
const used = new Set([...(html+'\n'+externalScripts.join('\n')).matchAll(/pywebview\.api\.(\w+)\(/g)].map(m => m[1]));
for (const method of used) assert.ok(methods.has(method), `Missing Python API method: ${method}`);

// Execute the actual UI polling/cancel functions against a small in-memory DOM.
const source = scripts.join('\n');
function sliceFunction(name, next) {
  const start = source.indexOf(`async function ${name}(`);
  const end = source.indexOf(next, start);
  assert.ok(start >= 0 && end > start, `Function boundaries: ${name}`);
  return source.slice(start, end);
}
const functionSource = [sliceFunction('poll', 'async function loadConfig'),
  sliceFunction('pollRewrite', 'function renderReview'),
  sliceFunction('cancelJob', 'let shownPct')].join('\n');
function setup(api) {
  const elements = new Map();
  const timers = [];
  const getElement = id => {
    if (!elements.has(id)) elements.set(id, {textContent: '', innerHTML: '', disabled: false,
      style: {}, classList: {add() {}, remove() {}, toggle() {}}});
    return elements.get(id);
  };
  const context = vm.createContext({pywebview: {api}, $: getElement,
    document: {querySelectorAll: () => []}, t: k => k, ORDER: {},
    console: {warn() {}}, known: new Set(), total: 0, lastQueueId: null, reviewProfileId: null, reviewSourceProfile: null,
    animatePct() {}, showWait() {}, buildFrames() {}, markRedo() {},
    renderReview() {}, fmt: String, setTimeout: (fn, ms) => {timers.push({fn, ms});},
  });
  vm.runInContext(functionSource, context);
  return {context, timers, getElement};
}
const idleState = {running: false, queue_active: false, log: [], stage: 'done'};
{
  const s = setup({get_state: async () => {throw new Error('bridge interrupted');}});
  await s.context.poll();
  assert.equal(s.timers.length, 1);
  assert.equal(s.timers[0].fn, s.context.poll);
  assert.match(s.getElement('error').textContent, /bridge interrupted/);
}
{
  const s = setup({get_state: async () => idleState, gallery: async () => {throw new Error('thumbnail unavailable');}});
  await s.context.poll();
  assert.equal(s.getElement('btn_again').disabled, false);
  assert.equal(s.getElement('btn_cancel').disabled, true);
  assert.equal(s.timers.length, 0, 'Idle job should not poll forever');
}
{
  const s = setup({get_state: async () => ({...idleState, running: true}), gallery: async () => {throw new Error('thumbnail unavailable');}});
  await s.context.poll();
  assert.equal(s.timers.length, 1, 'Gallery failure must not stop job status updates');
  assert.equal(s.timers[0].fn, s.context.poll);
}
{
  const s = setup({get_state: async () => {throw new Error('temporary read error');}});
  await s.context.pollRewrite();
  assert.equal(s.timers.length, 1);
  assert.equal(s.timers[0].fn, s.context.pollRewrite);
}
{
  const s = setup({cancel: async () => {throw new Error('retry cancellation');}});
  await s.context.cancelJob();
  assert.equal(s.getElement('btn_cancel').disabled, false);
  assert.match(s.getElement('error').textContent, /retry cancellation/);
}
{
  // Clear stale restore errors before awaiting a new preparation request.
  const elements = new Map();
  const element = id => { if (!elements.has(id)) elements.set(id, {textContent: '', value: 'script', style: {}, disabled: false}); return elements.get(id); };
  element('s_prep2').textContent = 'Error: Неизвестная раскадровка.';
  let resolvePrepare;
  const request = new Promise(resolve => { resolvePrepare = resolve; });
  const context = vm.createContext({boardBusy:false, storyboardControls(){}, $:element, opts:()=>({}),
    pywebview:{api:{prepare_storyboard:()=>request}},boardText:ru=>ru,pollStoryboard(){}});
  vm.runInContext(sliceFunction('createStoryboard','async function pollStoryboard'),context);
  const pending = context.createStoryboard();
  assert.equal(element('s_prep2').textContent,'');
  resolvePrepare({ok:true}); await pending;
}
console.log(`UI: ${scripts.length} script syntax check, ${used.size} Python bridge methods, 6 recovery checks passed.`);
{
  const elements = new Map();
  const element = id => {if (!elements.has(id)) elements.set(id, {disabled:false, hidden:false, textContent:''}); return elements.get(id);};
  const calls = [];
  let accepted = false, polled = 0;
  const context = vm.createContext({$:element, confirm:()=>accepted, poll:()=>polled++, Error,
    pywebview:{api:{get_state:async()=>({video_retry:{operation:'elysium-video:stopped',plan:83}}),
      retry_uncertain_video:async(...args)=>{calls.push(args);return {ok:true};}}}});
  const start = source.indexOf('async function retryUncertainVideo(');
  const end = source.indexOf('async function loadConfig(',start);
  vm.runInContext(source.slice(start,end),context);
  await context.retryUncertainVideo();
  assert.equal(calls.length,0,'Declined recovery must never authorize another paid submission');
  assert.equal(element('btn_video_retry').disabled,false);
  accepted=true;await context.retryUncertainVideo();
  assert.deepEqual(calls[0],['elysium-video:stopped',true]);
  assert.equal(polled,1);
  assert.equal(element('btn_video_retry').hidden,true);
}
