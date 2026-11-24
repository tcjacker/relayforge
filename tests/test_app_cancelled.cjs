const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const vm = require('node:vm');
const base = path.join(__dirname, '..', 'app', 'src');

function renderer() {
  class Node {
    constructor() { this.children = []; this.hidden = false; this.style = {}; this.dataset = {}; this.textContent = ''; this.events = {}; this.classList = { toggle() {}, add() {}, remove() {} }; }
    appendChild(n) { this.children.push(n); return n; }
    insertAdjacentHTML() {}
    querySelectorAll() { return []; }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(event, fn) { this.events[event] = fn; }
    setAttribute(name, value) { this[name] = value; }
    querySelector() { return null; }
  }
  const nodes = {};
  const get = (id) => nodes[id] || (nodes[id] = new Node());
  const context = vm.createContext({
    document: { getElementById: get, createElement: () => new Node(), createTextNode: (text) => Object.assign(new Node(), { textContent: text }), querySelector: () => get('main') },
    window: { shell: { viewing() {}, traceGet: async () => null, sessionUsage: async () => ({ sessions: [], groups: [] }), predictConflicts: async () => null } },
    setTimeout, clearTimeout, requestAnimationFrame() {}, refit() {}, terminal() { throw new Error('Read-only requirement opened a terminal'); }, show() {},
  });
  for (const file of ['html-text', 'core', 'inbox-model', 'inbox-panel', 'deps-model', 'conflicts-model', 'board-page-model', 'reqs',
    'board', 'stage-menu', 'intake', 'designs', 'stage-tabs-model', 'panel', 'compose', 'trace-model', 'trace', 'session-history-model', 'session-history']) vm.runInContext(fs.readFileSync(path.join(base, 'renderer', 'scripts', file + '.js'), 'utf8'), context);
  get('preview').hidden = get('bench').hidden = true;
  get('board-date-filter').value = 'recent';
  const cancelled = { dropped_on: context.localDate(new Date()), id: 'R-cancelled', title: '取消的需求', state: 'dropped', worktree: '/stale/path', branch: 'req/cancelled', sessions: ['old-session'], sections: { '需求说明': ['保留内容'], '验收条件': ['验收内容'], '变更记录': ['用户取消'] }, designs: [] };
  context.reqs = [cancelled];
  return { context, get, cancelled };
}

test('cancelled requirements are hidden by default and have a recoverable details view', () => {
  const { context: c, get, cancelled } = renderer();
  assert.equal(c.stageOf(cancelled), 'dropped');
  assert.equal(c.hasChat(cancelled), false); // even if a stale worktree is still present
  c.board();
  assert.equal(get('board-columns').children.length, 6);
  c.list(); assert.equal(get('req-list').children[0].textContent, '没有符合筛选条件的需求。');
  c.reqFilter.done = c.reqFilter.dropped = true; c.board();
  assert.equal(get('board-columns').children.length, 6);
  const col = get('board-columns').children[5];
  assert.equal(col.children[0].children[1].textContent, '已取消');
  assert.equal(col.children[1].children[0].textContent, '取消的需求');
  c.select(cancelled.id);
  assert.equal(get('req-state').textContent, '已取消 · 可恢复');
  assert.equal(get('finished').hidden, false);
  assert.match(get('finished').textContent, /恢复取消前的状态/);
  for (const id of ['gate', 'intake', 'accept', 'image']) assert.equal(get(id).hidden, true, id);
  assert.equal(get('sync').disabled, true);
  assert.equal(c.canCompose(), false);
  c.paste('不能发送'); // no terminal or input call
  assert.ok(get('req-body').children.some((n) => n.textContent === '取消记录'));
});

test('active requirements keep their existing stage and chat behavior', () => {
  const { context: c } = renderer();
  const req = { state: 'draft', worktree: '/active', sessions: [], sections: { '验收条件': ['check'] } };
  assert.equal(c.stageOf(req), 'talk');
  assert.equal(c.hasChat(req), true);
  req.state = 'open'; assert.equal(c.stageOf(req), 'build');
  req.steps = req.done = 1; assert.equal(c.stageOf(req), 'check');
  req.state = 'done'; assert.equal(c.stageOf(req), 'done');
});

test('switching between drafts without a worktree keeps the center and sidebar in the intake stage', () => {
  const { context: c, get } = renderer();
  const draft = { state: 'draft', stage: 'talk', worktree: null, sessions: [], sections: {} };
  const direction = { ...draft, id: 'R-20261119-cab9', branch: 'req/20261119-cab9', title: '大图更好找' };
  const idea = { ...draft, id: 'R-20261119-3f2b', branch: 'req/20261119-3f2b', title: '自动清理旧草稿' };
  c.reqs = [direction, idea];
  c.list();
  const selectRow = (index) => get('req-list').children[index].events.click();
  selectRow(0);
  assert.equal(get('title').textContent, direction.title);
  selectRow(1);
  assert.equal(get('title').textContent, idea.title);
  assert.equal(get('req-state').textContent, '立项中');
  const currentStage = get('stage-steps').children.find((step) => step['aria-current'] === 'step');
  assert.equal(currentStage.children[0].textContent, '立项');
  assert.equal(currentStage['aria-selected'], 'true');
  assert.equal(get('finished').hidden, false);
  assert.match(get('finished').textContent, /立项中/);
  assert.doesNotMatch(get('finished').textContent, /确认完成|合并进了主线/);

  const done = { ...draft, id: 'R-done', title: '已完成的需求', stage: 'done', state: 'done' };
  c.reqs.push(done);
  c.reqFilter.done = true;
  c.list();
  selectRow(2);
  assert.equal(get('req-state').textContent, '已完成');
  assert.equal(get('finished').hidden, false);
  assert.match(get('finished').textContent, /已经确认完成/);

  selectRow(1);
  assert.match(get('finished').textContent, /立项中/);
  c.select(null);
  assert.equal(get('finished').hidden, true);
});

test('main process refuses cancelled requirement and historical session entry points', async () => {
  const sent = [], runs = new Map();
  const state = { runs, project: '/project' };
  const mocks = {
    child_process: { spawn() { throw new Error('must not spawn'); } },
    './demo': { session: () => null, info: () => null }, './initialization': { forSession: () => null }, './settings': {}, './codex-sessions': {}, './config': {}, './backend': {}, './state': { state, send: (channel, payload) => sent.push(payload) },
    './requirements': { listReqs: async () => [{ id: 'R-cancelled', state: 'dropped', sessions: ['old-session'] }] },
    './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, seen() {}, stopped() {} }, './claude-settings': {}, './codex-hooks': { flags: () => [], env: () => ({}) }, './ui-names': { guide: () => '' }, './prompts': {}, './session-token': require(path.join(base, 'main', 'session-token')), './splits': {},
  };
  const context = vm.createContext({ require: (id) => mocks[id] || require(id), module: { exports: {} }, clearTimeout });
  vm.runInContext(fs.readFileSync(path.join(base, 'main', 'sessions.js'), 'utf8'), context);
  let killed = 0;
  runs.set('R-cancelled', { id: 'R-cancelled', term: { kill() { killed++; } } });
  for (const id of ['R-cancelled', 'session:old-session']) await context.module.exports.open({ id });
  assert.equal(killed, 1);
  assert.equal(runs.size, 0);
  assert.equal(sent.length, 2);
  assert.ok(sent.every((p) => p.data.includes('不能继续对话')));
});

test('cancelling from the right-click menu asks first, keeps its card and returns to the board', async () => {
  const { context: c, get, cancelled } = renderer();
  const calls = [];
  c.reqs = [{ ...cancelled, state: 'draft', stage: 'talk', gate: { ok: true }, moves: { talk: '当前就是这个状态', build: null, check: null, done: null, dropped: null } }];
  c.current = cancelled.id;
  Object.assign(get('stage-dlg'), { showModal() { this.open = true; }, close() { this.open = false; } });
  get('stage-dlg-no').focus = () => {};
  c.window.shell.setReq = async (id, to) => { calls.push([id, to]); return { ...cancelled, stage: 'dropped', worktree: null }; };
  c.changeStage(c.reqs[0], 'dropped');
  assert.equal(get('stage-dlg').open, true); // 先弹确认框，还没改
  assert.deepEqual(calls, []);
  get('stage-dlg-yes').onclick();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, [['R-cancelled', 'dropped']]);
  assert.equal(c.current, null);
  assert.equal(c.reqs.length, 1);
  assert.equal(c.reqs[0].state, 'dropped');
  assert.equal(get('board-columns').children.length, 6);
  c.reqFilter.dropped = true; c.board();
  assert.equal(get('board-columns').children[5].children[1].children[0].textContent, '取消的需求');
  assert.match(get('notice').children[0].textContent, /已取消/);
});

test('a blocked move is refused before stopping any session; an allowed one closes the sessions first', async () => {
  for (const [state, moves, ok] of [['done', { dropped: '已合并进主线，不能再改' }, false], ['dropped', { dropped: '当前就是这个状态' }, false], ['open', { dropped: null }, true]]) {
    const calls = [], closed = [];
    const mocks = {
      './backend': { rf: async (args) => { calls.push(args); return args[1] === 'list' ? { ok: true, stdout: JSON.stringify([{ id: 'R', state, moves, sessions: ['sid'] }]) } : { ok: true, stdout: JSON.stringify({ id: 'R', state: 'dropped' }) }; }, parse: JSON.parse, lastLine: (x) => x },
      './state': { state: {}, send() {} },
      './services': { stopScope: async () => {} }, // 放弃前停掉从工作目录启动的验收服务
    };
    const file = path.join(base, 'main', 'requirements.js');
    const load = createRequire(file);
    const context = vm.createContext({ require: (id) => mocks[id] || load(id), module: { exports: {} }, setTimeout });
    vm.runInContext(fs.readFileSync(file, 'utf8'), context);
    const result = await context.module.exports.dropReq('R', (id) => { closed.push(id); return false; });
    if (ok) {
      assert.equal(result.state, 'dropped');
      assert.deepEqual([...closed], ['session:sid', 'R']);
      assert.deepEqual([...calls[1]], ['req', 'set', 'R', 'dropped', '--json']);
    } else {
      assert.equal(result.error, moves.dropped);
      assert.deepEqual(closed, []);
      assert.equal(calls.length, 1);
    }
  }
});


test('sidebar filters only affect the session list and always keep six board columns', () => {
  const { context: c, get, cancelled } = renderer();
  c.reqs = [cancelled, { ...cancelled, id: 'R-done', title: '完成', state: 'done', worktree: null, sessions: [] }];
  get('board-date-filter').value = 'all';
  c.wireBoard();
  c.board(); assert.equal(get('board-columns').children.length, 6);
  assert.equal(get('board-columns').children[4].children[1].children[0].textContent, '完成');
  assert.equal(get('board-columns').children[5].children[1].children[0].textContent, '取消的需求');
  get('req-filter').events.change({ target: { value: 'dropped', checked: true } });
  assert.equal(get('req-list').children.length, 1);
  c.select(cancelled.id);
  get('req-filter').events.change({ target: { value: 'dropped', checked: false } });
  assert.equal(c.current, null);
  assert.equal(get('req-list').children[0].textContent, '没有符合筛选条件的需求。');
  get('req-filter').events.change({ target: { value: 'done', checked: true } });
  assert.equal(get('req-list').children[0].children[0].textContent, '完成');
});

test('board date range filters drafts, completed and cancelled requirements by their respective dates', () => {
  const { context: c, get, cancelled } = renderer();
  c.Date = class extends Date { constructor() { super(2027, 0, 4, 0, 30); } };
  const old = '2026-12-28', boundary = '2026-12-29', today = '2027-01-04';
  const card = (id, stage, dates = {}) => ({ ...cancelled, id, title: id, stage, state: stage === 'talk' ? 'draft' : stage, worktree: null, sessions: [], created: old + ' 23:45:00', updated: today, ...dates });
  c.reqs = [
    card('old draft', 'talk'), card('recent draft', 'talk', { created: boundary + ' 23:45:00' }),
    card('in progress', 'build'), card('awaiting review', 'check'),
    card('older', 'done', { completed_on: old }), card('unknown', 'done'),
    card('boundary', 'done', { completed_on: boundary }), card('today', 'done', { completed_on: today }),
    card('future', 'done', { completed_on: '2027-01-05' }),
    card('old cancellation', 'dropped', { dropped_on: old }),
    card('recent cancellation', 'dropped', { dropped_on: boundary }),
  ];
  c.reqFilter.done = true;
  c.wireBoard();
  c.list();
  const boardColumns = () => get('board-columns').children;
  const columns = () => boardColumns().map((col) => col.children.slice(1).map((item) => item.children[0]?.textContent || item.textContent));
  const counts = () => boardColumns().map((col) => col.children[0].children[2].textContent);
  const range = get('board-date-filter');
  const switchTo = (value) => { range.value = value; range.events.change(); };
  const initial = columns();
  assert.equal(range.value, 'recent');
  assert.deepEqual(initial, [['recent draft'], ['in progress'], ['没有'], ['awaiting review'], ['today', 'boundary'], ['recent cancellation']]);
  assert.deepEqual(counts(), ['1/2', '1', '0', '1', '2/5', '1/2']);
  assert.equal(get('req-list').children.length, 9);
  switchTo('all');
  assert.deepEqual(columns(), [['old draft', 'recent draft'], ['in progress'], ['没有'], ['awaiting review'], ['future', 'today', 'boundary', 'older', 'unknown'], ['recent cancellation', 'old cancellation']]); // 已取消也按取消日期从新到旧（R-20261121-c909）
  assert.deepEqual(counts(), ['2', '1', '0', '1', '5', '2']);
  boardColumns()[4].children[4].events.click();
  assert.equal(c.current, 'older');
  assert.equal(get('title').textContent, 'older');
  assert.equal(get('board').hidden, true);
  get('to-board').events.click();
  assert.equal(c.current, null);
  assert.equal(range.value, 'all');
  switchTo('recent');
  assert.deepEqual(columns(), initial);
  c.reqs = [card('older', 'done', { completed_on: old })];
  c.board();
  assert.deepEqual(columns()[4], ['最近 7 天没有完成的需求']);
  c.reqs = [];
  switchTo('all');
  assert.deepEqual(columns()[4], ['没有']);
});

test('manual stages override automatically computed progress', () => {
  const { context: c } = renderer();
  const req = { state: 'open', stage: 'build', steps: 2, done: 2, sections: { '验收条件': ['检查'] } };
  assert.equal(c.stageOf(req), 'build');
  req.stage = 'check'; req.done = 0;
  assert.equal(c.stageOf(req), 'check');
});

test('acceptance buttons follow manual stage changes while the plan is unfinished', async () => {
  const { context: c, get, cancelled } = renderer();
  const req = { ...cancelled, state: 'open', stage: 'build', steps: 2, done: 1 };
  c.reqs = [req];
  c.current = req.id;
  c.terminal = () => { c.terms[req.id] = { term: { focus() {} } }; };
  c.refit = () => {};
  c.window.shell.setReq = async (id, stage) => ({ ...req, stage });
  c.panel();
  for (const stage of ['build', 'check', 'build']) {
    if (c.reqs[0].stage !== stage) {
      c.changeStage(c.reqs[0], stage);
      await new Promise((resolve) => setImmediate(resolve));
    }
    assert.equal(get('req-state').textContent, stage === 'check' ? '待验收' : '进行中');
    for (const id of ['accept', 'run-it', 'done']) assert.equal(get(id).hidden, stage !== 'check', id);
  }
  clearTimeout(c.say.timer);
});

test('acceptance buttons appear when the refreshed requirement reaches automatic review', async () => {
  const { context: c, get, cancelled } = renderer();
  const req = { ...cancelled, state: 'open', stage: 'build', steps: 2, done: 1 };
  c.noteDesigns = () => {};
  let update;
  Object.assign(c.window.shell, {
    onConflicts() {}, getConflicts: () => Promise.resolve(null), predictConflicts: () => Promise.resolve(null), onReqs(fn) { update = fn; },
    listReqs: async () => [req],
    listSessions: async () => [],
  });
  c.current = req.id;
  c.wireReqs();
  await new Promise((resolve) => setImmediate(resolve));
  c.panel();
  assert.equal(get('accept').hidden, true);
  update([{ ...req, stage: 'check', done: 2 }]);
  assert.equal(get('req-state').textContent, '待验收');
  for (const id of ['accept', 'run-it', 'done']) assert.equal(get(id).hidden, false, id);
});

test('legacy unfinished merges have a resume control instead of the acceptance buttons', () => {
  const { context: c, get, cancelled } = renderer();
  const req = { ...cancelled, state: 'done', stage: 'done' };
  c.reqs = [req];
  c.current = req.id;
  c.panel();
  assert.equal(get('accept').hidden, false);
  assert.equal(get('resume-merge').hidden, false);
  for (const id of ['run-it', 'done']) assert.equal(get(id).hidden, true, id);
  c.reqs = [{ ...req, worktree: null }];
  c.panel();
  assert.equal(get('accept').hidden, true);
});

test('a cancelled requirement is restored from the right-click menu and its column is shown again', async () => {
  const { context: c, get, cancelled } = renderer();
  c.reqs = [{ ...cancelled, stage: 'dropped', moves: { talk: '已取消的需求只读', build: '已取消的需求只读', check: '已取消的需求只读', done: '已取消的需求只读', dropped: '当前就是这个状态' } }];
  get('stage-menu').contains = () => false; Object.defineProperty(get('stage-menu'), 'offsetWidth', { value: 200 }); Object.defineProperty(get('stage-menu'), 'offsetHeight', { value: 200 });
  c.innerWidth = c.innerHeight = 1000;
  c.openStageMenu(c.reqs[0], 10, 10);
  const items = get('stage-menu').children.filter((n) => n.children && n.children[1]);
  const restore = items.find((n) => n.children[1].textContent === '恢复取消前的状态');
  assert.ok(restore, 'restore item in the menu');
  assert.ok(!items.some((n) => n.children[1].textContent === '取消这个需求…'));
  const calls = [];
  c.window.shell.restoreReq = async (id) => { calls.push(id); return { ...cancelled, state: 'open', stage: 'check', worktree: '/back' }; };
  restore.events.click({ stopPropagation() {} });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, ['R-cancelled']);
  assert.equal(c.reqs[0].stage, 'check');
  assert.equal(c.reqFilter.check, true);
  assert.match(get('notice').children[0].textContent, /已恢复到待验收/);
});
