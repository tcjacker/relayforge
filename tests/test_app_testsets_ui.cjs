// 测试集的界面（应用这一侧）：model 层（比例、分组、筛选、哪些算「需要看的」、需求面板的两块、查看器的用例列表、确认完成前的判断、
// 「让 AI 改」交给谁）和主进程（调哪些 rf.py test 命令、让 AI 改时发给谁、前缀文字、放行规则、确认完成前先跑一遍）
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
process.env.RELAYFORGE_HOME = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-tsui-home-')); // config.js 读它：收件箱（inbox.js）等本机记录写在临时目录里
const root = path.join(__dirname, '..', 'app', 'src');
const main = path.join(root, 'main');
const M = require(path.join(root, 'renderer', 'scripts', 'tests-model.js'));

const row = (extra) => Object.assign({ id: 'TS-001', title: '按月筛选', feature: 'RF-101', feature_title: '账单列表', requirement: 'R-1', fix: null, stale: false, stale_reason: null,
  in_progress: null, cases: 10, unseen: 0, pending_changes: [], approved_changes: [], hints: [], last: { counts: { pass: 10, fail: 0, broken: 0, total: 10 } }, last_main: null }, extra);

test('counts and the bar: failed, broken, not run yet (cases added since), passed — in that order, sized by count', () => {
  assert.deepEqual(M.counts(row({ cases: 12, last: { counts: { pass: 7, fail: 2, broken: 1, total: 10 } } })), { pass: 7, fail: 2, broken: 1, unrun: 2, total: 12 });
  assert.deepEqual(M.counts(row({ cases: 4, last: null })), { pass: 0, fail: 0, broken: 0, unrun: 4, total: 4 });
  assert.deepEqual(M.segments({ pass: 7, fail: 2, broken: 1, unrun: 2 }), [['f', 2], ['e', 1], ['n', 2], ['p', 7]]);
  assert.deepEqual(M.segments({ pass: 3, fail: 0, broken: 0, unrun: 0 }), [['p', 3]]);
  assert.match(M.barHtml({ pass: 3, fail: 1, broken: 0, unrun: 0 }), /class="f" style="flex:1".*class="p" style="flex:3"/);
  assert.equal(M.resultText({ pass: 3, fail: 1, broken: 0, unrun: 2 }), '1 条失败，2 条还没跑，3 条通过');
  assert.match(M.pillsHtml({ pass: 0, fail: 0, broken: 0, unrun: 0, total: 0 }), /还没有数据/);
});

test('test page: "needs a look" is failures, broken, hints or changes waiting; filters, counts and grouping by feature', () => {
  const rows = [
    row({ id: 'TS-001' }),
    row({ id: 'TS-002', feature: 'RF-102', feature_title: '导出', last: { counts: { pass: 0, fail: 0, broken: 8, total: 8 } }, cases: 8 }),
    row({ id: 'TS-003', hints: [{ key: 'never_failed', text: '从没失败过' }] }),
    row({ id: 'TS-004', in_progress: 'R-2', last: { counts: { pass: 4, fail: 1, broken: 0, total: 5 } }, cases: 5 }),
    row({ id: 'TS-005', stale: true, stale_reason: '下线了', last: { counts: { pass: 0, fail: 2, broken: 0, total: 2 } }, cases: 2 }),
    row({ id: 'TS-006', pending_changes: [{ id: 1 }] }),
  ];
  assert.deepEqual(rows.filter(M.needsLook).map((r) => r.id), ['TS-002', 'TS-003', 'TS-004', 'TS-006']); // 过期的有失败也不算
  assert.deepEqual(M.filterCounts(rows), { need: 4, all: 5, inprog: 1, stale: 1 });
  const g = M.groups(rows, 'all');
  assert.deepEqual(g.map((x) => [x.feature, x.rows.map((r) => r.id), x.cases]), [['RF-101', ['TS-003', 'TS-004', 'TS-006', 'TS-001'], 35], ['RF-102', ['TS-002'], 8]]);
  assert.deepEqual(M.groups(rows, 'stale').map((x) => x.rows.map((r) => r.id)), [['TS-005']]);
  const reqs = [{ id: 'R-1', title: '账单列表', state: 'done' }, { id: 'R-2', title: '按月筛选', state: 'open' }];
  assert.deepEqual(M.reqLabel(row({ requirement: 'R-1' }), reqs), { title: '账单列表', sub: 'R-1 · 已完成', going: false });
  assert.deepEqual(M.reqLabel(row({ requirement: 'R-2', in_progress: 'R-2' }), reqs), { title: '按月筛选', sub: 'R-2 · 进行中', going: true });
  assert.equal(M.reqLabel(row({ requirement: null }), reqs).title, '没有挂需求');
  assert.equal(M.lastRunAt([row({ last_main: { at: '2026-11-18 09:00:00' } }), row({ last_main: { at: '2026-11-18 11:02:00' } })]), '2026-11-18 11:02:00');
});

test('requirement panel: shown while in progress with sets or changes; failing and unseen first, the rest folded; done shows one line', () => {
  const r = { id: 'R-2', state: 'open' };
  const change = { id: 1, case: 1, name: '不带条件', op: 'edit', old: { expect: ['a'] }, new: { expect: ['b'] }, why: '需求改了', by: 'R-2', state: 'pending' };
  const list = { scope: 'R-2', sets: [
    row({ id: 'TS-001', origin: 'existing', pending_changes: [change, Object.assign({}, change, { id: 2, by: 'R-9' })], last: { counts: { pass: 29, fail: 1, broken: 0, total: 30 } }, cases: 30 }),
    row({ id: 'TS-004', origin: 'new', last: { counts: { pass: 3, fail: 2, broken: 0, total: 5 } }, cases: 5 }),
    row({ id: 'TS-005', origin: 'new', cases: 43, last: { counts: { pass: 43, fail: 0, broken: 0, total: 43 } } }),
    row({ id: 'TS-006', origin: 'new', cases: 3, unseen: 2, last: { counts: { pass: 3, fail: 0, broken: 0, total: 3 } } }),
  ] };
  const m = M.panel(list, r, [Object.assign({ set: 'TS-001' }, change, { id: 7 })]);
  assert.equal(m.visible, true);
  assert.deepEqual(m.hot.map((s) => s.id), ['TS-004', 'TS-001', 'TS-006']); // 失败多的在前，只有新加没看的排在失败后面
  assert.deepEqual(m.calm.map((s) => s.id), ['TS-005']);
  assert.equal(m.calmCases, 43);
  assert.deepEqual(m.pending.map((c) => [c.set, c.id]), [['TS-001', 1]]); // 别的需求提的不算在这里
  assert.equal(m.rejected.length, 1);
  assert.equal(m.head, '4 个 · 81 条数据 · 3 条没通过');
  assert.equal(M.panel({ sets: [] }, r).visible, false);
  assert.equal(M.panel({ sets: [row({ pending_changes: [change], cases: 0 })].map((s) => Object.assign(s, { skipped: '过期' })) }, r).visible, true);
  assert.equal(M.panel(list, { id: 'R-2', state: 'draft' }).visible, false);
  const done = M.panel({ sets: [row({ cases: 30, last: { counts: { pass: 30, fail: 0, broken: 0, total: 30 } } })] }, { id: 'R-1', state: 'done' });
  assert.deepEqual([done.visible, done.done, done.doneLine], [true, true, '测试集 1 个、30 条：30 条通过']);
  const sides = M.changeSides(change);
  assert.deepEqual([sides.old, sides.now], ['[\n  "a"\n]', '[\n  "b"\n]']);
  assert.equal(M.changeSides({ op: 'delete', old: { raises: '不合法' }, new: null }).old, '期望报错，含「不合法」');
  assert.match(M.rejectText('TS-001', change), /不同意测试集 TS-001 的改动 1（#1「不带条件」，改期望）：这是改坏了/);
});

test('viewer: filter (needs a look / failed / all), search, grouped normal / edge / error with failures first, and the first case shown', () => {
  const res = (status, extra) => Object.assign({ status, actual: null, error: null, diff: [], ms: 1 }, extra);
  const cases = [
    { n: 1, name: '只返回那个月的', cat: '正常', result: res('pass') },
    { n: 2, name: '那个月没有', cat: '正常', unseen: true, result: res('pass') },
    { n: 3, name: '月底 23:59', cat: '边界', result: res('fail', { diff: ['$：期望 1 项，实际 0 项'] }) },
    { n: 4, name: '跨年', cat: '边界', result: null },
    { n: 5, name: '月份不对', cat: '异常', result: res('broken', { error: 'ImportError: x' }) },
    { n: 6, name: '没分类的', result: res('fail', { error: 'ValueError: y' }) },
  ];
  assert.deepEqual(M.viewerCounts(cases), { pass: 2, fail: 2, broken: 1, unrun: 1, total: 6, need: 4, unseen: 1 });
  assert.deepEqual(M.caseGroups(cases, 'need', '').map((g) => [g.cat, g.rows.map((c) => c.n)]), [['正常', [2]], ['边界', [3]], ['异常', [5]], ['还没分类', [6]]]);
  assert.deepEqual(M.caseGroups(cases, 'fail', '').map((g) => g.rows.map((c) => c.n)), [[3], [5], [6]]);
  assert.deepEqual(M.caseGroups(cases, 'all', '').map((g) => g.rows.map((c) => c.n)), [[1, 2], [3, 4], [5], [6]]);
  assert.deepEqual(M.caseGroups(cases, 'all', '月').map((g) => g.rows.map((c) => c.n)), [[1, 2], [3], [5]]);
  assert.deepEqual(M.firstView(cases), { filter: 'need', n: 3 }); // 先选失败的那条
  assert.deepEqual(M.firstView([cases[0]]), { filter: 'all', n: 1 });
  assert.equal(M.whyFailed(cases[2]), '返回值：数量不同，期望 1 项，实际 0 项'); // 以前的结果里的写法，翻译成人话
  assert.equal(M.whyFailed({ result: { status: 'fail', diff: ['返回值的第 2 项：值不同，期望 2，实际 3', '$.items[0].amount：期望 12，实际 13', '$[0].id：期望有，实际没有', '$.b：期望没有，实际有 2'] } }),
    '返回值的第 2 项：值不同，期望 2，实际 3\n返回值的 items 的第 1 项的 amount：值不同，期望 12，实际 13\n返回值的第 1 项缺少字段 id\n返回值多出字段 b（实际是 2）');
  assert.equal(M.whyFailed(cases[4]), 'ImportError: x');
  assert.equal(M.whyFailed(cases[5]), 'ValueError: y');
  assert.equal(M.whyFailed(cases[0]), '');
  assert.deepEqual(M.payload([{ n: 1, name: 'a', cat: '正常', input: 1, expect: 2, unseen: true, result: {} }, { n: 2, name: 'b', input: 1, raises: '不合法' }]),
    { cases: [{ n: 1, name: 'a', cat: '正常', input: 1, expect: 2 }, { n: 2, name: 'b', cat: '正常', input: 1, raises: '不合法' }] });
  assert.match(M.parseJson('{', '输入').error, /^输入不是合法的 JSON/);
});

test('before confirming done: failures only warn, changes still waiting must be decided first', () => {
  const ran = { sets: [{ id: 'TS-1', title: 'a', result: 'pass', counts: { pass: 3, fail: 0, broken: 0 }, origin: 'new' },
    { id: 'TS-2', title: 'b', result: 'fail', counts: { pass: 1, fail: 2, broken: 0 }, origin: 'existing' },
    { id: 'TS-3', title: 'c', result: 'broken', counts: { pass: 0, fail: 0, broken: 4 }, origin: 'new' }], totals: { sets: 3, total: 10 } };
  const listed = { scope: 'R-2', sets: [{ id: 'TS-2', title: 'b', pending_changes: [{ id: 1, by: 'R-2' }, { id: 2, by: 'R-8' }] }] };
  const out = M.doneCheck(ran, listed);
  assert.equal(out.ok, false);
  assert.deepEqual(out.failing.map((f) => [f.id, f.fail, f.broken]), [['TS-2', 2, 0], ['TS-3', 0, 4]]);
  assert.deepEqual(out.pending.map((c) => [c.set, c.id]), [['TS-2', 1]]);
  assert.equal(out.mustDecide, true);
  assert.equal(M.failingText(out.failing[1]), '跑不起来，4 条都没有执行');
  assert.equal(M.failingText(out.failing[0]), '2 条失败');
  assert.deepEqual(M.doneCheck({ sets: [ran.sets[0]], totals: {} }, { scope: 'R-2', sets: [] }).ok, true);
  const broke = M.doneCheck({ error: '超时' }, { scope: 'R-2', sets: [] });
  assert.deepEqual([broke.ok, broke.error], [false, '没能运行测试集：超时']);
});

test('"let the AI change it" goes to the in-progress requirement (or fix) session, otherwise to a new everyday chat', () => {
  const reqs = [{ id: 'R-2', title: '按月筛选', state: 'open', worktree: '/w/R-2' }, { id: 'R-1', title: '账单列表', state: 'done' }];
  assert.deepEqual(M.aiTarget({ id: 'TS-1' }, { req: 'R-2' }, reqs), { kind: 'req', run: 'R-2', title: '「按月筛选」的开发会话', scope: { req: 'R-2' } });
  assert.deepEqual(M.aiTarget({ id: 'TS-1', in_progress: 'R-2' }, {}, reqs).run, 'R-2'); // 测试页打开的、进行中的需求新建的：只有它的会话能改
  assert.deepEqual(M.aiTarget({ id: 'TS-1' }, {}, reqs), { kind: 'chat', run: '', title: '新的日常聊天', scope: {} });
  assert.equal(M.aiTarget({ id: 'TS-1' }, { req: 'R-1' }, reqs).kind, 'chat'); // 已完成的需求：按主线
  assert.deepEqual(M.aiTarget({ id: 'TS-1', in_progress: 'F-003' }, {}, reqs), { kind: 'fix', run: 'fix:F-003', title: '修复 F-003 的会话', scope: { fix: 'F-003' } });
});

// ---------- 主进程 ----------
function loadMain(answers) {
  const calls = [], sent = [];
  for (const k of Object.keys(require.cache)) if (k.startsWith(main)) delete require.cache[k];
  const backendPath = require.resolve(path.join(main, 'backend'));
  const real = require(backendPath);
  const rf = async (args, rootDir, agent, timeout, input) => {
    calls.push({ args, input, timeout });
    const key = args.slice(0, 2).join(' ');
    const made = typeof answers[key] === 'function' ? answers[key](args) : answers[key];
    return made === undefined ? { ok: false, stdout: '', stderr: '不认识', timedOut: false } : { ok: !made.error, stdout: made.error ? '' : JSON.stringify(made), stderr: made.error || '', timedOut: false };
  };
  require.cache[backendPath].exports = Object.assign({}, real, { rf });
  const { state } = require(path.join(main, 'state'));
  state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-tsui-'));
  state.win = { isDestroyed: () => false, webContents: { send: (c, p) => sent.push([c, p]) } };
  state.runs = new Map();
  return { T: require(path.join(main, 'testsets')), state, calls, sent };
}
const SHOW = { id: 'TS-004', title: '按月筛选账单', feature: 'RF-101', feature_title: '账单列表', cases: 5, data_file: '/p/.relayforge/tests/TS-004.json', entry: 'tests/rf_list.py', in_progress: null, cases_list: [] };

test('main: which rf.py test commands it runs, with the scope; saving goes through standard input', async () => {
  const { T, calls } = loadMain({ 'test list': { sets: [], totals: {} }, 'test show': SHOW, 'test run': { sets: [] }, 'test save': SHOW, 'test approve': SHOW, 'test reject': SHOW, 'test stale': SHOW, 'test move': SHOW, 'test seen': { cleared: 1 }, 'test revive': SHOW });
  await T.list({ req: 'R-2' });
  await T.list({});
  await T.show({ id: 'TS-004', req: 'R-2' });
  await T.run({ req: 'R-2' });
  await T.run({ all: true });
  await T.run({ ids: ['TS-004'], fix: 'F-001', caseN: 3 });
  await T.save({ id: 'TS-004', req: 'R-2', caseN: 'new', payload: { name: 'x', input: 1, expect: 1 } });
  await T.act({ action: 'approve', id: 'TS-001', change: 1 });
  await T.act({ action: 'stale', id: 'TS-001', reason: '下线了' });
  await T.act({ action: 'move', id: 'TS-001', feature: 'RF-102', req: 'none' });
  await T.act({ action: 'seen', id: 'TS-001', caseN: 4 });
  assert.deepEqual(calls.map((c) => c.args.join(' ')), [
    'test list --req R-2 --json', 'test list --json', 'test show TS-004 --req R-2 --json',
    'test run --req R-2 --prep-check --json', 'test run --req R-2 --json', 'test run --all --prep-check --json', 'test run --all --json', // 先问有没有要你同意的准备步骤
    'test run TS-004 --fix F-001 --case 3 --prep-check --json', 'test run TS-004 --fix F-001 --case 3 --json', 'test save TS-004 --case new --req R-2 --file - --json', 'test approve TS-001 1 --json',
    'test stale TS-001 --reason 下线了 --json', 'test move TS-001 --feature RF-102 --req none --json', 'test seen TS-001 --case 4 --json']);
  assert.deepEqual(JSON.parse(calls[9].input), { name: 'x', input: 1, expect: 1 });
  assert.ok(calls[4].timeout >= 10 * 60 * 1000); // 运行给足时间
  assert.deepEqual(await T.act({ action: 'delete', id: 'TS-001' }), { error: '不认识的操作。' });
  assert.deepEqual(await T.show({ id: '../x' }), { error: '要给测试集编号。' });
  assert.deepEqual(await T.run({}), { error: '要说跑哪些测试集。' });
});

test('main: not agreeing tells the development session that proposed it; the AI request goes to that session with the prefix', async () => {
  const { T, calls, sent, state } = loadMain({ 'test reject': SHOW, 'test show': SHOW, 'req list': [{ id: 'R-2', title: '按月筛选', state: 'open', worktree: '/w' }] });
  const change = { id: 1, case: 1, name: '不带条件', op: 'edit', by: 'R-2' };
  await T.act({ action: 'reject', id: 'TS-001', change });
  assert.equal(calls[0].args.join(' '), 'test reject TS-001 1 --json');
  const requests = () => sent.filter(([channel]) => channel === 'tests:say');
  assert.equal(requests()[0]?.[1].id, 'R-2'); // 会话没开着，让界面打开它
  const queued = require(path.join(main, 'inbox')).messagesFor('R-2');
  assert.equal(queued.length, 1);
  assert.equal(queued[0].state, 'queued');
  assert.match(queued[0].text, /这是改坏了/);
  const made = await T.aiEdit({ id: 'TS-004', req: 'R-2', text: '补几条边界值' });
  assert.deepEqual([made.kind, made.run, made.handed.title], ['req', 'R-2', '「按月筛选」的开发会话']);
  const say = require(path.join(main, 'inbox')).messagesFor('R-2').at(-1).text; // 交给开发会话的话排在收件箱里
  assert.equal(requests()[1]?.[1].id, 'R-2');
  assert.match(say, /^【改测试集 TS-004「按月筛选账单」/);
  assert.match(say, /我在测试集查看器的「让 AI 改」里说：补几条边界值/);
  assert.match(say, /rf\.py test add TS-004/);
  assert.match(say, /不要手工编辑 \.relayforge\/tests\//);
  assert.match(say, /test run TS-004 --req R-2/);
  assert.match(say, /test analyze TS-004 --text/);
  assert.match(say, /不要运行 test approve、reject、stale、revive、move、save、seen/);
  assert.equal((await T.show({ id: 'TS-004' })).handed.run, 'R-2'); // 查看器里显示交给了谁
  // 会话开着、这一轮已经说完：经收件箱马上送进去（在干活或等批准时会排队，见 test_app_inbox.cjs）
  const typed = [];
  state.runs.set('R-2', { id: 'R-2', term: { stdin: { write: (x) => typed.push(x) } } });
  require(path.join(main, 'attention')).setPhase('R-2', 'reply');
  await T.aiEdit({ id: 'TS-004', req: 'R-2', text: '换一批数据' });
  assert.match(typed.join(''), /换一批数据/);
});

test('main: from the test page with no requirement the request opens a new chat that may use the AI test commands', async () => {
  const { T, state } = loadMain({ 'test show': SHOW, 'req list': [] });
  const made = await T.aiEdit({ id: 'TS-004', text: '补几条异常输入' });
  assert.equal(made.kind, 'chat');
  assert.ok(state.testChats.has(made.sessionId));
  assert.match(made.say, /这是主线上的数据/);
  assert.equal(made.run, 'session:' + made.sessionId);
  assert.deepEqual(await T.aiEdit({ id: 'TS-004', text: '  ' }), { error: '先写想怎么改。' });
  const { hooks } = require(path.join(main, 'claude-settings'));
  const { BUNDLED } = require(path.join(main, 'config'));
  const allowed = (h) => JSON.parse(h).permissions.allow.filter((x) => x.includes(' test ')).map((x) => x.replace(`python3 ${BUNDLED} test `, '').replace('python3 .relayforge/bin/rf.py test ', ''));
  const chat = allowed(hooks(null, false, false, false, true));
  for (const a of ['add', 'edit', 'rm', 'run', 'analyze']) assert.ok(chat.some((x) => x.startsWith('Bash(' + a)), a);
  for (const a of ['new', 'bridge', 'approve', 'save', 'seen']) assert.ok(!chat.some((x) => x.startsWith('Bash(' + a)), a);
  assert.ok(!allowed(hooks(null, false)).some((x) => x.startsWith('Bash(add'))); // 普通的日常聊天不放行
  assert.ok(!allowed(hooks('/drafts', false, false, false, true)).some((x) => x.startsWith('Bash(add'))); // 立项会话即使带了也不放行
});

test('main: before done, no test sets means nothing to do; otherwise it runs them and reports', async () => {
  let lists = 0;
  const empty = loadMain({ 'test list': { scope: 'R-2', sets: [], totals: { pending_changes: 0 } } });
  assert.deepEqual(await empty.T.beforeDone('R-2'), { ok: true, none: true });
  assert.deepEqual(empty.calls.map((c) => c.args[1]), ['list']);
  const some = loadMain({
    'test list': () => ({ scope: 'R-2', sets: [{ id: 'TS-4', title: 'a', pending_changes: lists++ ? [] : [{ id: 1, by: 'R-2' }] }], totals: { pending_changes: 1 } }),
    'test run': { sets: [{ id: 'TS-4', title: 'a', result: 'fail', counts: { pass: 1, fail: 1, broken: 0 }, origin: 'new' }], totals: { sets: 1, total: 2 } },
  });
  const out = await some.T.beforeDone('R-2');
  assert.deepEqual(some.calls.map((c) => c.args.slice(0, 3).join(' ')), ['test list --req', 'test run --req', 'test run --req', 'test list --req']);
  assert.deepEqual([out.ok, out.failing.length, out.pending.length], [false, 1, 0]); // 待确认的改动以运行以后读到的为准
  assert.deepEqual(await some.T.beforeDone('../x'), { ok: true });
});

test('main: the watcher notices changes under .relayforge/tests/ (data, stats and results)', async () => {
  const { T, state, sent } = loadMain({});
  const dir = path.join(state.project, '.relayforge', 'tests', 'results', 'TS-001');
  fs.mkdirSync(dir, { recursive: true });
  T.watch();
  assert.equal(sent.length, 0);
  const file = path.join(dir, 'main.json');
  fs.writeFileSync(file, '{}');
  fs.utimesSync(file, new Date(), new Date(Date.now() + 5000));
  T.watch();
  assert.deepEqual(sent.map((x) => x[0]), ['tests:changed']);
});

// 上次全量（backend/relayforge/testsets/store.py 的 full_status）：合并前的检查只跑一部分，剩下的靠全量；测试页上说清楚它新不新
test('the tests page says when the last full run was and whether it is out of date', () => {
  assert.equal(M.fullLine(null), '');
  assert.match(M.fullLine({ last: null, stale: true }), /还没跑过全量/);
  const last = { at: '2026-11-23 10:15:00', commit: 'abcdef1234567', failing: [] };
  assert.equal(M.fullLine({ last, stale: false, behind: 0 }), '上次全量：11-23 10:15，在 abcdef12，都通过了。');
  assert.match(M.fullLine({ last: { ...last, failing: ['TS-002'] }, stale: true, behind: 3 }), /没过 TS-002。之后又合进来 3 次，还没跑过全量。/);
});
