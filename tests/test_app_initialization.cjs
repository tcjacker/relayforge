const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const base = path.join(__dirname, '..', 'app', 'src');
function load(file, mocks, extras = {}) {
  const context = vm.createContext({ require: (id) => id === './locale' ? require('../app/src/main/locale') : Object.hasOwn(mocks, id) ? mocks[id] : require(id), module: { exports: {} }, process, Buffer, setTimeout, clearTimeout, ...extras });
  vm.runInContext(fs.readFileSync(path.join(base, file), 'utf8'), context);
  return context.module.exports;
}
test('failure context survives reload and retry reuses one diagnostic session', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-init-'));
  const mocks = { electron: { app: { getPath: () => dir } }, './config': { BUNDLED: '/app with space/rf.py', PYTHON: 'python3' } };
  try {
    let init = load('main/initialization.js', mocks);
    init.fail('/repo', '生成大图草稿', { stderr: 'model login failed', stdout: 'diagnostic details' });
    const session = init.repair('/repo');
    init = load('main/initialization.js', mocks);
    assert.equal(init.get('/repo').stage, '生成大图草稿');
    assert.equal(init.repair('/repo').session_id, session.session_id);
    assert.match(init.prompt(init.forSession(session.session_id)), /model login failed/);
    assert.match(init.prompt(init.forSession(session.session_id)), /diagnostic details/);
    assert.match(init.prompt(init.forSession(session.session_id)), /'\/app with space\/rf.py'/);
    assert.match(init.prompt(init.forSession(session.session_id)), /不要直接使用 --force/);
    init.clear('/repo');
    assert.equal(init.get('/repo'), null);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
function projectHarness({ bootstrap, initFails = false } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-project-'));
  const root = path.join(dir, 'repo'); fs.mkdirSync(root);
  const failures = new Map(), calls = [], sent = [];
  const state = { project: root, runs: new Map(), win: null };
  const failureStore = {
    get: (r) => failures.get(r) || null,
    fail: (r, stage, out) => failures.set(r, { root: r, stage, error: out.stderr || out.stdout }),
    clear: (r) => failures.delete(r),
    repair: (r) => ({ session_id: 'repair-id', blank: true, root: r }),
  };
  const project = load('main/project.js', {
    electron: { app: { getPath: () => dir }, dialog: { showErrorBox() { throw Error('unexpected blocking error dialog'); } }, Menu: {} },
    './config': { HOME_REPO: root, RF_HOME_REAL: '/home/rf', YES: true },
    './demo': { session: () => null, info: () => null }, './initialization': failureStore, './state': { state, send: (channel, value) => sent.push({ channel, value }) },
    './app-menu': { refresh() {}, setWindowReady() {} },
    './requirements': { pushReqs() {} }, './sessions': { stopAll() {} }, './services': { stopAll() {} }, './merge': { pause() {} }, './conflicts': { reset() {} }, './summary': { sweep() { return Promise.resolve(null); } },
    './backend': {
      lastLine: (s) => s.trim().split('\n').at(-1),
      exec: async (_cmd, args) => {
        calls.push(args);
        if (args.includes('--show-toplevel')) return { ok: true, stdout: root, stderr: '' };
        return { ok: true, stdout: '', stderr: '' };
      },
      rfBundled: async (args) => {
        calls.push(args);
        if (args[0] === 'init') {
          if (initFails) return { ok: false, stdout: '', stderr: 'permission denied during init' };
          fs.mkdirSync(path.join(root, '.relayforge'));
          return { ok: true, stdout: '', stderr: '' };
        }
        if (args[0] === 'bootstrap') return bootstrap ? bootstrap() : { ok: false, stdout: '', stderr: 'model login failed\nfull details' };
        return { ok: true, stdout: '{}', stderr: '' };
      },
    },
  }, { global: {} });
  return { root, dir, project, failures, calls, sent, state, cleanup: () => fs.rmSync(dir, { recursive: true, force: true }) };
}
test('initialization failure keeps the selected repository open for AI recovery', async () => {
  const h = projectHarness({ initFails: true });
  try {
    h.state.project = '/previous';
    assert.equal(await h.project.openProject(h.root), true);
    assert.equal(h.state.project, h.root);
    assert.match(h.project.info().initialization.failure.error, /permission denied/);
    assert.equal(h.project.repairInitialization().session_id, 'repair-id');
  } finally { h.cleanup(); }
});
test('bootstrap failure retains full context, releases running state and is shown again on info', async () => {
  const h = projectHarness();
  try {
    h.state.project = '/previous'; await h.project.openProject(h.root);
    assert.equal(h.project.info().initialization.running, true);
    await new Promise((r) => setImmediate(r));
    const status = h.project.info().initialization;
    assert.equal(status.running, false);
    assert.equal(status.failure.stage, '生成大图草稿');
    assert.match(status.failure.error, /model login failed\nfull details/);
    assert.equal(h.calls.filter((args) => args[0] === 'bootstrap').length, 1);
    assert.equal(h.sent.at(-1).channel, 'project:initialization');
    assert.equal(h.project.repairInitialization().session_id, 'repair-id');
  } finally { h.cleanup(); }
});
test('bootstrap exceptions become a recoverable failure and never leave the spinner active', async () => {
  const h = projectHarness({ bootstrap: () => { throw Error('unexpected CLI error'); } });
  try {
    h.state.project = '/previous'; await h.project.openProject(h.root); h.project.info();
    await new Promise((r) => setImmediate(r));
    assert.equal(h.project.info().initialization.running, false);
    assert.match(h.project.info().initialization.failure.error, /unexpected CLI error/);
  } finally { h.cleanup(); }
});
test('repair verification preserves failures until files and facts exist', async () => {
  const h = projectHarness();
  try {
    h.failures.set(h.root, { error: 'original failure' });
    assert.match((await h.project.checkInitialization()).error, /初始化文件仍不完整/);
    fs.mkdirSync(path.join(h.root, '.relayforge'), { recursive: true });
    fs.mkdirSync(path.join(h.root, '.relayforge/bin'));
    for (const file of ['.relayforge/FORMAT.md', '.relayforge/bin/rf.py', 'AGENTS.md', 'CLAUDE.md']) fs.writeFileSync(path.join(h.root, file), 'content');
    assert.match((await h.project.checkInitialization()).error, /还没有完整的大图草稿/);
    for (const dir of ['modules', 'features']) {
      fs.mkdirSync(path.join(h.root, '.relayforge', dir)); fs.writeFileSync(path.join(h.root, '.relayforge', dir, 'fact.md'), 'content');
    }
    assert.equal((await h.project.checkInitialization()).ok, true);
    assert.equal(h.failures.has(h.root), false);
    assert.ok(!h.calls.find((args) => args.includes('--porcelain')).includes('.relayforge'));
  } finally { h.cleanup(); }
});
function attentionHarness() {
  const handlers = {}, nodes = {}, timers = new Map(); let timerId = 0;
  class Node {
    constructor() { this.hidden = false; this.textContent = ''; this.children = []; this.events = {}; this.dataset = {}; }
    addEventListener(name, fn) { this.events[name] = fn; }
    replaceChildren(...children) { this.children = children; }
    querySelector() { return this.children.at(-1); }
    setAttribute() {}
  }
  const get = (id) => nodes[id] || (nodes[id] = new Node());
  const shell = {};
  for (const event of ['Waiting', 'Running', 'Notice', 'Initialization', 'Goto']) shell['on' + event] = (fn) => handlers[event] = fn;
  shell.repairInitialization = async () => ({ session_id: 'repair', blank: true, title: '修复初始化' });
  shell.checkInitialization = async () => ({ error: '仍需修复' });
  const context = vm.createContext({
    $: get, el: (_tag, _cls, text) => Object.assign(new Node(), { textContent: text }), window: { shell },
    requestAnimationFrame: (fn) => fn(), refit() {}, list() {}, waiting: {}, running: {}, past: [],
    view() {}, select(id) { context.selected = id; },
    setTimeout: (fn) => { timers.set(++timerId, fn); return timerId; }, clearTimeout: (id) => timers.delete(id),
  });
  for (const file of ['html-text', 'attention']) {
    vm.runInContext(fs.readFileSync(path.join(base, 'renderer/scripts', file + '.js'), 'utf8'), context);
  }
  context.wireAttention();
  return { context, get, handlers, timers };
}
test('transient notices auto-dismiss, cancel older timers and have a close control', () => {
  const h = attentionHarness();
  h.handlers.Notice('first'); h.handlers.Notice('second');
  assert.equal(h.timers.size, 1);
  assert.equal(h.get('notice').hidden, false);
  assert.equal(h.get('notice').children[0].textContent, 'second');
  assert.equal(h.get('notice').children[1].textContent, '×');
  [...h.timers.values()][0]();
  assert.equal(h.get('notice').hidden, true);
  h.handlers.Notice('third'); h.get('notice').events.click();
  assert.equal(h.get('notice').hidden, true);
});
test('failure status can close and reopen; retry navigates into the AI repair chat', async () => {
  const h = attentionHarness();
  const status = { failure: { stage: '生成大图草稿', error: 'login failed' }, running: false };
  h.handlers.Initialization(status);
  assert.equal(h.get('initialization').hidden, false);
  assert.equal(h.get('init-error').textContent, 'login failed');
  h.get('init-close').events.click(); assert.equal(h.get('initialization').hidden, true);
  h.handlers.Initialization(status); assert.equal(h.get('initialization').hidden, false);
  await h.get('init-retry').events.click();
  assert.equal(h.context.selected, 'session:repair');
  assert.equal(h.context.past.length, 1);
  await h.get('init-retry').events.click(); assert.equal(h.context.past.length, 1);
  await h.get('init-check').events.click(); assert.equal(h.get('init-summary').textContent, '仍需修复');
  assert.equal(h.get('initialization').hidden, false);
  h.handlers.Initialization({ running: false, failure: null }); assert.equal(h.get('initialization').hidden, true);
});

test('projects that failed in an older app also expose AI retry without rerunning initialization blindly', () => {
  const h = projectHarness();
  try {
    fs.mkdirSync(path.join(h.root, '.relayforge'));
    const status = h.project.info().initialization;
    assert.match(status.failure.error, /旧版本可能未保存失败详情/);
    assert.equal(h.calls.length, 0);
    assert.equal(h.project.repairInitialization().session_id, 'repair-id');
  } finally { h.cleanup(); }
});
