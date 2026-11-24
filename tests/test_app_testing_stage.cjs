// 测试阶段（应用这一侧）：看板多一栏、面板的阶段进度条和归类标记、测试会话的启动和开场白、进入测试中时自动开出它、
// 交回的结论交给开发会话或提醒你、测试会话和开发会话的权限规则
const test = require('node:test');
const assert = require('node:assert/strict');
const locale = require('../app/src/main/locale');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const base = path.join(__dirname, '..', 'app', 'src');
const main = path.join(base, 'main');
const plain = (x) => JSON.parse(JSON.stringify(x)); // vm 里的数组和这里的不是同一个 Array

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
    window: { shell: { viewing() {}, traceGet: async () => null, sessionUsage: async () => ({ sessions: [], groups: [] }), openTesting: async () => ({}) } },
    preview() {}, levelRow: () => new Node(), setTimeout, clearTimeout, levelName: () => '方向', terminal() {}, show() {}, refit() {}, requestAnimationFrame() {},
  });
  for (const file of ['html-text', 'core', 'inbox-model', 'inbox-panel', 'deps-model', 'conflicts-model', 'board-page-model', 'reqs',
    'board', 'stage-menu', 'designs', 'stage-tabs-model', 'panel', 'compose', 'trace-model', 'trace', 'session-history-model', 'session-history']) vm.runInContext(fs.readFileSync(path.join(base, 'renderer', 'scripts', file + '.js'), 'utf8'), context);
  get('board-date-filter').value = 'all';
  return { context, get };
}

const testing = (over = {}) => ({ flow: true, checks: [], classified: false, all_ui: false, rounds: [], last: null, round: 1, sessions: [], kicked: 0, told: 0, needs_kick: false, to_tell: 0, skipped: false, ever: false, doing: '', line: '', ...over });
const req = (over = {}) => ({ id: 'R-1', title: '导出加合计行', file: '.relayforge/requirements/R-1.md', state: 'open', stage: 'build', worktree: '/wt', branch: 'req/1', sessions: ['dev'], dev_sessions: ['dev'], test_sessions: [], designs: [],
  sections: { '需求说明': ['加合计'], '要改的功能点': ['RF-102'], '验收条件': ['导出的 CSV 最后一行是合计', '合计行在 Excel 里显示为「合计」'] }, moves: {}, testing: testing(), ...over });

test('the board has a 测试中 column between 进行中 and 待验收, and the filter knows it', () => {
  const { context: c, get } = renderer();
  assert.deepEqual(plain(c.STAGES.map((s) => s[0])), ['talk', 'build', 'test', 'check', 'done', 'dropped']);
  assert.equal(c.reqFilter.test, true);
  c.reqs = [req({ id: 'R-1', stage: 'test' }), req({ id: 'R-2', title: '老需求', stage: 'check' })];
  c.board();
  const cols = get('board-columns').children;
  assert.equal(cols.length, 6);
  assert.equal(cols[2].children[0].children[1].textContent, '测试中');
  assert.equal(cols[2].children[1].children[0].textContent, '导出加合计行');
  assert.equal(cols[3].children[1].children[0].textContent, '老需求');
  assert.equal(c.stageOf({ state: 'open', stage: 'test', sections: {} }), 'test');
});

test('the panel shows the stage bar, marks a skipped test step, and badges each acceptance condition', () => {
  const { context: c, get } = renderer();
  const steps = [{ open: '账单列表 › 导出', do: '用 Excel 打开', see: '最后一行是「合计」' }];
  const r = req({ stage: 'test', test_sessions: ['t'], testing: testing({ sessions: ['t'], checks: [{ n: 1, text: '导出的 CSV 最后一行是合计', kind: 'data', why: '程序能比对', steps: [] }, { n: 2, text: '合计行在 Excel 里显示为「合计」', kind: 'ui', why: '要看', steps }] }) });
  c.reqs = [r]; c.current = r.id; c.panel();
  const bar = get('stage-steps').children.filter((n) => n.tag === 'button').map((n) => n.children[0]);
  assert.deepEqual(plain(bar.map((n) => n.textContent)), ['✓ 立项', '✓ 开发', '测试', '验收', '完成']);
  assert.equal(bar[2].className, 'cur');
  c.viewTab(r.id, 'talk'); // 验收条件和它们的归类标记在「立项」标签里
  assert.equal(get('req-state').textContent, '测试中');
  assert.equal(get('testing-box').hidden, false); // 测试中：测试会话在做什么、跳过测试
  assert.match(get('testing-note').textContent, /测试中（第 1 轮）/);
  assert.equal(get('to-check').hidden, true);
  const checks = get('req-body').children[5].children;
  assert.equal(checks[0].children[1].textContent, '数据可验');
  assert.equal(checks[1].children[1].textContent, '看界面');
  assert.equal(checks[1].children[2].tag, 'details'); // 看界面的展开能看到检查步骤
  assert.match(checks[1].children[2].text, /检查步骤（1 步）.*账单列表 › 导出：用 Excel 打开.*应该看到：最后一行是「合计」/);
  c.reqs = [{ ...r, stage: 'check', testing: testing({ rounds: [{ n: 1, verdict: 'skipped', problems: [] }], last: { n: 1, verdict: 'skipped', summary: '用户点了「跳过测试，直接验收」', problems: [] }, skipped: true, line: '测试：跳过了测试（第 1 轮跳过测试）。' }) }];
  c.panel();
  const after = get('stage-steps').children.filter((n) => n.tag === 'button').map((n) => n.children[0]);
  assert.equal(after[2].className, 'skip');
  assert.equal(after[3].className, 'cur');
  assert.equal(get('test-result').hidden, false);
  assert.match(get('test-result').text, /跳过了测试：用户点了「跳过测试，直接验收」/);
});

test('building: two actions instead of one; problems handed back are listed', async () => {
  const { context: c, get } = renderer();
  const problems = [{ k: 1, where: '测试集 TS-002 #1', what: '没有 total_row', judge: '没实现' }];
  const r = req({ stage: 'build', moves: { test: null, check: null }, testing: testing({ rounds: [{ n: 1, verdict: 'problems', problems }], last: { n: 1, at: '2026-11-19 10:20', verdict: 'problems', problems, fixed: null } }) });
  c.reqs = [r]; c.current = r.id;
  c.terms[r.id] = { term: { focus() {} } };
  c.panel();
  assert.equal(get('to-check').hidden, false);
  assert.match(get('to-check-note').textContent, /自动进入测试中/);
  assert.equal(get('test-problems').hidden, false);
  assert.match(get('test-problems').text, /测试交回的问题（第 1 轮.*测试集 TS-002 #1.*现象：没有 total_row.*测试会话的判断：没实现/);
  const calls = [];
  c.window.shell.setReq = async (id, to) => { calls.push([id, to]); return { ...r, stage: to === 'skip' ? 'check' : to }; };
  Object.assign(get('stage-dlg'), { showModal() { this.open = true; }, close() { this.open = false; } });
  get('stage-dlg-no').focus = () => {};
  c.states[r.id] = { state: { plan: [{ title: 'a', status: 'done' }] } }; // 计划全做完才不会多一道确认（R-20261123-221a 之前没有这道）
  c.changeStage(r, 'test'); // 交给测试：直接改
  await new Promise((done) => setImmediate(done));
  c.changeStage(r, 'skip'); // 跳过测试：先问
  assert.equal(get('stage-dlg').open, true);
  assert.match(get('stage-dlg-body').children.map((n) => n.textContent).join(''), /如实写「跳过了测试」/);
  get('stage-dlg-yes').onclick();
  await new Promise((done) => setImmediate(done));
  assert.deepEqual(calls, [['R-1', 'test'], ['R-1', 'skip']]);
  clearTimeout(c.say.timer);
});

test('a requirement that went through testing gets a 测试 tab; it routes the terminal to test:R', () => {
  const { context: c } = renderer();
  const r = req({ stage: 'test', test_sessions: ['t'] });
  c.reqs = [r]; c.current = r.id;
  const tabs = c.designTabs(r);
  assert.deepEqual(plain(tabs.map((li) => li.children[1].textContent)), ['对话', '测试（测试中）']);
  assert.equal(c.termOf(r.id), r.id);
  c.shown[r.id] = 'test';
  assert.equal(c.termOf(r.id), 'test:R-1');
  assert.deepEqual(c.designTabs(req({ test_sessions: [] })).length, 0); // 从没进过测试阶段、也没有设计稿：照旧只有聊天
});

function mainTesting() {
  const calls = { rf: [], sent: [], told: [], reminded: [], typed: [] };
  const state = { project: '/p', runs: new Map() };
  const mocks = {
    './backend': { uiText: locale.uiText,  rf: async (args) => { calls.rf.push(args); return { ok: true, stdout: '{}' }; } },
    './state': { state, send: (channel, payload) => calls.sent.push([channel, payload]) },
    './ui-names': require(path.join(main, 'ui-names')), './prompts': require(path.join(main, 'prompts')),
    './demo': { info: () => null },
    './sessions': { type: (run, text) => calls.typed.push([run.id, text]) },
    './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post: (m) => calls.typed.push([m.to, m.text]) }, // 再测一轮经收件箱送（inbox.js）
    './testsets': { tell: (id, text) => calls.told.push([id, text]) },
    './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, remind: (id, title, text) => calls.reminded.push([id, title, text]) },
  };
  const context = vm.createContext({ require: (id) => id === './locale' ? locale : mocks[id] || require(id), module: { exports: {} } });
  vm.runInContext(fs.readFileSync(path.join(main, 'testing.js'), 'utf8'), context);
  return { T: context.module.exports, calls, state };
}

test('entering 测试中 while the app runs opens the test session once; at startup it waits until you open the requirement', async () => {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-tst-'));
  const { T, calls } = mainTesting();
  const before = req({ stage: 'build' });
  const kicked = req({ stage: 'test', testing: testing({ needs_kick: true }) });
  await T.follow([kicked]); // 刚打开应用：不一下子开会话
  assert.deepEqual(calls.rf, []);
  const fresh = mainTesting();
  await fresh.T.follow([before]);
  await fresh.T.follow([kicked]);
  assert.deepEqual(plain(fresh.calls.rf), [['req', 'test-kicked', 'R-1', '1']]);
  assert.deepEqual(plain(fresh.calls.sent[0]), ['tests:say', { id: 'test:R-1', test: true }]); // 界面在后台打开 test:R-1，开场白由 sessions.js 给
  await fresh.T.follow([kicked]);
  assert.equal(fresh.calls.rf.length, 1); // 同一轮不重复
  await T.open('R-1').catch(() => {}); // 你点开需求时补上（这里 listReqs 没有接上，不报错就行）
});

test('the next round is typed into the open test session; handed-back problems go to the development session', async () => {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-tst-'));
  const { T, calls, state } = mainTesting();
  const problems = [{ k: 1, where: '测试集 TS-002 #1', what: '没有 total_row', judge: '没实现' }];
  const rounds = [{ n: 1, verdict: 'problems', problems, fixed: { note: '加了 total_row' } }];
  state.runs.set('test:R-1', { id: 'test:R-1' });
  await T.kick(req({ stage: 'test', test_sessions: ['t'], testing: testing({ needs_kick: true, round: 2, sessions: ['t'], rounds, last: rounds[0] }) }));
  assert.equal(calls.typed[0][0], 'test:R-1');
  assert.match(calls.typed[0][1], /【第 2 轮测试/);
  assert.match(calls.typed[0][1], /开发会话说修好了：加了 total_row/);
  assert.match(calls.typed[0][1], /1\. 测试集 TS-002 #1：没有 total_row（没实现）/);
  const back = req({ stage: 'build', testing: testing({ to_tell: 1, rounds: [{ ...rounds[0], fixed: null }], last: { ...rounds[0], fixed: null } }) });
  await T.tell(back);
  assert.deepEqual(plain(calls.rf.at(-1)), ['req', 'test-told', 'R-1', '1']);
  assert.equal(calls.told[0][0], 'R-1');
  assert.match(calls.told[0][1], /【测试会话交回第 1 轮：有问题，需求退回进行中】[\s\S]*现象：没有 total_row[\s\S]*req retest "修了什么"/);
  const ok = req({ stage: 'check', testing: testing({ to_tell: 2, last: { n: 2, verdict: 'ok', summary: '都过了', problems: [] } }) });
  await T.tell(ok);
  assert.deepEqual(plain(calls.reminded.at(-1)), ['R-1', '导出加合计行 · 可以验收了', '都过了']); // 用现有的提醒（系统通知 + 等你）
});

test('the test session is opened in the requirement working directory with its own prompt and rules', async () => {
  const sent = [], rfCalls = [], spawned = [];
  const state = { runs: new Map(), project: '/project' };
  const r = req({ stage: 'test', worktree: fs.mkdtempSync(path.join(os.tmpdir(), 'rf-wt-')) });
  const mocks = {
    child_process: { spawn(cmd, args, opts) { spawned.push({ args, cwd: opts.cwd, env: opts.env }); const ee = () => ({ on() {}, write() {} }); return { stdin: ee(), stdout: ee(), stderr: ee(), stdio: [null, null, null, ee()], on() {} }; } },
    './demo': { session: () => null, info: () => null }, './initialization': { forSession: () => null },
    './settings': { get: () => ({ agent: 'claude' }), command: () => ['claude'] }, './codex-sessions': { has: () => false },
    './config': { EVENTS: '/tmp/ev', PTY_BRIDGE: 'bridge.py', PYTHON: 'python3' },
    './backend': { uiText: locale.uiText,  rf: async (args) => { rfCalls.push(args); return { ok: true, stdout: 'S-20261119-abcd\n' }; }, cleanEnv: () => ({}), lastLine: (x) => x },
    './state': { state, send: (channel, payload) => sent.push([channel, payload]) },
    './requirements': { listReqs: async () => [r], pushReqs() {} },
    './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, seen() {}, stopped() {} },
    './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post() {}, takeForOpen: () => '', envFor: () => ({}) }, './codex-hooks': { flags: () => [], env: () => ({}) }, './claude-settings': { guardSpec: () => ({}), draftsFor: () => '/d', hooks: (...args) => JSON.stringify({ args }) },
    './ui-names': require(path.join(main, 'ui-names')), './prompts': require(path.join(main, 'prompts')),
    './session-token': require(path.join(main, 'session-token')),
    './splits': { guide: '拆分的说明' },
  };
  const context = vm.createContext({ require: (id) => id === './locale' ? locale : mocks[id] || require(id), module: { exports: {} }, clearTimeout, setTimeout, process });
  vm.runInContext(fs.readFileSync(path.join(main, 'sessions.js'), 'utf8'), context);
  await context.module.exports.open({ id: 'test:R-1', cols: 80, rows: 24 });
  const attach = rfCalls.find((a) => a[1] === 'attach');
  assert.equal(attach[2], 'R-1');
  assert.equal(attach[4], '--test'); // 登记成测试会话（rf.py 还要应用的标记）
  assert.equal(spawned.length, 1);
  assert.equal(spawned[0].cwd, r.worktree);
  assert.equal(spawned[0].env.RELAYFORGE_SESSION_ID, attach[3]); // 程序给的会话编号：只用来记出处
  const sig = require(path.join(main, 'session-token')).shared.check(spawned[0].env.RELAYFORGE_SESSION_SIG); // 签名的会话令牌：rf.py 靠它认出测试会话
  assert.equal(sig.kind, 'test'); assert.equal(sig.owner, 'R-1'); assert.equal(sig.sid, attach[3]);
  const args = spawned[0].args;
  const hookArgs = JSON.parse(args[args.indexOf('--settings') + 1]).args;
  assert.deepEqual(plain(hookArgs[7]), { role: 'test', place: r.worktree, split: false, req: true, headless: false }); // 测试会话只测这个需求，不提拆分；是需求的会话，放行测试用例的命令
  const guide = args[args.indexOf('--append-system-prompt') + 1]; // 只有程序消息的说明（它也会收到「再测一轮」），没有问题和拆分的说明
  assert.match(guide, /〔RelayForge 程序 · 来源 · 编号〕/);
  assert.doesNotMatch(guide, /拆分的说明|--source Agent/);
  assert.equal(hookArgs[3], false); // 不是开发会话
  assert.ok(args.includes('--disallowedTools'));
  assert.match(args.at(-1), /这个会话是需求「导出加合计行」（R-1）的测试会话/);
  assert.ok(state.runs.has('test:R-1'));
});

test('test session prompt: identity, classification, test sets only when data can verify, check steps, old sets, environment, no product code, verdict', () => {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-tsp-'));
  const P = require(path.join(main, 'prompts'));
  const text = P.testing({ id: 'R-1', title: '导出', file: '.relayforge/requirements/R-1.md' }, 'S-1', false);
  for (const re of [/你没参与开发，只拿着需求说明和验收条件/, /当前目录是这个需求的工作目录.*代码可以读、可以跑/, /req classify 序号\[,序号…\] 数据\|界面 --why/,
    /不为了覆盖验收条件去凑测试集：只有一条数据的、只断言「返回了东西」或「函数存在」的，不该建；一个需求可以一个测试集都没有/,
    /req steps 序号 "打开哪里" "做什么" "应该看到什么"/, /不写测试集/, /test run --req R-1/, /是改坏了，就作为问题交回/,
    /项目目录里的安装和构建预先放行/, /env propose 测试环境编号 --file -/, /不改产品代码。你只能写测试数据（只通过 rf\.py test）、测试集的接入代码和桥接文件/,
    /req verdict ok --summary/, /req verdict problems --problem "哪条验收条件，或哪个测试集的哪条数据｜现象｜你的判断"/, /全部验收条件都要看界面时，你只写检查步骤，然后交回「可以验收了」/]) {
    assert.match(text, re);
  }
  assert.doesNotMatch(text, /是改坏了，就修代码/);
  assert.match(P.testing({ id: 'R-1', title: '导出', file: 'a.md' }, 'S-1', true), /接着之前的测试往下做/);
});

test('permissions: the test session may classify, write test data and propose environments, not hand itself on or edit facts; development only retests', () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-perm-'));
  require(path.join(main, 'state')).state.project = project;
  const { hooks } = require(path.join(main, 'claude-settings'));
  const { BUNDLED } = require(path.join(main, 'config'));
  const tester = JSON.parse(hooks(null, false, false, false, false, false, false, { role: 'test', place: '/wt/R-1' })).permissions;
  const dev = JSON.parse(hooks(null, false, false, true, false, false, false, { role: 'develop' })).permissions;
  const fix = JSON.parse(hooks(null, false, false, true, false, false, false, { role: 'fix' })).permissions;
  const chat = JSON.parse(hooks(null, false)).permissions;
  const rule = (rf, what) => `Bash(python3 ${rf} ${what}:*)`;
  for (const rf of [BUNDLED, '.relayforge/bin/rf.py']) {
    for (const a of ['steps', 'verdict']) {
      assert.ok(tester.allow.includes(rule(rf, 'req ' + a)), 'test ' + a);
      assert.ok(dev.deny.includes(rule(rf, 'req ' + a)), 'dev denied ' + a);
    }
    // 归类挪到了立项：测试会话、开发会话都能运行 classify，改已经归好的只能提给用户（rf.py 按会话令牌定）
    assert.ok(tester.allow.includes(rule(rf, 'req classify')) && dev.allow.includes(rule(rf, 'req classify')), 'classify proposes');
    assert.ok(!dev.deny.includes(rule(rf, 'req classify')));
    assert.ok(dev.allow.includes(rule(rf, 'req retest')));
    assert.ok(tester.deny.includes(rule(rf, 'req retest')));
    for (const p of [tester, dev, fix, chat]) for (const a of ['to-test', 'skip-test', 'test-kicked', 'test-told']) assert.ok(p.deny.includes(rule(rf, 'req ' + a)), a);
    for (const a of ['new', 'add', 'edit', 'rm', 'run', 'analyze', 'bridge']) assert.ok(tester.allow.includes(rule(rf, 'test ' + a)), 'tester test ' + a);
    assert.ok(!dev.allow.includes(rule(rf, 'test new')), 'development does not create test sets');
    for (const a of ['add', 'edit', 'rm', 'run', 'analyze']) assert.ok(dev.allow.includes(rule(rf, 'test ' + a)), 'dev test ' + a);
    assert.ok(fix.allow.includes(rule(rf, 'test new')), 'fix sessions still write their own test sets');
    for (const a of ['list', 'show', 'propose', 'prep', 'prep-run']) assert.ok(tester.allow.includes(rule(rf, 'env ' + a)), 'tester env ' + a);
  }
  assert.ok(tester.allow.includes('Bash(npm ci:*)') || tester.allow.some((x) => x.startsWith('Bash(npm ci')), 'project installs');
  assert.ok(tester.deny.includes('Edit(//wt/R-1/src/main/**)'));
  // 事实目录不写规则：Edit 的禁止会用到沙箱上（rf.py test run 就写不了运行结果），Write(路径) 规则 Claude Code 对不上；
  // Edit 工具改事实目录由执行前的检查拦（test_app_hardening.cjs 核对了这道检查）
  assert.ok(!tester.deny.some((x) => x.startsWith('Write(')));
  for (const p of ['//wt/R-1/.relayforge/**', `/${fs.realpathSync(project)}/.relayforge/**`]) {
    assert.ok(!tester.deny.includes(`Edit(${p})`), p);
  }
  assert.ok(tester.deny.includes('NotebookEdit'));
  assert.ok(!dev.deny.some((x) => x.startsWith('Edit(')), 'development edits code freely');
});

test('a requirement left to manual acceptance says so instead of an empty test-set block', () => {
  const TM = require(path.join(base, 'renderer', 'scripts', 'tests-model.js'));
  const none = { sets: [], totals: {} };
  assert.equal(TM.manualOnly(none, req({ testing: testing({ all_ui: true }) })), true);
  assert.equal(TM.manualOnly(none, req({ testing: testing({ rounds: [{ n: 1, verdict: 'ok' }] }) })), true);
  assert.equal(TM.manualOnly(none, req({ testing: testing() })), false); // 还没测：不下结论
  assert.equal(TM.manualOnly({ sets: [{ id: 'TS-1', cases: 3 }] }, req({ testing: testing({ all_ui: true }) })), false);
  assert.equal(TM.panel(none, req()).visible, false);
});
