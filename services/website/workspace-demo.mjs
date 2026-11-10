// Standalone sample. Importing this module never touches the DOM.
import { brand } from './brand.mjs';
export const WORKFLOWS = Object.freeze([
  Object.freeze({ id: 'reading', title: 'Weekly reading brief', purpose: 'Turn a topic into a short reading plan with questions to explore.', topic: 'Calmer team workspaces', steps: Object.freeze(['Read the sample source notes', 'Group ideas around your topic', 'Draft a short reading brief']) }),
  Object.freeze({ id: 'release', title: 'Release checklist', purpose: 'Organize example release notes into a reviewable checklist.', topic: 'First public release', steps: Object.freeze(['Read the sample release notes', 'Identify checks and owners', 'Draft the release checklist']) }),
  Object.freeze({ id: 'meeting', title: 'Meeting follow-up', purpose: 'Shape example meeting notes into decisions and next actions.', topic: 'Design review', steps: Object.freeze(['Read the sample meeting notes', 'Separate decisions from questions', 'Draft the follow-up summary']) }),
]);
const SECTIONS = ['Chats', 'Workflows', 'Runs', 'Settings'];
const DEFAULT_TOPIC = WORKFLOWS[0].topic;
const MAX_RUNS = 50;

function sampleRuns() {
  return [
    { id: 'sample-1', title: 'Weekly reading brief', topic: DEFAULT_TOPIC, workflow: 'reading', ageDays: 1, status: 'Completed', progress: 3 },
    { id: 'sample-2', title: 'Release checklist', topic: 'Release notes', workflow: 'release', ageDays: 4, status: 'Failed', progress: 1 },
    { id: 'sample-3', title: 'Meeting follow-up', topic: 'Design review', workflow: 'meeting', ageDays: 9, status: 'Completed', progress: 3 },
    { id: 'sample-4', title: 'Weekly reading brief', topic: 'Research habits', workflow: 'reading', ageDays: 20, status: 'Completed', progress: 3 },
  ];
}
export function createDemoState() {
  return { section: 'Chats', chatTab: 'Chat', runTab: 'Execution', workflow: 'reading', topic: DEFAULT_TOPIC,
    theme: 'light', research: true, agent: 'Writing', preview: false, tools: false, period: 7,
    runs: sampleRuns(), selectedRun: 'sample-1', sequence: 0, notice: '' };
}
export function selectStatistics(state, period = state.period) {
  const days = period === 30 ? 30 : 7;
  const runs = state.runs.filter(run => run.ageDays >= 0 && run.ageDays < days);
  return { days, total: runs.length, completed: runs.filter(run => run.status === 'Completed').length,
    failed: runs.filter(run => run.status === 'Failed').length, running: runs.filter(run => run.status === 'Running').length };
}
export function transitionDemo(state, action) {
  // Unknown or invalid actions are safe no-ops; never mutate an earlier state.
  if (!action || typeof action !== 'object') return state;
  const value = action.value;
  switch (action.type) {
    case 'reset': return { ...createDemoState(), notice: 'Demo reset. Nothing was saved or sent.' };
    case 'section': return SECTIONS.includes(value) ? { ...state, section: value, preview: false } : state;
    case 'chatTab': return ['Chat', 'Outputs'].includes(value) ? { ...state, chatTab: value, preview: false } : state;
    case 'runTab': return ['Execution', 'Statistics'].includes(value) ? { ...state, runTab: value } : state;
    case 'workflow': {
      const workflow = WORKFLOWS.find(item => item.id === value);
      return workflow ? { ...state, workflow: value, topic: workflow.topic } : state;
    }
    case 'topic': return typeof value === 'string' ? { ...state, topic: value.slice(0, 160) } : state;
    case 'theme': return ['light', 'night'].includes(value) ? { ...state, theme: value } : state;
    case 'research': return typeof value === 'boolean' ? { ...state, research: value } : state;
    case 'agent': return ['Research', 'Builder', 'Writing'].includes(value) ? { ...state, agent: value } : state;
    case 'period': return [7, 30, '7', '30'].includes(value) ? { ...state, period: Number(value) } : state;
    case 'tools': return { ...state, tools: !state.tools };
    case 'preview': return { ...state, preview: !state.preview };
    case 'selectRun': return state.runs.some(run => run.id === value) ? { ...state, selectedRun: value, runTab: 'Execution' } : state;
    case 'simulate':
    case 'retry': {
      const previous = state.runs.find(run => run.id === state.selectedRun);
      if (action.type === 'retry' && previous?.status !== 'Failed') return state;
      if (state.runs.length >= MAX_RUNS) return { ...state, notice: 'Sample history is full. Reset the demo to start again.' };
      const workflow = WORKFLOWS.find(item => item.id === (action.type === 'retry' ? previous.workflow : state.workflow));
      if (!workflow) return state;
      const sequence = state.sequence + 1;
      const run = { id: `local-${sequence}`, title: workflow.title, topic: action.type === 'retry' ? previous.topic : state.topic,
        workflow: workflow.id, ageDays: 0, status: 'Running', progress: 0 };
      return { ...state, sequence, runs: [run, ...state.runs], selectedRun: run.id, section: 'Runs', runTab: 'Execution',
        notice: 'Local sample run ready. Advance each step manually; nothing is executed.' };
    }
    case 'next': {
      const selected = state.runs.find(run => run.id === state.selectedRun);
      if (!selected || selected.status !== 'Running' || selected.progress >= 3) return state;
      const progress = selected.progress + 1;
      return { ...state, runs: state.runs.map(run => run.id === selected.id ? { ...run, progress, status: progress === 3 ? 'Completed' : 'Running' } : run),
        notice: progress === 3 ? 'Sample run completed. No real tools were executed.' : `Sample step ${progress} of 3 completed.` };
    }
    default: return state;
  }
}
export function escapeDemoText(value) {
  return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
}
const text = escapeDemoText;
function button(label, type, value = '', extra = '') {
  return `<button type="button" data-action="${type}" data-value="${text(value)}" data-focus="${type}-${text(value)}" ${extra}>${text(label)}</button>`;
}
function tabs(names, active, type, id) {
  return `<div class="wd-tabs" role="tablist" aria-label="${type === 'chatTab' ? 'Chat views' : 'Run views'}">${names.map(name => button(name, type, name,
    `role="tab" id="${id}-${name.toLowerCase()}-tab" aria-controls="${id}-panel" aria-selected="${name === active}" tabindex="${name === active ? 0 : -1}"`)).join('')}</div>`;
}
function chatView(state, id) {
  return `<header class="wd-view-header"><span class="wd-avatar" aria-hidden="true">WR</span><div><h2>Product brief &amp; slides</h2><p>Sample conversation · Writing agent</p></div></header>
    ${tabs(['Chat', 'Outputs'], state.chatTab, 'chatTab', id)}
    <div class="wd-scroll" role="tabpanel" id="${id}-panel" aria-labelledby="${id}-${state.chatTab.toLowerCase()}-tab" tabindex="0">
    ${state.chatTab === 'Chat' ? `<article class="wd-message wd-user"><p class="wd-eyebrow">You · sample message</p><p>Create a product brief and slide outline from these notes: a calmer team workspace, with conversations, research and files together.</p></article>
      <div class="wd-tools">${button('3 tool calls', 'tools', '', `aria-expanded="${state.tools}" aria-controls="${id}-tools"`)}
      ${state.tools ? `<ol id="${id}-tools"><li>Read example notes — identified audience and goals.</li><li>Outline the brief — grouped the first-release ideas.</li><li>Sketch slide headings — arranged the story in six parts.</li></ol><p>Illustrative steps only; no tools were called.</p>` : ''}</div>
      <article class="wd-message"><p class="wd-eyebrow">Writing agent · sample reply</p><h3>Ready to refine</h3><p>The example brief focuses on small teams that want less context switching. The outline covers the audience, problem, approach and first release.</p><p>Open question: which task should the first release make easier?</p>${button('View sample output', 'chatTab', 'Outputs')}</article>
      <p class="wd-note">Read-only example. Messages and agents aren’t sent in this demo.</p>` : `<div class="wd-output"><p class="wd-eyebrow">1 sample output · inline text</p><h3>Product brief</h3><p>An illustrative brief, not a generated document or a downloadable file.</p>${button('Preview product brief', 'preview', '', `aria-expanded="${state.preview}" aria-controls="${id}-preview"`)}</div>
      ${state.preview ? `<article class="wd-preview" id="${id}-preview" aria-label="Sample product brief preview"><div class="wd-preview-top"><h3>Example product brief</h3>${button('Close preview', 'preview')}</div><p class="wd-eyebrow">Illustrative content · not a generated file</p><h4>A calmer team workspace</h4><p><strong>Audience:</strong> small teams collecting ideas across conversations and files.</p><p><strong>Problem:</strong> decisions get lost when context lives in separate places.</p><p><strong>First release:</strong> keep a conversation, research notes and an editable brief together.</p><p><strong>Open question:</strong> which repeated task should we simplify first?</p></article>` : ''}`}
    </div>`;
}
function workflowView(state, id) {
  const workflow = WORKFLOWS.find(item => item.id === state.workflow) || WORKFLOWS[0];
  return `<header class="wd-view-header"><span class="wd-avatar wd-amber" aria-hidden="true">WF</span><div><h2>${workflow.title}</h2><p>Example workflow · manual simulation</p></div></header>
    <div class="wd-scroll"><p>${workflow.purpose}</p><div class="wd-field"><label for="${id}-workflow">Workflow</label><select id="${id}-workflow" data-change="workflow" data-focus="workflow">${WORKFLOWS.map(item => `<option value="${item.id}" ${item.id === state.workflow ? 'selected' : ''}>${item.title}</option>`).join('')}</select></div>
    <label class="wd-field" for="${id}-topic">Topic<input id="${id}-topic" data-input="topic" data-focus="topic" maxlength="160" value="${text(state.topic)}" aria-describedby="${id}-topic-help"></label>
    <p class="wd-note" id="${id}-topic-help">Edit this sample input. It stays in memory and is never sent.</p>
    <h3>Three sample steps</h3><ol class="wd-steps">${workflow.steps.map(step => `<li>${step}</li>`).join('')}</ol>
    ${button('Simulate run', 'simulate', '', 'class="wd-primary"')}<p class="wd-note">No real execution. Use “Next sample step” three times in Runs to finish.</p></div>`;
}
function statisticsView(state, id) {
  const stats = selectStatistics(state);
  return `<div class="wd-field wd-period"><label for="${id}-period">Period</label><select id="${id}-period" data-change="period" data-focus="period"><option value="7" ${state.period === 7 ? 'selected' : ''}>7 days</option><option value="30" ${state.period === 30 ? 'selected' : ''}>30 days</option></select></div>
    <h3>Sample run outcomes</h3><p>${stats.total} sample runs: ${stats.completed} completed, ${stats.failed} failed, ${stats.running} running.</p>
    <figure class="wd-chart"><figcaption>Illustrative sample data · last ${stats.days} days of the fixed demo history. Includes your local simulations.</figcaption>
    <div role="img" aria-label="${stats.completed} completed, ${stats.failed} failed, ${stats.running} running out of ${stats.total} sample runs">${[['Completed', stats.completed], ['Failed', stats.failed], ['Running', stats.running]].map(([label, count]) => `<div class="wd-chart-row"><span>${label}</span><div class="wd-bar-track"><span class="wd-bar wd-bar-${label.toLowerCase()}" style="width:${stats.total ? count / stats.total * 100 : 0}%"></span></div><strong>${count}</strong></div>`).join('')}</div></figure>
    <p class="wd-note">Counts come from this demo’s sample run history, not real usage. No performance estimates.</p>`;
}
function runsView(state, id) {
  const run = state.runs.find(item => item.id === state.selectedRun) || state.runs[0];
  const workflow = WORKFLOWS.find(item => item.id === run.workflow);
  return `<header class="wd-view-header"><span class="wd-avatar wd-amber" aria-hidden="true">RN</span><div><h2>Runs</h2><p>Local sample history · no backend</p></div></header>
    ${tabs(['Execution', 'Statistics'], state.runTab, 'runTab', id)}
    <div class="wd-scroll" role="tabpanel" id="${id}-panel" aria-labelledby="${id}-${state.runTab.toLowerCase()}-tab" tabindex="0">
    ${state.runTab === 'Statistics' ? statisticsView(state, id) : `<div class="wd-field"><label for="${id}-run">Sample execution</label><select id="${id}-run" data-change="selectRun" data-focus="selectRun">${state.runs.map(item => `<option value="${item.id}" ${item.id === run.id ? 'selected' : ''}>${text(item.title)} · ${item.status} · ${item.id.startsWith('local') ? `local #${item.id.slice(6)}` : `${item.ageDays} days ago`}</option>`).join('')}</select></div>
      <div class="wd-run-title"><h3>${text(run.title)}</h3><span class="wd-status wd-status-${run.status.toLowerCase()}">${run.status}</span></div>
      <p class="wd-topic"><strong>Topic:</strong> ${text(run.topic) || '(empty sample topic)'}</p>
      <ol class="wd-timeline">${workflow.steps.map((step, index) => `<li><span class="wd-step-marker" aria-hidden="true">${index < run.progress ? '✓' : index + 1}</span><div><strong>${step}</strong><p>${index < run.progress ? 'Completed in this sample' : run.status === 'Failed' && index === run.progress ? 'Failed: example notes were unavailable.' : index === run.progress && run.status === 'Running' ? 'Ready for your next click' : 'Not started'}</p></div></li>`).join('')}</ol>
      ${run.status === 'Running' ? button('Next sample step', 'next', '', 'class="wd-primary"') : run.status === 'Failed' ? `<div class="wd-error"><p>The sample source notes could not be read. In this example, a retry uses the restored sample notes.</p>${button('Retry sample run', 'retry')}</div>` : `<p class="wd-note">All three sample steps completed. To replay, choose a workflow and simulate another run.</p>${button('Choose a workflow', 'section', 'Workflows')}`}`}
    </div>`;
}
function settingsView(state, id) {
  return `<header class="wd-view-header"><span class="wd-avatar" aria-hidden="true">ST</span><div><h2>Settings</h2><p>Only this demo · in-memory preferences</p></div></header><div class="wd-scroll">
    <h3>Appearance</h3><div class="wd-theme" role="group" aria-label="Demo appearance">${button('Light', 'theme', 'light', `aria-pressed="${state.theme === 'light'}"`)}${button('Night', 'theme', 'night', `aria-pressed="${state.theme === 'night'}"`)}</div>
    <p class="wd-note">Changes this wireframe only, not the website or your device.</p>
    <h3>Sample agent preferences</h3><label class="wd-checkbox"><input type="checkbox" data-change="research" data-focus="research" ${state.research ? 'checked' : ''}>Allow web research</label><p class="wd-note">A sample permission switch. Even when enabled, the demo never accesses the web.</p>
    <div class="wd-field"><label for="${id}-agent">Agent</label><select id="${id}-agent" data-change="agent" data-focus="agent">${['Research', 'Builder', 'Writing'].map(agent => `<option ${agent === state.agent ? 'selected' : ''}>${agent}</option>`).join('')}</select></div>
    <p class="wd-note">No accounts, keys or connections. Reset demo restores every preference.</p></div>`;
}
export function renderWorkspaceDemo(state, instanceId = 'demo-preview') {
  // Keep caller-provided identifiers safe even in pure-render tests.
  const id = `demo-${String(instanceId).replace(/[^a-zA-Z0-9_-]/g, '')}`;
  return `<div class="wd-shell"><header class="wd-top"><div><strong>${text(brand.name)}</strong><span class="wd-sample">Interactive wireframe · sample data</span></div>${button('Reset demo', 'reset')}</header>
    <div class="wd-body"><nav class="wd-nav" aria-label="Demo sections">${SECTIONS.map((section, index) => button(section, 'section', section, `aria-current="${state.section === section ? 'page' : 'false'}" class="wd-nav-${index}"`)).join('')}</nav>
    <div class="wd-content"><aside class="wd-list" aria-label="Workspace overview"><p class="wd-eyebrow">Sample workspace</p><h2>${state.section}</h2>${state.section === 'Chats' ? '<p class="wd-list-item">Product brief &amp; slides</p><p class="wd-note">A conversation and its example output, together.</p>' : state.section === 'Workflows' ? '<p class="wd-note">Choose a recipe, edit a topic, then walk through a local run.</p>' : state.section === 'Runs' ? `<p class="wd-list-item">${state.runs.length} sample runs</p><p class="wd-note">Inspect each step or compare outcomes.</p>` : '<p class="wd-note">Try a local theme and sample agent preferences.</p>'}<span class="wd-local-label">Nothing saved or sent</span></aside>
    <section class="wd-main" aria-label="Demo workspace">${state.section === 'Chats' ? chatView(state, id) : state.section === 'Workflows' ? workflowView(state, id) : state.section === 'Runs' ? runsView(state, id) : settingsView(state, id)}</section></div></div>
    <footer class="wd-footer"><span>Local demo · no live agents or execution</span><span class="wd-live" role="status" aria-live="polite" aria-atomic="true">${text(state.notice)}</span></footer></div>`;
}
let instanceSequence = 0;
export function mountWorkspaceDemo(element) {
  if (!element || typeof element.addEventListener !== 'function') throw new TypeError('mountWorkspaceDemo requires a DOM element');
  const id = `workspace-${++instanceSequence}`;
  let state = createDemoState();
  let root = element;
  const document = element.ownerDocument;
  function render(restoreFocus = false, focusOverride = '') {
    const active = document.activeElement;
    const hadFocus = root.contains(active);
    const key = focusOverride || (hadFocus ? active.getAttribute('data-focus') : '');
    root.classList.add('workspace-demo');
    root.setAttribute('data-demo-theme', state.theme);
    const markup = renderWorkspaceDemo(state, id);
    if (root.querySelector('.wd-shell')) {
      // Preserve the live region and reset control so updates are announced
      // rather than recreating an already-populated live region on every click.
      const template = document.createElement('template');
      template.innerHTML = markup;
      root.querySelector('.wd-body').replaceWith(template.content.querySelector('.wd-body'));
      root.querySelector('.wd-live').textContent = state.notice;
    } else root.innerHTML = markup;
    if (restoreFocus && hadFocus) {
      const controls = [...root.querySelectorAll('[data-focus]')];
      const target = controls.find(control => control.getAttribute('data-focus') === key) || controls.find(control => control.getAttribute('data-focus') === 'reset-');
      target?.focus({ preventScroll: true });
    }
  }
  function dispatch(action, focusOverride = '') {
    const next = transitionDemo(state, action);
    if (next === state) return;
    state = next;
    render(true, focusOverride);
  }
  function click(event) {
    const control = event.target.closest?.('button[data-action]');
    if (!control || !root.contains(control)) return;
    const type = control.dataset.action;
    // Put focus at the next useful action when a clicked control disappears.
    const focus = type === 'simulate' || type === 'retry' ? 'next-' : type === 'next' && state.runs.find(run => run.id === state.selectedRun)?.progress === 2 ? 'section-Workflows' : '';
    dispatch({ type, value: control.dataset.value }, focus);
  }
  function change(event) {
    const control = event.target;
    if (!control.dataset.change) return;
    dispatch({ type: control.dataset.change, value: control.type === 'checkbox' ? control.checked : control.value });
  }
  function input(event) {
    if (event.target.dataset.input === 'topic') state = transitionDemo(state, { type: 'topic', value: event.target.value });
  }
  function keydown(event) {
    if (event.key === 'Escape' && state.preview) {
      event.preventDefault();
      dispatch({ type: 'preview' }, 'preview-');
      return;
    }
    const tab = event.target.closest?.('[role="tab"]');
    if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const list = [...tab.parentElement.querySelectorAll('[role="tab"]')];
    const index = list.indexOf(tab);
    const next = event.key === 'Home' ? list[0] : event.key === 'End' ? list.at(-1) : list[(index + (event.key === 'ArrowRight' ? 1 : -1) + list.length) % list.length];
    event.preventDefault();
    dispatch({ type: next.dataset.action, value: next.dataset.value }, next.dataset.focus);
  }
  root.addEventListener('click', click);
  root.addEventListener('change', change);
  root.addEventListener('input', input);
  root.addEventListener('keydown', keydown);
  render(); // No focus change, and no change to the parent's hidden state.
  return {
    reset() { if (root) dispatch({ type: 'reset' }); },
    destroy() {
      if (!root) return;
      root.removeEventListener('click', click);
      root.removeEventListener('change', change);
      root.removeEventListener('input', input);
      root.removeEventListener('keydown', keydown);
      root.replaceChildren();
      root.classList.remove('workspace-demo');
      root.removeAttribute('data-demo-theme');
      state = null;
      root = null;
    },
  };
}
