// 需求之间的冲突在界面上的说法（app/src/renderer/scripts/conflicts-model.js），和主进程什么时候重算（app/src/main/conflicts.js）。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const C = require(path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts', 'conflicts-model.js'));
const main = path.join(__dirname, '..', 'app', 'src', 'main');
const plain = (x) => JSON.parse(JSON.stringify(x));

const data = {
  base: 'main',
  items: [
    { id: 'R-1', kind: 'req', title: '导出', committed: ['a.py', 'b.py'], uncommitted: ['n.md'], base_conflicts: ['a.py'] },
    { id: 'R-2', kind: 'req', title: '导入', committed: ['b.py', 'c.py', 'd.py', 'e.py'], uncommitted: [], base_conflicts: [] },
    { id: 'F-1', kind: 'fix', title: '修复 F-1', committed: ['c.py'], uncommitted: [], base_conflicts: null },
    { id: 'R-3', kind: 'req', title: '列表', committed: ['z.py'], uncommitted: [], base_conflicts: [] },
  ],
  pairs: [
    { a: 'R-1', b: 'R-2', files: ['b.py'], conflicts: ['b.py'] },
    { a: 'R-2', b: 'F-1', files: ['c.py'], conflicts: [] },
  ],
};

test('a card shows conflicts first, then overlaps, at most two lines', () => {
  assert.deepEqual(plain(C.cardNotes(data, 'R-1')), [
    { cls: 'bad', text: '⚠ 和 main 合并会冲突：a.py', title: 'a.py' },
    { cls: 'bad', text: '⚠ 和《导入》会冲突：b.py', title: 'b.py' },
  ]);
  assert.deepEqual(plain(C.cardNotes(data, 'R-2')).map((n) => n.text), ['⚠ 和《导出》会冲突：b.py', '和「修复 F-1」都在改：c.py']);
  assert.deepEqual(plain(C.cardNotes(data, 'R-3')), []); // 谁都没撞上
  assert.deepEqual(plain(C.cardNotes(data, 'R-9')), []); // 不在进行中
  assert.deepEqual(plain(C.cardNotes(null, 'R-1')), []); // 还没算过
});

test('the panel lists every file and says what can be done', () => {
  const lines = plain(C.panelLines(data, 'R-1'));
  assert.equal(lines.length, 2);
  assert.match(lines[0].hint, /让开发会话现在就把 main 合并进来/);
  assert.equal(lines[1].other, 'R-2');
  assert.match(lines[1].hint, /设成前置需求/);
  assert.equal(C.summary(data, 'R-1'), '改了 2 个文件，另有 1 个没提交');
  const many = { ...data, pairs: [{ a: 'R-1', b: 'R-2', files: ['1', '2', '3', '4', '5'], conflicts: [] }] };
  assert.equal(C.panelLines(many, 'R-1')[1].text, '和《导入》都在改：1、2、3 等 5 个文件');
});

function mainConflicts(answer) {
  const sent = [], calls = [], timers = [];
  const state = { project: '/p' };
  const mocks = {
    './backend': { rf: async (args) => { calls.push(args); return answer(); }, parse: (t, f) => { try { return JSON.parse(t); } catch (_) { return f; } } },
    './state': { state, send: (channel, payload) => sent.push([channel, payload]) },
  };
  const context = vm.createContext({ require: (id) => (Object.hasOwn(mocks, id) ? mocks[id] : require(id)), module: { exports: {} },
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimeout: (n) => { if (timers[n - 1]) timers[n - 1].cancelled = true; } });
  vm.runInContext(fs.readFileSync(path.join(main, 'conflicts.js'), 'utf8'), context);
  return { M: context.module.exports, sent, calls, timers, state };
}

test('the main process recomputes after a turn ends, debounced, and pushes the result', async () => {
  const h = mainConflicts(() => ({ ok: true, stdout: JSON.stringify(data) }));
  h.M.settled('R-1', 'running'); // 干活中途不算
  assert.equal(h.timers.length, 0);
  h.M.settled('R-1', 'reply');
  h.M.settled('R-2', 'answer'); // 又有动静：往后推
  assert.equal(h.timers.filter((t) => !t.cancelled).length, 1);
  assert.equal(h.timers.at(-1).ms, 10000);
  await h.timers.at(-1).fn();
  assert.deepEqual(plain(h.calls), [['conflicts', '--json']]);
  assert.equal(h.sent[0][0], 'conflicts');
  assert.equal(h.sent[0][1].items.length, 4);
  assert.equal(h.M.current().base, 'main');
  h.M.reset();
  assert.equal(h.M.current(), null);
});

test('a first look from the window is not pushed back by later activity', () => {
  const h = mainConflicts(() => ({ ok: true, stdout: JSON.stringify(data) }));
  h.M.schedule(0); // 界面刚打开、还没算过
  h.M.schedule(); // 紧接着需求列表刷新
  assert.deepEqual(h.timers.map((t) => [t.ms, !!t.cancelled]), [[0, false]]);
});

test('a failed run keeps the last result', async () => {
  let ok = true;
  const h = mainConflicts(() => (ok ? { ok: true, stdout: JSON.stringify(data) } : { ok: false, stdout: '' }));
  await h.M.refresh();
  ok = false;
  await h.M.refresh();
  assert.equal(h.sent.length, 1);
  assert.equal(h.M.current().items.length, 4);
});

test('the development sessions are told: sync now when it conflicts with the target, keep changes small when it conflicts with another', () => {
  const h = mainConflicts(() => ({ ok: true, stdout: '{}' }));
  const withDraft = { ...data, items: [...data.items, { id: 'R-4', kind: 'req', state: 'draft', title: '草稿', committed: [], uncommitted: [], base_conflicts: ['q.py'] }] };
  withDraft.items = withDraft.items.map((i) => ({ state: i.kind === 'fix' ? 'fixing' : 'open', ...i }));
  const out = plain(h.M.notices(withDraft));
  assert.deepEqual(out.map((n) => [n.to, n.key]), [
    ['R-1', 'conflict:base:R-1:a.py'],
    ['R-1', 'conflict:pair:R-2:b.py'],
    ['R-2', 'conflict:pair:R-1:b.py'],
  ]); // 立项中的 R-4 不提醒（立项会话只读）；只是改到同一处的不提醒
  assert.ok(out.every((n) => n.from === 'conflicts' && n.open === false)); // 不替你打开会话
  assert.match(out[0].text, /现在就运行 git merge main/);
  assert.match(out[1].text, /需求《导入》也改了 b\.py/);
  assert.match(out[1].text, /谁等谁由用户在应用里决定/);
});

test('a requirement still in intake is told, from history, who it may run into', () => {
  const pred = {
    features: ['RF-101'], history: [{ id: 'R-0', title: '旧导出' }, { id: 'R-00', title: '更旧的导出' }],
    predicted: [{ path: 'export.py', count: 2, from: ['R-0', 'R-00'] }, { path: 'a.py', count: 1 }, { path: 'b.py', count: 1 }, { path: 'c.py', count: 1 }, { path: 'd.py', count: 1 }],
    against: [
      { id: 'R-2', title: '导出加筛选', state: 'open', features: [], files: ['export.py'] },
      { id: 'R-3', title: '导出加合计', state: 'draft', features: ['RF-101'], files: [] },
    ],
  };
  const lines = plain(C.predictLines(pred));
  assert.deepEqual(lines.map((l) => l.text), ['可能撞上《导出加筛选》（进行中）：已经在改预计会动到的 export.py', '可能撞上《导出加合计》（立项中）：同样要改 RF-101']);
  assert.match(lines[0].hint, /设成这个需求的前置需求/);
  assert.equal(C.predictSummary(pred), '按 2 个改过同样功能点的需求推测，可能会动到 export.py、a.py、b.py、c.py 等 5 个文件');
  assert.equal(C.predictSummary({ history: [], predicted: [], against: [] }), '没有改过同样功能点的历史需求，预测不了会动到哪些文件');
  assert.deepEqual(plain(C.predictLines(null)), []);
});

test('the main process predicts once a minute per requirement and only for requirement ids', async () => {
  const h = mainConflicts(() => ({ ok: true, stdout: JSON.stringify({ id: 'R-1', against: [] }) }));
  assert.equal((await h.M.predict('R-1')).id, 'R-1');
  await h.M.predict('R-1');
  assert.deepEqual(plain(h.calls), [['conflicts', '--predict', 'R-1', '--json']]);
  assert.equal(await h.M.predict('../x'), null);
  h.M.reset();
  await h.M.predict('R-1');
  assert.equal(h.calls.length, 2); // 换项目以后重算
});

// 知识保鲜：开发中改到了「要改的功能点」以外的功能点的代码（backend/relayforge/facts/fresh.py 的 outside_scope），面板上标出来、告诉开发会话一次，只提示
test('changing the code of a feature the requirement did not declare is pointed out, not blocked', () => {
  const h = mainConflicts(() => ({ ok: true, stdout: '{}' }));
  const withScope = { base: 'main', pairs: [], items: [
    { id: 'R-9', kind: 'req', state: 'open', title: '导出', committed: ['calc.py'], uncommitted: [], base_conflicts: [], outside: [{ feature: 'RF-101', files: ['calc.py'] }] },
    { id: 'R-8', kind: 'req', state: 'draft', title: '立项中', committed: [], uncommitted: [], base_conflicts: [], outside: [{ feature: 'RF-102', files: ['x.py'] }] },
  ] };
  const line = plain(C.panelLines(withScope, 'R-9'))[0];
  assert.equal(line.cls, 'warn');
  assert.equal(line.text, '改到了 RF-101 的代码：calc.py');
  assert.match(line.hint, /不在这个需求「要改的功能点」里/);
  const told = plain(h.M.notices(withScope)).filter((n) => n.key.startsWith('scope:'));
  assert.deepEqual(told.map((n) => [n.to, n.key]), [['R-9', 'scope:R-9:RF-101:calc.py']]); // 立项中的不提醒
  assert.match(told[0].text, /rf\.py note 记下为什么要改它/);
});
