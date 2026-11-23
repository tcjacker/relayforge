// 测试用例（三个会话的协作分工，应用这一侧）：三种开场白里的用例（立项写用例、开发照着做不能改、测试对接和执行；老需求退回原来的做法）、
// 面板的 model 层（用例按验收条件分组、结果、提示、用例改动的两边）、面板里一条验收条件下面的用例块、权限规则、开发会话的选择也能改
const test = require('node:test');
const assert = require('node:assert/strict');
const locale = require('../app/src/main/locale');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const base = path.join(__dirname, '..', 'app', 'src');
const main = path.join(base, 'main');
const scripts = path.join(base, 'renderer', 'scripts');
const CM = require(path.join(scripts, 'cases-model.js'));

const CHECKS = [
  { n: 1, text: '账单列表能按月份筛选', key: '账单列表能按月份筛选', kind: 'data', why: '给几笔账和月份就能比对', steps: [], cases: [1, 2, 3] },
  { n: 2, text: '筛选后的标题显示月份', key: '筛选后的标题显示月份', kind: 'ui', why: '标题显示「2026 年 10 月」', steps: [], cases: [] },
  { n: 3, text: '导出的 CSV 有合计行', key: '导出的 CSV 有合计行', kind: null, why: '', steps: [], cases: [] },
];
const CASES = [
  { n: 1, check: '账单列表能按月份筛选', name: '月中的账', cat: '正常', given: '10 月 15 日记了一笔账', when: '选 2026 年 10 月', then: '这笔账在列表里', source: 'intake', live: true },
  { n: 2, check: '账单列表能按月份筛选', name: '月底最后一刻', cat: '边界', given: '10 月 31 日 23:59 记了一笔账', when: '选 2026 年 10 月', then: '这笔账在列表里', source: 'intake', live: true },
  { n: 3, check: '账单列表能按月份筛选', name: '没有账的月份', cat: '异常', given: '9 月没有记账', when: '选 2026 年 9 月', then: '列表是空的，提示「这个月没有账单」', source: 'test', live: true },
  { n: 4, check: '账单列表能按月份筛选', name: '删了的', cat: '正常', given: 'a', when: 'b', then: 'c', source: 'intake', live: false },
];
const req = (extra = {}) => Object.assign({
  id: 'R-1', title: '账单按月筛选', file: '.relayforge/requirements/R-1.md', state: 'open', stage: 'build', sections: { 验收条件: CHECKS.map((c) => c.text) },
  gate: { ok: true, missing: '', uncovered: [{ n: 3, text: '导出的 CSV 有合计行' }], hint: 'x' },
  testing: { checks: CHECKS, cases: CASES, has_cases: true, case_changes: [], rounds: [], round: 1 },
}, extra);

function prompts() {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-uc-'));
  return require(path.join(main, 'prompts'));
}

test('intake prompt: a step after the acceptance conditions to classify them and write business-language cases', () => {
  const P = prompts();
  const text = P.intake(req({ state: 'draft' }), 'S-1', [], false, '/drafts');
  for (const re of [/5\. 用例：验收条件定下来以后，给每条验收条件归类、写测试用例/, /怎么写测试用例（验收条件定下来以后再做/,
    /req classify 序号 数据\|界面 --why "一句话理由"/, /req case-add 验收条件序号 --name "覆盖哪种情况" --cat 正常\|边界\|异常 --given "前提" --when "操作" --then "预期"/,
    /只用业务的话写：前提、操作、预期都说用户看得到、说得出的东西.*不提函数名、文件、接口字段/, /想一想正常、边界、异常几种情况.*不凑数/,
    /归「界面」的不写用例，在 --why 里写清楚「应该看到什么」；具体点哪里、怎么操作，等界面做出来以后由测试会话补成检查步骤/,
    /req case-edit 用例编号/, /没做到也能立项（右边会提示还有哪几条），由我决定/, /这个会话不能改代码/]) assert.match(text, re);
  assert.ok(text.indexOf('4. 可验收') < text.indexOf('5. 用例'));
});

test('development prompt carries the cases in full and says it may not change them; without cases nothing is added', () => {
  const P = prompts();
  const text = P.develop(req(), 'S-1');
  for (const re of [/这个需求的测试用例和验收条件的归类/, /第 1 条验收条件：账单列表能按月份筛选（能用数据验证：给几笔账和月份就能比对）/,
    /用例 2「月底最后一刻」〔边界〕前提：10 月 31 日 23:59 记了一笔账；操作：选 2026 年 10 月；预期：这笔账在列表里/,
    /用例 3「没有账的月份」〔异常，测试补充的〕/, /第 2 条验收条件：筛选后的标题显示月份（只能人工看：标题显示「2026 年 10 月」）/,
    /第 3 条验收条件：导出的 CSV 有合计行（还没归类）\n  - （还没有用例）/,
    /你的目标是让这些用例通过/, /用例你看得到、不能改：认为哪条写错了，用 .* case-edit 用例编号 .* --why "理由"/, /等我在应用里同意，不要绕过/]) assert.match(text, re);
  assert.doesNotMatch(text, /删了的/);
  assert.match(P.following(req(), 'S-1'), /你的目标是让这些用例通过/);
  const old = P.develop(req({ testing: { checks: CHECKS, cases: [], has_cases: false } }), 'S-1');
  assert.doesNotMatch(old, /这个需求的测试用例/);
  assert.match(P.DEV_TEST_GUIDE(), /不要运行 .* steps、verdict/);
  assert.match(P.DEV_TEST_GUIDE(), /用例本身写错了、不是代码错了的.*case-edit/);
});

test('test prompt with cases: join the cases, never change their meaning, may add supplements, the new rule for ok; without cases: the old way', () => {
  const P = prompts();
  const text = P.testing(req({ stage: 'test' }), 'S-9', false);
  for (const re of [/你的工作是把这些用例对接成能跑的东西并执行，不是自己决定测什么/, /测试用例和归类：\n<<<\n第 1 条验收条件/,
    /每条数据必须挂上它对应的用例编号：.* add 测试集编号 --usecase 用例编号/, /读代码只是为了接上，不能改变用例的意思：前提对应到输入（和准备动作），操作对应到调用，预期对应到期望输出/,
    /「只能人工看」的验收条件：界面已经做出来了，把立项写的「应该看到什么」补成具体的检查步骤/, /可以补充用例：.*req case-add .*加成「测试补充的」用例/,
    /不能减少或改动立项写的用例和归类/, /req cases R-1/, /test run --req R-1/, /旧测试集失败的逐条判断/, /env propose 测试环境编号/, /不改产品代码/,
    /立项写的每条「能用数据验证」的用例都至少对接了一条数据并且运行过（程序会核对）；实在对接不了的，逐条说明原因：--unlinked "用例编号｜为什么对接不了"/,
    /verdict problems --problem "用例 3「名称」｜现象｜你的判断"/]) assert.match(text, re);
  assert.doesNotMatch(text, /先把验收条件逐条归类/);
  const old = P.testing(req({ stage: 'test', testing: { checks: CHECKS, cases: [], has_cases: false } }), 'S-9', false);
  assert.match(old, /这个需求立项时没有写测试用例，照原来的做法/);
  assert.match(old, /先把验收条件逐条归类.*req classify 序号\[,序号…\] 数据\|界面/);
  assert.doesNotMatch(old, /--usecase/);
  const again = P.retesting(req({ testing: Object.assign({}, req().testing, { round: 2, rounds: [{ n: 1, verdict: 'problems', problems: [{ k: 1, where: '用例 2「月底最后一刻」', what: '不在列表里', judge: '改坏了' }], fixed: { note: '修了' } }] }) }));
  assert.match(again, /req cases R-1 看每条用例的结果/);
  assert.match(again, /现在的测试用例和归类：/);
  const back = P.testProblems(req({ testing: Object.assign({}, req().testing, { rounds: [{ n: 1, verdict: 'problems', problems: [{ k: 1, where: '用例 2', what: 'x', judge: 'y' }] }] }) }));
  assert.match(back, /用例本身写错了、不是代码错了的：不要改代码去迁就，也不要自己改用例，用 .* case-edit 用例编号 … --why "理由" 提给我/);
});

test('model: cases grouped under their conditions with results; the hint for uncovered conditions; case changes in words', () => {
  const report = { by_case: { 1: { status: 'pass', links: [{ set: 'TS-001', case: 1, name: '月中', status: 'pass' }] }, 2: { status: 'fail', links: [{ set: 'TS-001', case: 2, name: '月底', status: 'fail' }] }, 3: { status: 'unlinked', links: [] } } };
  const conds = CM.conditions(req(), report);
  assert.deepEqual(conds.map((c) => [c.n, c.cases.map((x) => x.n), c.uncovered]), [[1, [1, 2, 3], false], [2, [], false], [3, [], true]]);
  assert.equal(conds[0].cases[1].status, 'fail');
  assert.equal(CM.condLine(conds[0], true), '3 条用例 · 1 条有失败 · 1 条还没对接 · 1 条都通过 · 其中 1 条测试补充的');
  assert.equal(CM.condLine(conds[0], false), '3 条用例 · 其中 1 条测试补充的');
  assert.equal(CM.condLine(conds[1], true), '只能人工看：标题显示「2026 年 10 月」');
  assert.equal(CM.condLine(conds[2], true), '还没有用例，也没标「只能人工看」');
  assert.deepEqual(CM.counts(conds[0].cases), { pass: 1, fail: 1, broken: 0, unrun: 0, unlinked: 1, total: 3, supplements: 1 });
  const ask = CM.startAsk(req({ state: 'draft' }), '方案');
  assert.match(ask, /测试用例 3 条/);
  const dlg = CM.startDialog(req({ state: 'draft' }), '方案');
  assert.equal(dlg.warn, true);
  assert.equal(dlg.title, '确认「账单按月筛选」立项，开始开发？');
  assert.match(ask, /注意：还有 1 条验收条件没有测试用例、也没标「只能人工看」：\n· 第 3 条：导出的 CSV 有合计行\n可以先立项（不拦）/);
  assert.doesNotMatch(CM.startAsk(req({ gate: { ok: true, uncovered: [] } }), '方案'), /注意/);
  assert.match(CM.gateNote(req()), /还有 1 条验收条件没有测试用例.*（第 3 条）。可以先立项，不拦。/);
  assert.equal(CM.gateNote(req({ gate: { ok: false, missing: '还没有验收条件' } })), '还没有验收条件');
  const changes = [
    { k: 1, target: '用例 2', op: 'edit', by: '开发会话 S-2', actor: 'develop', why: '需求改了', state: 'pending', old: CASES[1], new: Object.assign({}, CASES[1], { then: '不在列表里' }) },
    { k: 2, target: '第 2 条验收条件', op: 'classify', by: '测试会话 S-3', actor: 'test', why: '能比对', state: 'rejected', old: { kind: 'ui', why: '看标题' }, new: { kind: 'data', why: '能比对' } },
    { k: 3, target: '用例 1', op: 'delete', by: '立项会话 S-1', actor: 'intake', why: '重复', state: 'approved', old: CASES[0], new: null },
  ];
  const r = req({ testing: Object.assign({}, req().testing, { case_changes: changes }) });
  assert.deepEqual(CM.pending(r).map((c) => c.k), [1]);
  const v = CM.changes(r);
  assert.match(v[0].old, /「月底最后一刻」〔边界〕前提：10 月 31 日 23:59 记了一笔账；操作：选 2026 年 10 月；预期：这笔账在列表里/);
  assert.match(v[0].now, /预期：不在列表里/);
  assert.equal(v[1].old, '只能人工看：看标题');
  assert.equal(v[1].now, '能用数据验证：能比对');
  assert.equal(v[2].now, '（删掉这条用例）');
  assert.deepEqual(CM.savePayload('edit', { case: '2', check: '1', name: ' 新名字 ', given: '', cat: '边界' }), { op: 'edit', case: 2, check: 1, name: '新名字', cat: '边界' });
  assert.equal(CM.checkForm({ name: 'a', given: '', when: 'b', then: '' }), '还要写：前提、预期（用业务的话；没有特别的前提就写「无特别前提」）。');
  assert.match(CM.rejectText(v[0]), /用例改动 1 我没有同意/);
});

test('panel: under a condition, each case with category, three parts, result and the data it joins; the uncovered one is pointed out', () => {
  class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.textContent = ''; this.className = ''; this.open = false; }
    appendChild(n) { this.children.push(n); return n; }
    addEventListener() {}
    get text() { return this.textContent + this.children.map((c) => c.text).join('|'); }
    all() { return [this].concat(...this.children.map((c) => c.all())); }
  }
  const context = vm.createContext({ document: { createElement: (tag) => new Node(tag), getElementById: () => new Node('x') }, window: {}, setTimeout, TestsModel: require('../app/src/renderer/scripts/tests-model') });
  for (const file of ['html-text', 'core', 'cases-model', 'cases']) vm.runInContext(fs.readFileSync(path.join(scripts, file + '.js'), 'utf8'), context);
  vm.runInContext('var states = {}; var TS = { panel: {} };', context);
  context.TS.panel['R-1'] = { usecases: { by_case: { 1: { status: 'pass', links: [{ set: 'TS-001', case: 1, name: '月中', status: 'pass' }] }, 2: { status: 'fail', links: [{ set: 'TS-001', case: 2, name: '月底', status: 'fail' }] }, 3: { status: 'unlinked', links: [] } } } };
  const li = new Node('li');
  context.caseBlock(li, req(), CHECKS[0]);
  const text = li.text;
  for (const want of ['3 条用例 · 1 条有失败', '用例 2', '月底最后一刻', '边界', '有失败', '前提', '10 月 31 日 23:59 记了一笔账', '✗ TS-001 #2 月底', '测试补充的', '还没对接', '还没有对接的测试数据。', '加用例', '改', '删']) assert.ok(text.includes(want), want);
  const link = li.all().find((n) => n.dataset && n.dataset.ucset === 'TS-001' && n.textContent.includes('#2'));
  assert.equal(link.dataset.ucref, 'R-1#2'); // 点了跳到查看器里这条用例对接的数据
  const uncovered = new Node('li');
  context.caseBlock(uncovered, req(), CHECKS[2]);
  assert.ok(uncovered.all().some((n) => n.className === 'uc-head warn'));
  const done = new Node('li');
  context.caseBlock(done, req({ state: 'done' }), CHECKS[0]);
  assert.ok(!done.text.includes('加用例'), 'finished requirements are read-only');
  const draft = new Node('li');
  context.caseBlock(draft, req({ state: 'draft' }), CHECKS[0]);
  assert.ok(!draft.text.includes('有失败') && draft.text.includes('加用例'), 'while 立项中 no results, but you can edit');
});

test('permissions: case commands for requirement sessions, the user decisions denied in every session', () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-ucperm-'));
  require(path.join(main, 'state')).state.project = project;
  const { hooks } = require(path.join(main, 'claude-settings'));
  const { BUNDLED } = require(path.join(main, 'config'));
  const intake = JSON.parse(hooks('/drafts/R-1', false, false, false, false, false, false, { req: true })).permissions;
  const tester = JSON.parse(hooks(null, false, false, false, false, false, false, { role: 'test', place: '/wt/R-1', req: true })).permissions;
  const dev = JSON.parse(hooks(null, false, false, true, false, false, false, { role: 'develop', req: true })).permissions;
  const fix = JSON.parse(hooks(null, false, false, true, false, false, false, { role: 'fix' })).permissions;
  const chat = JSON.parse(hooks(null, false)).permissions;
  const rule = (rf, what) => `Bash(python3 ${rf} ${what}:*)`;
  for (const rf of [BUNDLED, '.relayforge/bin/rf.py']) {
    for (const a of ['case-add', 'case-edit', 'case-rm', 'cases', 'classify']) {
      for (const [name, p] of [['intake', intake], ['test', tester], ['develop', dev]]) assert.ok(p.allow.includes(rule(rf, 'req ' + a)), name + ' ' + a);
      for (const [name, p] of [['fix', fix], ['chat', chat]]) assert.ok(!p.allow.includes(rule(rf, 'req ' + a)), name + ' has no ' + a);
    }
    for (const a of ['case-ok', 'case-no', 'case-save', 'choose']) for (const [name, p] of [['intake', intake], ['test', tester], ['develop', dev], ['fix', fix], ['chat', chat]]) assert.ok(p.deny.includes(rule(rf, 'req ' + a)), name + ' denied ' + a);
    for (const a of ['steps', 'verdict']) assert.ok(intake.deny.includes(rule(rf, 'req ' + a)) || !intake.allow.includes(rule(rf, 'req ' + a)), 'intake ' + a);
  }
  const src = fs.readFileSync(path.join(main, 'sessions.js'), 'utf8');
  assert.match(src, /req: !!\(req && !probing && !id\.startsWith\('session:'\) && \['draft', 'open'\]\.includes\(req\.state\)\)/);
});

test('the session choice is read-only after the requirement is made: no pickers or save buttons on the panel, no IPC to change it', () => {
  const html = fs.readFileSync(path.join(base, 'renderer', 'index.html'), 'utf8');
  for (const id of ['development-cli', 'development-model', 'development-save', 'testing-cli', 'testing-save', 'testing-session']) assert.doesNotMatch(html, new RegExp('id="' + id + '"'));
  assert.match(html, /id="session-choices"/);
  assert.doesNotMatch(html, /会话选择/);
  const preload = fs.readFileSync(path.join(base, 'preload', 'index.js'), 'utf8');
  assert.doesNotMatch(preload, /chooseDevelopment|chooseTesting/);
  for (const name of ['saveCase', 'decideCase']) assert.match(preload, new RegExp(name + ': '));
});

test('main process: saving and deciding go through rf.py; a rejected change goes back to the session that proposed it', async () => {
  const calls = [], told = [];
  const mocks = {
    './backend': { uiText: locale.uiText,  rf: async (args, cwd, env, timeout, input) => { calls.push({ args, input }); return { ok: true, stdout: JSON.stringify({ id: 'R-1', change: { k: 4, target: '用例 2', op: 'edit', why: '理由', actor: 'test', state: 'rejected' } }) }; }, lastLine: (x) => x, parse: (x, d) => { try { return JSON.parse(x); } catch (_) { return d; } } },
    './state': { state: { project: '/p' }, send() {} },
    './testsets': { tell: (run, text) => told.push([run, text]) },
    './testing': { follow: async () => {} },
    '../renderer/scripts/requirement-sessions': require(path.join(scripts, 'requirement-sessions.js')),
  };
  const context = vm.createContext({ require: (id) => id === './locale' ? locale : mocks[id] || require(id), module: { exports: {} }, console });
  vm.runInContext(fs.readFileSync(path.join(main, 'requirements.js'), 'utf8'), context);
  const R = context.module.exports;
  await R.saveCase('R-1', { op: 'add', check: 1, name: 'a' });
  assert.deepEqual(Array.from(calls[0].args), ['req', 'case-save', 'R-1', '--file', '-', '--json']);
  assert.deepEqual(JSON.parse(calls[0].input), { op: 'add', check: 1, name: 'a' });
  await R.decideCase('R-1', 4, false);
  assert.deepEqual(Array.from(calls.find((c) => c.args[1] === 'case-no').args), ['req', 'case-no', 'R-1', '4', '--json']);
  assert.equal(told[0][0], 'test:R-1');
  assert.match(told[0][1], /【用例改动 4 我没有同意】你提的「用例 2」修改（理由：理由）我没有同意/);
  assert.equal((await R.decideCase('R-1; rm -rf', 4, true)).error, '要给需求编号和改动编号。');
});
