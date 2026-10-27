// Syntax, Python bridge contract, and UI recovery checks; no browser/network needed.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const html = fs.readFileSync(root + 'ui.html', 'utf8');
const app = fs.readFileSync(root + 'app.py', 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]);
assert.ok(scripts.length, 'No UI JavaScript found');
for (const [i, script] of scripts.entries()) new vm.Script(script, {filename: `ui-script-${i}.js`});
const methods = new Set([...app.matchAll(/^    def (\w+)\(/gm)].map(m => m[1]));
const used = new Set([...html.matchAll(/pywebview\.api\.(\w+)\(/g)].map(m => m[1]));
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
    console: {warn() {}}, known: new Set(), total: 0, lastQueueId: null,
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
console.log(`UI: ${scripts.length} script syntax check, ${used.size} Python bridge methods, 5 recovery checks passed.`);
