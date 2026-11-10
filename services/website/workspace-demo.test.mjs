import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { createDemoState, transitionDemo, selectStatistics, renderWorkspaceDemo, escapeDemoText, WORKFLOWS, mountWorkspaceDemo } from './workspace-demo.mjs';

const act = (state, type, value) => transitionDemo(state, { type, value });

test('module import is DOM-free, including in an environment with forbidden browser globals', () => {
  const url = new URL('./workspace-demo.mjs', import.meta.url).href;
  execFileSync(process.execPath, ['--input-type=module', '-e', `
    for (const key of ['window', 'document', 'localStorage', 'sessionStorage', 'fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource']) {
      Object.defineProperty(globalThis, key, { configurable: true, get() { throw new Error('Forbidden access: ' + key); } });
    }
    await import(${JSON.stringify(url)});
  `]);
  assert.throws(() => mountWorkspaceDemo(null), TypeError);
});

test('initial state has a useful sample conversation and one inline example output', () => {
  const state = createDemoState();
  assert.equal(state.section, 'Chats');
  assert.equal(state.theme, 'light');
  assert.equal(state.research, true);
  assert.equal(state.preview, false);
  const html = renderWorkspaceDemo(state);
  assert.match(html, /Product brief &amp; slides/);
  assert.match(html, /3 tool calls/);
  assert.match(html, /aria-label="Demo sections"/);
  for (const name of ['Chats', 'Workflows', 'Runs', 'Settings']) assert.ok(html.includes(`>${name}</button>`));
  assert.ok(!html.includes('role="status" aria-live="polite" aria-atomic="true"><div'));
  const outputs = renderWorkspaceDemo(act(state, 'chatTab', 'Outputs'));
  assert.match(outputs, /Preview product brief/);
  assert.doesNotMatch(outputs, /download=|<form|<iframe/);
});

test('each workflow defines three understandable steps and simulation takes exactly three manual advances', () => {
  for (const workflow of WORKFLOWS) {
    assert.equal(workflow.steps.length, 3);
    let state = act(createDemoState(), 'workflow', workflow.id);
    assert.equal(state.topic, workflow.topic);
    state = act(state, 'topic', 'Accessible team notes');
    const before = structuredClone(state);
    state = act(state, 'simulate');
    assert.equal(state.section, 'Runs');
    assert.equal(state.runTab, 'Execution');
    assert.equal(state.runs.length, before.runs.length + 1);
    assert.equal(state.runs[0].status, 'Running');
    assert.equal(state.runs[0].progress, 0);
    assert.equal(state.runs[0].topic, 'Accessible team notes');
    for (let step = 1; step <= 3; step++) {
      const previous = state;
      state = act(state, 'next');
      assert.equal(state.runs[0].progress, step);
      assert.equal(state.runs[0].status, step === 3 ? 'Completed' : 'Running');
      assert.equal(previous.runs[0].progress, step - 1);
    }
    assert.equal(act(state, 'next'), state);
    assert.equal(before.runs.length, 4);
  }
});

test('statistics are derived from history for 7 and 30 days and reflect new run outcomes', () => {
  let state = createDemoState();
  assert.deepEqual(selectStatistics(state), { days: 7, total: 2, completed: 1, failed: 1, running: 0 });
  assert.deepEqual(selectStatistics(state, 30), { days: 30, total: 4, completed: 3, failed: 1, running: 0 });
  state = act(state, 'simulate');
  assert.deepEqual(selectStatistics(state), { days: 7, total: 3, completed: 1, failed: 1, running: 1 });
  for (let i = 0; i < 3; i++) state = act(state, 'next');
  assert.deepEqual(selectStatistics(state), { days: 7, total: 3, completed: 2, failed: 1, running: 0 });
  assert.deepEqual(selectStatistics(act(state, 'period', '30')), { days: 30, total: 5, completed: 4, failed: 1, running: 0 });
  const boundaries = { ...state, runs: [
    { ageDays: 0, status: 'Completed' }, { ageDays: 6, status: 'Failed' },
    { ageDays: 7, status: 'Running' }, { ageDays: 29, status: 'Completed' },
    { ageDays: 30, status: 'Completed' }, { ageDays: -1, status: 'Completed' },
  ] };
  assert.deepEqual(selectStatistics(boundaries, 7), { days: 7, total: 2, completed: 1, failed: 1, running: 0 });
  assert.deepEqual(selectStatistics(boundaries, 30), { days: 30, total: 4, completed: 2, failed: 1, running: 1 });
});

test('failed example explains the error and retry creates a new local run without rewriting history', () => {
  let state = act(createDemoState(), 'section', 'Runs');
  state = act(state, 'selectRun', 'sample-2');
  assert.match(renderWorkspaceDemo(state), /example notes were unavailable/);
  assert.match(renderWorkspaceDemo(state), /Retry sample run/);
  state = act(state, 'retry');
  assert.equal(state.runs[0].workflow, 'release');
  assert.equal(state.runs[0].progress, 0);
  assert.equal(state.runs.find(run => run.id === 'sample-2').status, 'Failed');
  for (let i = 0; i < 3; i++) state = act(state, 'next');
  assert.equal(state.runs[0].status, 'Completed');
  assert.equal(act(state, 'retry'), state);
});

test('navigation preserves input, run progress and local settings; reset clears everything', () => {
  let state = act(createDemoState(), 'topic', 'Custom topic');
  state = act(state, 'theme', 'night');
  state = act(state, 'research', false);
  state = act(state, 'agent', 'Research');
  state = act(state, 'simulate');
  state = act(state, 'next');
  state = act(state, 'section', 'Settings');
  state = act(state, 'section', 'Runs');
  assert.equal(state.runs[0].progress, 1);
  assert.equal(state.topic, 'Custom topic');
  assert.equal(state.theme, 'night');
  assert.equal(state.research, false);
  assert.equal(state.agent, 'Research');
  const reset = act(state, 'reset');
  assert.deepEqual({ ...reset, notice: '' }, createDemoState());
  assert.match(reset.notice, /Nothing was saved or sent/);
});

test('state instances and immutable transitions are isolated', () => {
  const a = createDemoState();
  const b = createDemoState();
  const initial = structuredClone(a);
  const deepFreeze = value => {
    if (value && typeof value === 'object') { Object.values(value).forEach(deepFreeze); Object.freeze(value); }
    return value;
  };
  deepFreeze(a);
  let changed = act(a, 'simulate');
  changed = act(changed, 'next');
  assert.deepEqual(a, initial);
  assert.deepEqual(b, initial);
  assert.notEqual(a.runs, b.runs);
  assert.notEqual(a.runs[0], b.runs[0]);
  assert.equal(changed.runs.length, 5);
});

test('invalid actions are bounded no-ops, topic length and local history are capped', () => {
  const state = createDemoState();
  for (const action of [null, {}, { type: 'unknown' }, { type: 'section', value: '<script>' },
    { type: 'workflow', value: 'absent' }, { type: 'theme', value: 'system' }, { type: 'period', value: 365 },
    { type: 'period', value: Symbol('invalid') }, { type: 'period', value: { valueOf() { throw new Error('must not coerce'); } } },
    { type: 'selectRun', value: 'absent' }, { type: 'agent', value: 'Admin' }, { type: 'topic', value: 42 },
    { type: 'research', value: 'true' }, { type: 'chatTab', value: 'missing' }, { type: 'runTab', value: 'missing' },
    { type: 'next' }, { type: 'retry' }]) assert.equal(transitionDemo(state, action), state);
  assert.equal(act(state, 'topic', 'x'.repeat(1000)).topic.length, 160);
  let many = state;
  for (let i = 0; i < 100; i++) many = act(many, 'simulate');
  assert.equal(many.runs.length, 50);
  assert.equal(new Set(many.runs.map(run => run.id)).size, 50);
  assert.match(many.notice, /history is full/);
});

test('topic text and caller-provided identifiers cannot inject HTML', () => {
  const attack = '\"><img src=x onerror=alert(1)><script>alert("x")</script>&\'';
  let state = act(createDemoState(), 'topic', attack);
  state = act(state, 'section', 'Workflows');
  const workflowHtml = renderWorkspaceDemo(state, '\" onclick=\"alert(1)');
  assert.ok(workflowHtml.includes(escapeDemoText(attack)));
  assert.doesNotMatch(workflowHtml, /<img|<script|onclick=/);
  state = act(state, 'simulate');
  const runHtml = renderWorkspaceDemo(state);
  assert.ok(runHtml.includes(escapeDemoText(attack)));
  assert.doesNotMatch(runHtml, /<img|<script/);
});

test('rendered identifiers are instance-prefixed; all CSS selectors are scoped', async () => {
  const html = renderWorkspaceDemo(act(createDemoState(), 'section', 'Workflows'), 'instance-a');
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.ok(ids.length > 0);
  assert.ok(ids.every(id => id.startsWith('demo-instance-a-')));
  assert.equal(new Set(ids).size, ids.length);
  const css = await readFile(new URL('./workspace-demo.css', import.meta.url), 'utf8');
  const selectorBlocks = css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/(?:^|[{}])\s*([^{}]+)\s*\{/g);
  for (const [, selector] of selectorBlocks) {
    if (selector.trim().startsWith('@')) continue;
    assert.ok(selector.trim().startsWith('.workspace-demo'), `unscoped selector: ${selector}`);
  }
});
