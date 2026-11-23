// 知识保鲜在应用这一侧（app/src/main/fresh.js、renderer/scripts/fresh-model.js）：大图要的数据怎么合起来、核对会话怎么开、
// 建议对话框怎么写（动态内容都转义，处理完才能核对完）
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const main = path.join(__dirname, '..', 'app', 'src', 'main');
const M = require(path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts', 'fresh-model.js'));
const plain = (x) => JSON.parse(JSON.stringify(x));

function load(answers) {
  const calls = [], sent = [], opened = [];
  const state = { project: '/p', runs: new Map() };
  const mocks = {
    './backend': { rf: async (args) => { calls.push(args.join(' ')); return answers[args.slice(0, 2).join(' ')] || { ok: true, stdout: 'null' }; },
      parse: (t, f) => { try { return JSON.parse(t); } catch (_) { return f; } }, lastLine: (x) => String(x).trim().split('\n').pop() },
    './state': { state, send: (c, p) => sent.push([c, p]) },
    './sessions': { open: async (o) => { opened.push(o); state.runs.set(o.id, {}); } },
  };
  const ctx = vm.createContext({ require: (id) => (Object.hasOwn(mocks, id) ? mocks[id] : require(id)), module: { exports: {} }, console });
  vm.runInContext(fs.readFileSync(path.join(main, 'fresh.js'), 'utf8'), ctx);
  return { F: ctx.module.exports, calls, sent, opened, state };
}

test('the map gets stale features, their commits and the suggestions waiting, in one object', async () => {
  const drift = [{ id: 'RF-101', stale: true, unmapped: false, commits: [{ short: 'abc1234', subject: '改了加法', owner: null, hash: 'x', files: ['calc.py'] }] },
    { id: 'RF-102', stale: false, unmapped: true, commits: [] }];
  const show = [{ feature: 'RF-101', verdict: 'changed', items: [{ k: 1, state: 'pending' }, { k: 2, state: 'adopted' }] }];
  const h = load({ 'fresh drift': { ok: true, stdout: JSON.stringify(drift) }, 'fresh show': { ok: true, stdout: JSON.stringify(show) } });
  const out = plain(await h.F.overview());
  assert.deepEqual(out['RF-101'], { stale: true, unmapped: false, checking: false, commits: [{ short: 'abc1234', subject: '改了加法', owner: null }], more: 0,
    pending: 1, verdict: 'changed', proposed: true });
  assert.equal(out['RF-102'].unmapped, true);
});

test('a check opens one background read-only session per feature, only for feature ids', async () => {
  const h = load({});
  assert.ok((await h.F.check('../x')).error);
  assert.deepEqual(plain(await h.F.check('RF-101')), { ok: true });
  assert.deepEqual(plain(await h.F.check('RF-101')), { running: true }); // 已经在核对：不再开一个
  assert.deepEqual(plain(h.opened), [{ id: 'check:RF-101', headless: true }]);
  await h.F.adopt('RF-101', [1, 'x', -2, 3]);
  assert.equal(h.calls.at(-1), 'fresh adopt RF-101 1 3 --json'); // 只传正整数的序号
  h.F.settled('check:RF-101', 'reply');
  h.F.settled('R-1', 'reply');
  assert.deepEqual(plain(h.sent.filter(([c]) => c === 'fresh:changed').map(([, p]) => p.id)), ['RF-101', 'RF-101', 'RF-101']);
});

test('the dialog escapes what the session wrote and only lets you finish when every suggestion is handled', () => {
  const data = { verdict: 'changed', summary: '加法<b>改了</b>', items: [
    { k: 1, section: '规则与边界', op: 'replace', old: '整数', new: '两位<小数>', why: '提交 abc', evidence: 'calc.py', state: 'pending' },
    { k: 2, section: '调整时要注意', op: 'add', old: '', new: '影响导出', why: '推测', evidence: '', state: 'ignored' }] };
  const html = M.html('RF-101', data, '');
  assert.match(html, /加法&lt;b&gt;改了&lt;\/b&gt;/);
  assert.match(html, /两位&lt;小数&gt;/);
  assert.match(html, /data-fr="adopt" data-k="1"/);
  assert.doesNotMatch(html, /data-k="2"/); // 处理过的不再有按钮
  assert.match(html, /data-fr="done" disabled/);
  assert.doesNotMatch(M.html('RF-101', { ...data, items: [{ ...data.items[0], state: 'adopted' }] }, ''), /data-fr="done" disabled/);
  assert.match(M.html('RF-101', null, ''), /还没有核对结果/);
});
