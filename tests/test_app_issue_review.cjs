// 问题页的「刷新」：AI 在后台复查待处理问题（R-20261121-5b21）。界面的 model 层和主进程怎么读进度、开始、确认、切换项目。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const M = require(path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts', 'issue-review-model.js'));

const item = (n, status, more = {}) => ({ id: 'I-00' + n, num: '#00' + n, title: '问题 ' + n, status, ...more });
const row = (n, kind, more = {}) => ({ id: 'I-0' + n, num: '#0' + n, title: '问题 ' + n, kind, detail: kind + ' 的依据', ran: '重跑了测试',
  ...(['gone', 'stale', 'merge'].includes(kind) ? { stamp: 's' + n } : {}), ...more });

test('button: refresh when idle or failed, progress while running, results when ready', () => {
  assert.equal(M.button({ state: 'idle' }).label, '刷新');
  assert.equal(M.button({ state: 'failed', error: 'x' }).label, '刷新');
  const running = { state: 'running', done: 2, items: [item(1, 'done'), item(2, 'done'), item(3, 'running'), item(4, 'queued'), item(5, 'queued')] };
  assert.deepEqual([M.button(running).label, M.button(running).running], ['复查中 2/5', true]);
  assert.deepEqual([M.button({ state: 'ready', rows: [] }).label, M.button({ state: 'ready', rows: [] }).dot], ['复查结果', true]);
  assert.equal(M.active(running), true);
  assert.equal(M.active({ state: 'ready' }), true);
  assert.equal(M.active({ state: 'failed' }), false);
});

test('list rows: the one being checked says 复查中…, finished ones say 已复查, only while running', () => {
  const s = { state: 'running', done: 1, current: 'I-002', items: [item(1, 'done'), item(2, 'running'), item(3, 'queued')] };
  assert.match(M.rowMark(s, 'I-001'), /已复查/);
  assert.match(M.rowMark(s, 'I-002'), /复查中…/);
  assert.equal(M.rowMark(s, 'I-003'), '');
  assert.equal(M.rowMark(s, 'I-009'), '');
  assert.equal(M.rowMark({ state: 'ready', items: s.items }, 'I-001'), '');
});

test('strip above the list: progress with the current issue, then results, then a failure with its reason', () => {
  const s = { state: 'running', done: 2, current: 'I-003', items: [item(1, 'done'), item(2, 'done'), item(3, 'running'), item(4, 'queued')] };
  const running = M.strip(s);
  assert.match(running.html, /AI 复查中 2\/4/);
  assert.match(running.html, /正在查 #003 问题 3/);
  assert.match(running.html, /width:50%/);
  assert.match(running.html, /看进度/);
  assert.match(M.strip({ ...s, phase: 'group', current: null }).html, /同一个故障/);
  const ready = M.strip({ state: 'ready', rows: [row(12, 'gone'), row(14, 'still', { steps: 'a', observed: 'b' })] });
  assert.equal(ready.cls, 'ready');
  assert.match(ready.html, /2 条里 1 条可以处理/);
  const failed = M.strip({ state: 'failed', error: '复查 #003 时出错：超时。没有问题的状态被改动，可以再点「刷新」重来。' });
  assert.equal(failed.cls, 'failed');
  assert.match(failed.html, /复查 #003 时出错/);
  assert.equal(M.strip({ state: 'idle' }), null);
});

test('progress window lists each issue and what was done', () => {
  const html = M.progressHtml({ state: 'running', done: 1, items: [item(1, 'done', { ran: '重跑 test_x，通过' }), item(2, 'running'), item(3, 'queued')] });
  assert.match(html, /共|3 条待处理问题/);
  assert.match(html, /重跑 test_x，通过/);
  assert.match(html, /正在复现/);
  assert.match(html, /排队中/);
});

test('用例 5: five groups, each with conclusion and what AI did, only the first three can be ticked', () => {
  const rows = [row(11, 'gone'), row(12, 'stale'), row(13, 'merge', { target: 'I-014' }), row(14, 'still', { steps: '打开', observed: '报错' }), row(18, 'unclear')];
  const html = M.resultsHtml({ state: 'ready', rows });
  const order = [...html.matchAll(/data-kind="(\w+)"/g)].map((m) => m[1]);
  assert.deepEqual(order, ['gone', 'stale', 'merge', 'still', 'unclear']);
  assert.match(html, /并入 <button[^>]*data-id="I-014"[^>]*>#014<\/button>/);
  assert.equal((html.match(/type="checkbox"/g) || []).length, 3);
  for (const r of rows) assert.ok(html.includes(r.detail) && html.includes('AI 做了：重跑了测试'));
  assert.match(html, /复现步骤：打开；实际结果：报错/);
  assert.equal(M.pickable({ rows }).length, 3);
});

test('a conclusion voided because the issue changed during the review is its own group and cannot be ticked', () => {
  const rows = [row(11, 'gone'), row(12, 'recheck', { detail: '又有了新的回复，需要重新复查' })];
  const html = M.resultsHtml({ state: 'ready', rows });
  assert.deepEqual([...html.matchAll(/data-kind="(\w+)"/g)].map((m) => m[1]), ['gone', 'recheck']);
  assert.match(html, /要重查 · 1 条/);
  assert.equal((html.match(/type="checkbox"/g) || []).length, 1);
  assert.deepEqual(M.pickable({ rows }).map((r) => r.kind), ['gone']);
});

test('用例 6: only "still" means one group and nothing to confirm', () => {
  const rows = [row(1, 'still', { steps: 'a', observed: 'b' }), row(2, 'still', { steps: 'a', observed: 'b' }), row(3, 'still', { steps: 'a', observed: 'b' })];
  const html = M.resultsHtml({ state: 'ready', rows });
  assert.deepEqual([...html.matchAll(/data-kind="(\w+)"/g)].map((m) => m[1]), ['still']);
  assert.doesNotMatch(html, /checkbox/);
  assert.equal(M.pickable({ rows }).length, 0);
});

test('用例 7: several merged into one original, the original itself is not in the merge group', () => {
  const rows = [row(8, 'still', { steps: 'a', observed: 'b' }), row(11, 'merge', { target: 'I-008' }), row(14, 'merge', { target: 'I-008' })];
  const html = M.resultsHtml({ state: 'ready', rows });
  const merge = html.slice(html.indexOf('data-kind="merge"'), html.indexOf('data-kind="still"'));
  assert.match(merge, /#011/);
  assert.match(merge, /#014/);
  assert.equal((merge.match(/并入/g) || []).length, 2);
  assert.doesNotMatch(merge, /#008 问题 8/);
});

test('dynamic text is escaped', () => {
  const html = M.resultsHtml({ state: 'ready', rows: [row(1, 'gone', { title: '<img src=x>', detail: '<b>' })] });
  assert.doesNotMatch(html, /<img src=x>|<b>/);
});

test('after confirming: how many were handled and which changed since the review', () => {
  assert.equal(M.appliedNote({ applied: ['I-012', 'I-017'], skipped: [] }), '本次已处理 2 条，原记录可在对应状态查看。');
  assert.match(M.appliedNote({ applied: ['I-012'], skipped: [{ reason: '#015 在复查之后变过，没有处理，需要重新复查。' }] }), /#015 在复查之后变过/);
});

// ---------- 主进程 ----------

function loadMain(statusFor) {
  const calls = [], sent = [];
  const st = { project: '/p', runs: new Map() };
  const mocks = {
    './backend': {
      rf: (args, root, _agent, _timeout, input) => {
        calls.push({ args: args.join(' '), root: root || st.project, input });
        if (args[1] === 'list') return Promise.resolve({ ok: true, stdout: JSON.stringify({ issues: [], features: [] }), stderr: '' });
        if (args[1] === 'review-status') return Promise.resolve({ ok: true, stdout: JSON.stringify(statusFor(root || st.project)), stderr: '' });
        if (args[1] === 'review') return statusFor.start ? statusFor.start(root) : Promise.resolve({ ok: true, stdout: JSON.stringify(statusFor(root)), stderr: '' });
        if (args[1] === 'review-apply') return Promise.resolve({ ok: true, stdout: JSON.stringify({ applied: ['I-001'], skipped: [] }), stderr: '' });
        if (args[1] === 'review-discard') return Promise.resolve({ ok: true, stdout: '{"state":"idle"}', stderr: '' });
        return Promise.resolve({ ok: true, stdout: '{}', stderr: '' });
      },
      parse: (t, f) => { try { return JSON.parse(t); } catch (_) { return f; } }, lastLine: (x) => String(x).trim().split('\n').pop(),
    },
    './state': { state: st, send: (channel, payload) => sent.push([channel, JSON.parse(JSON.stringify(payload))]) },
    './requirements': { pushReqs() {} }, './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, issue() {} }, './sessions': { close() {}, type() {} },
  };
  const timers = [];
  const context = vm.createContext({ require: (id) => mocks[id] || require(id), module: { exports: {} },
    setTimeout: (fn) => { timers.push(fn); return timers.length; }, clearTimeout: () => {} });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'app', 'src', 'main', 'issues.js'), 'utf8'), context);
  return { issues: context.module.exports, calls, sent, st, timers };
}
const reviews = (sent) => sent.filter((s) => s[0] === 'issues:review').map((s) => s[1]);

test('a running review is polled and pushed until it ends, then the list is reread', async () => {
  let state = { state: 'running', id: 'RV-1', done: 1, items: [] };
  const app = loadMain(() => state);
  await app.issues.reviewStatus();
  assert.equal(reviews(app.sent).at(-1).done, 1);
  assert.equal(app.timers.length, 1);
  state = { state: 'ready', id: 'RV-1', rows: [] };
  await app.timers.shift()();
  assert.equal(reviews(app.sent).at(-1).state, 'ready');
  assert.equal(app.timers.length, 0);
  await new Promise((r) => setImmediate(r));
  assert.ok(app.calls.some((c) => c.args.startsWith('issue list')));
});

test('用例 2: nothing pending is passed back without pushing a review', async () => {
  const app = loadMain(() => ({ state: 'empty', message: '当前没有待处理问题。' }));
  const job = await app.issues.reviewIssues();
  assert.equal(job.message, '当前没有待处理问题。');
  assert.equal(reviews(app.sent).length, 0);
});

test('用例 16: switching projects while starting shows nothing from the old project', async () => {
  let release;
  const statusFor = (root) => (root === '/p' ? { state: 'running', id: 'RV-1', items: [] } : { state: 'idle' });
  statusFor.start = () => new Promise((r) => { release = r; });
  const app = loadMain(statusFor);
  const pending = app.issues.reviewIssues();
  app.st.project = '/q';
  release({ ok: true, stdout: JSON.stringify({ state: 'running', id: 'RV-1', items: [] }), stderr: '' });
  assert.deepEqual(JSON.parse(JSON.stringify(await pending)), { error: '项目已切换。' });
  assert.equal(reviews(app.sent).filter((s) => s.id === 'RV-1').length, 0);
  await app.issues.pushIssues();
  await new Promise((r) => setImmediate(r));
  assert.equal(reviews(app.sent).at(-1).state, 'idle');
  assert.ok(app.calls.some((c) => c.args.startsWith('issue review-status') && c.root === '/q'));
});

test('confirming sends the review and the ticked issues, then the review is gone', async () => {
  let state = { state: 'ready', id: 'RV-1', rows: [] };
  const app = loadMain(() => state);
  await app.issues.pushIssues();
  await app.issues.reviewStatus();
  state = { state: 'idle' }; // 确认以后任务文件没了
  const done = await app.issues.applyIssueReview({ review: 'RV-1', selected: ['I-001'] });
  assert.deepEqual(JSON.parse(JSON.stringify(done)), { applied: ['I-001'], skipped: [] });
  const sentApply = app.calls.find((c) => c.args.startsWith('issue review-apply'));
  assert.deepEqual(JSON.parse(sentApply.input), { review: 'RV-1', selected: ['I-001'] });
  assert.equal(reviews(app.sent).at(-1).state, 'idle');
  assert.deepEqual(JSON.parse(JSON.stringify(await app.issues.applyIssueReview({ review: 'RV-old', selected: ['I-001'] }))), { error: '这次复查的结果已经不在了，请重新刷新。' });
});

test('the refresh button stays visible on other tabs while a review is running or has results', () => {
  const css = fs.readFileSync(path.join(__dirname, '..', 'app', 'src', 'renderer', 'styles', 'issues.css'), 'utf8');
  assert.match(css, /#issues:not\(\[data-review="running"\]\):not\(\[data-review="ready"\]\)/);
});
