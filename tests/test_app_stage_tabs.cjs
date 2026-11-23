// 需求详情的阶段标签页（R-20261121-fe14）：默认停在哪、每个标签放什么和有哪些按钮、没到的阶段只有说明、
// 中间切到测试会话右边跟着切、阶段变化时跟不跟着跳、已取消不分标签
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const base = path.join(__dirname, '..', 'app', 'src');
const M = require(path.join(base, 'renderer', 'scripts', 'stage-tabs-model'));
const plain = (x) => JSON.parse(JSON.stringify(x));

function renderer() {
  class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.hidden = false; this.style = {}; this.dataset = {}; this.textContent = ''; this.events = {}; this.className = ''; this.classList = { toggle() {}, add() {}, remove() {} }; }
    appendChild(n) { this.children.push(n); return n; }
    insertAdjacentHTML() {}
    querySelectorAll() { return []; }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(event, fn) { this.events[event] = fn; }
    setAttribute(name, value) { this[name] = value; }
    remove() {}
    get text() { return this.textContent + this.children.map((c) => c.text).join(''); }
  }
  const nodes = {};
  const get = (id) => nodes[id] || (nodes[id] = new Node());
  const context = vm.createContext({
    document: { getElementById: get, createElement: (tag) => new Node(tag), createTextNode: (text) => Object.assign(new Node(), { textContent: text }), querySelector: () => get('main'), querySelectorAll: () => [] },
    window: { shell: { viewing() {}, traceGet: async () => null, sessionUsage: async () => ({ sessions: [], groups: [] }), openTesting: async () => ({}), predictConflicts: async () => null } },
    preview() {}, intakeAssessment() {}, ladder() {}, impact() {}, levelRow: () => new Node(), setTimeout, clearTimeout, levelName: () => '方向', terminal() {}, show() {}, refit() {}, requestAnimationFrame() {},
  });
  for (const file of ['html-text', 'core', 'inbox-model', 'inbox-panel', 'deps-model', 'conflicts-model', 'board-page-model', 'reqs',
    'board', 'stage-menu', 'designs', 'stage-tabs-model', 'panel', 'compose', 'trace-model', 'trace', 'session-history-model', 'session-history']) vm.runInContext(fs.readFileSync(path.join(base, 'renderer', 'scripts', file + '.js'), 'utf8'), context);
  get('board-date-filter').value = 'all';
  return { context, get };
}
const testing = (over = {}) => ({ flow: true, checks: [], classified: false, all_ui: false, rounds: [], last: null, round: 1, sessions: [], kicked: 0, told: 0, needs_kick: false, to_tell: 0, skipped: false, ever: false, doing: '', line: '', ...over });
const req = (over = {}) => ({ id: 'R-1', title: '导出加合计行', file: '.relayforge/requirements/R-1.md', state: 'open', stage: 'build', worktree: '/wt', branch: 'req/1', sessions: ['dev'], dev_sessions: ['dev'], test_sessions: [], designs: [],
  sections: { '需求说明': ['加合计'], '要改的功能点': ['RF-102'], '验收条件': ['导出的 CSV 最后一行是合计', '合计行显示为「合计」', '表头不变'] }, moves: {}, testing: testing(), ...over });

test('默认停在当前阶段；已取消的不分标签', () => {
  for (const s of M.ORDER) assert.equal(M.defaultTab(s), s);
  assert.equal(M.defaultTab('dropped'), null);
  assert.equal(M.hasTabs({ loose: true }, 'build'), false);
  assert.equal(M.hasTabs({}, 'dropped'), false);
});

test('每个标签放哪些块：需求说明、功能点、验收条件只在立项；没到的阶段只有一句说明', () => {
  assert.ok(M.blocks('build', 'talk').includes('req-sections'));
  for (const t of ['build', 'test', 'check', 'done']) assert.ok(!M.blocks('build', t).includes('req-sections'), t);
  assert.ok(M.blocks('build', 'build').includes('progress-block'));
  assert.ok(!M.blocks('build', 'build').includes('req-criteria'));
  assert.ok(M.blocks('check', 'check').includes('req-criteria'));
  assert.ok(M.blocks('done', 'done').includes('req-record'));
  assert.deepEqual(plain(M.blocks('talk', 'build')), ['later']);
  assert.deepEqual(plain(M.blocks('build', 'done')), ['later']);
  assert.match(M.LATER.build[1], /开发会话.*验收条件.*写代码/);
  assert.match(M.LATER.done[1], /合并.*完成记录/);
  assert.equal(M.criteriaMode('talk'), 'cases');
  assert.equal(M.criteriaMode('check'), 'accept');
});

test('按钮只在自己的标签里', () => {
  const all = (stage) => Object.fromEntries(M.ORDER.map((t) => [t, plain(M.buttons(stage, t))]));
  assert.deepEqual(all('talk'), { talk: ['start'], build: [], test: [], check: [], done: [] });
  assert.deepEqual(all('build'), { talk: [], build: ['to-test', 'skip-test'], test: [], check: [], done: [] });
  assert.deepEqual(all('test'), { talk: [], build: [], test: ['skip-test'], check: [], done: [] });
  assert.deepEqual(all('check'), { talk: [], build: [], test: [], check: ['run-it', 'done', 'accept-mark'], done: [] });
  assert.deepEqual(all('done'), { talk: [], build: [], test: [], check: [], done: [] });
});

test('阶段变化：看着当前阶段就跟着跳，看着别的标签不跳；测试退回开发跟着回去', () => {
  assert.equal(M.follow('build', 'test', 'build'), 'test');
  assert.equal(M.follow('build', 'test', 'talk'), 'talk');
  assert.equal(M.follow('test', 'build', 'test'), 'build');
  let st = M.sync(null, 'build'); assert.equal(st.viewing, 'build');
  st = M.sync(st, 'test'); assert.equal(st.viewing, 'test');
  st = M.sync({ viewing: 'talk', stage: 'build' }, 'test'); assert.equal(st.viewing, 'talk'); assert.equal(st.stage, 'test');
});

test('中间切到测试会话右边切到测试；切回需求会话右边不动', () => {
  assert.equal(M.middleSwitched({ viewing: 'talk', stage: 'test' }, 'test', 'test').viewing, 'test');
  assert.equal(M.middleSwitched({ viewing: 'test', stage: 'test' }, 'chat', 'test').viewing, 'test');
  assert.equal(M.middleSwitched({ viewing: 'check', stage: 'test' }, 'chat', 'test').viewing, 'check');
});

test('面板：打开进行中的需求停在开发，点立项标签才看到验收条件和用例', () => {
  const { context: c, get } = renderer();
  const r = req(); c.reqs = [r]; c.current = r.id; c.panel();
  assert.equal(get('panel').dataset.view, 'build');
  assert.equal(get('req-usage').hidden, false);
  const tabs = get('stage-steps').children.filter((n) => n.tag === 'button');
  assert.deepEqual(plain(tabs.map((n) => n['aria-selected'])), ['false', 'true', 'false', 'false', 'false']);
  assert.equal(get('to-check').hidden, false);
  const tagged = (v) => get('req-body').children.filter((n) => n.dataset.tab === v).map((n) => n.textContent).filter(Boolean);
  assert.deepEqual(plain(tagged('talk')), ['需求说明', '要改的功能点', '验收条件']);
  assert.deepEqual(plain(tagged('check')), ['验收结果']);
  tabs[0].onclick(); // 点立项
  assert.equal(get('panel').dataset.view, 'talk');
  assert.equal(get('later').hidden, true);
});

test('面板：立项中的需求点开发，只有一句说明', () => {
  const { context: c, get } = renderer();
  const r = req({ state: 'draft', stage: 'talk', worktree: '' }); c.reqs = [r]; c.current = r.id; c.panel();
  assert.equal(get('panel').dataset.view, 'talk');
  c.viewTab(r.id, 'build');
  assert.equal(get('panel').dataset.view, 'later');
  assert.equal(get('later').hidden, false);
  assert.match(get('later').text, /确认立项后开始开发.*开发会话/);
});

test('面板：待验收时验收标签有逐条验收且不带用例；完成后的验收标签没有按钮', () => {
  const { context: c, get } = renderer();
  const r = req({ stage: 'check', testing: testing({ checks: [{ n: 1, text: '表头不变', kind: 'data', why: '', steps: [] }] }) });
  c.reqs = [r]; c.current = r.id; c.panel();
  assert.equal(get('panel').dataset.view, 'check');
  assert.equal(get('accept').hidden, false);
  const done = { ...r, state: 'done', stage: 'done', worktree: '' };
  c.reqs = [done]; c.panel();
  assert.equal(get('req-usage').hidden, false);
  c.viewTab(done.id, 'check');
  assert.equal(get('panel').dataset.view, 'check');
  assert.equal(get('accept').hidden, true); // 完成了：没有「跑起来」「确认完成」
  assert.equal(get('done').hidden, true);
});

test('面板：看着当前阶段时阶段变化跟着跳，看着立项时不跳；退回开发跟着回去', () => {
  const { context: c, get } = renderer();
  let r = req({ stage: 'build' }); c.reqs = [r]; c.current = r.id; c.panel();
  c.reqs = [{ ...r, stage: 'test', test_sessions: ['t'] }]; c.panel();
  assert.equal(get('panel').dataset.view, 'test');
  c.reqs = [{ ...r, stage: 'build', test_sessions: ['t'] }]; c.panel();
  assert.equal(get('panel').dataset.view, 'build');
  c.viewTab(r.id, 'talk');
  c.reqs = [{ ...r, stage: 'test', test_sessions: ['t'] }]; c.panel();
  assert.equal(get('panel').dataset.view, 'talk');
  const cur = get('stage-steps').children.filter((n) => n.tag === 'button').map((n) => n.children[0]);
  assert.equal(cur[2].className, 'cur'); // 蓝底挪到测试
});

test('面板：已取消的需求不分标签，内容照旧从上到下', () => {
  const { context: c, get } = renderer();
  const r = req({ state: 'dropped', stage: 'dropped', sections: { '需求说明': ['x'], '验收条件': ['y'], '变更记录': ['取消了'] } });
  c.reqs = [r]; c.current = r.id; c.panel();
  assert.equal(get('panel').dataset.view, 'all');
  assert.equal(get('stage-steps').hidden, true);
  assert.deepEqual(plain(get('req-body').children.filter((n) => n.tag === 'h2').map((n) => n.textContent)), ['需求说明', '验收条件', '取消记录']);
});

test('页面里静态的块标的标签和 model 一致', () => {
  const html = fs.readFileSync(path.join(base, 'renderer', 'index.html'), 'utf8');
  for (const [id, tabs] of Object.entries(M.BLOCKS)) {
    if (['req-sections', 'req-criteria', 'req-record', 'req-tests', 'req-case-changes'].includes(id)) continue; // 这几块是脚本生成的
    const m = html.match(new RegExp('id="' + id + '"[^>]*data-tab="([^"]*)"'));
    assert.ok(m, id + ' 没标 data-tab');
    assert.deepEqual(m[1].split(' ').sort(), [...tabs].sort(), id);
  }
  for (const id of ['title', 'req-state', 'branch', 'session-choices', 'stage-steps', 'req-usage']) assert.ok(!new RegExp('id="' + id + '"[^>]*data-tab').test(html), id + ' 应固定在上方');
  assert.match(html, /id="panel-head"[\s\S]*id="title"[\s\S]*id="stage-steps"[\s\S]*<\/div>\s*<div id="later"/);
});

// R-20261123-221a：开发中计划没做完时，交给测试 / 跳过测试前先确认
test('planGap：没做完、没计划要确认，做完不用；已放弃的步骤不算', () => {
  const step = (status) => ({ title: 's', status });
  assert.deepEqual(plain(M.planGap([step('done'), step('done'), step('doing')])), { none: false, left: 1, total: 3 });
  assert.deepEqual(plain(M.planGap([step('todo'), step('todo'), step('todo')])), { none: false, left: 3, total: 3 });
  assert.equal(M.planGap([step('done'), step('done'), step('done')]), null);
  assert.equal(M.planGap([step('done'), step('dropped')]), null);
  assert.equal(M.planGap([]).none, true);
  assert.equal(M.planGap(undefined).none, true);
  assert.match(M.gapLine(M.planGap([step('done'), step('todo')]), false), /还差 1 步|still has 1/);
  assert.match(M.gapLine(M.planGap([]), true), /还没有记录计划|No plan/);
  assert.match(M.gapLine(M.planGap([step('todo')]), true), /不会跑测试|no tests will run/);
});

test('开发中点交给测试 / 跳过测试：计划没做完先弹确认，取消不动，确认才推进；做完直接推进', async () => {
  for (const [to, plan, expectAsk] of [['test', [{ status: 'done' }, { status: 'done' }, { status: 'doing' }], true], ['skip', [], true],
    ['test', [{ status: 'done' }], false], ['skip', [{ status: 'done' }], false]]) {
    const { context, get } = renderer();
    const calls = [];
    context.window.shell.setReq = async (id, t) => { calls.push(t); return { ...req(), stage: t === 'test' ? 'test' : 'check' }; };
    let shown = 0;
    context.HTMLDialogElement = undefined;
    get('stage-dlg').showModal = () => { shown++; };
    get('stage-dlg').close = () => {}; get('stage-dlg-no').focus = () => {};
    vm.runInContext(`states['R-1'] = { state: { plan: ${JSON.stringify(plan)} } }; changeStage(${JSON.stringify(req())}, '${to}');`, context);
    await new Promise((r) => setTimeout(r, 5));
    if (expectAsk) {
      assert.equal(shown, 1); assert.deepEqual(calls, []);
      get('stage-dlg-yes').onclick();
      await new Promise((r) => setTimeout(r, 5));
      assert.deepEqual(calls, [to]); assert.equal(shown, 1);
    } else {
      assert.deepEqual(calls.length ? calls : [to], [to]);
    }
  }
});
