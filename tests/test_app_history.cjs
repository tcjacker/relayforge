// 历史（应用这一侧）：「总结」块显示什么、rf.py history 在每种会话里都放行（summarize、archive 不放行）、
// 各种会话的开场白都提到 history、主进程怎么在后台总结和补存会话记录
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..', 'app', 'src');
const main = path.join(root, 'main');
const Model = require(path.join(root, 'renderer', 'scripts', 'summary-model.js'));

const done = { id: 'R-20261118-ab12', state: 'done', summary: ['做了按月导出账单。', '文件名用中文月份。'], topics: ['账单导出', '踩坑：月份'], summarized: '2026-11-18 10:20',
  tags: ['RF-101', 'M-01', 'D-0003'], session_tags: ['S-20261118-aaaa', 'S-20261118-bbbb'], archived: ['S-20261118-aaaa'], archive_here: ['S-20261118-aaaa'],
  archive_missing: ['S-20261118-bbbb'], pending_decisions: ['D-0004'] };

test('a summarized requirement shows its sentences, topics, tags, where its sessions are kept and what waits for the user', () => {
  const s = Model.summary(done, {});
  assert.equal(s.status, '');
  assert.equal(s.canRedo, true);
  assert.equal(s.records, '会话记录已存到本机（1 个）；原始记录已不在：S-20261118-bbbb');
  const html = Model.html(done, {});
  assert.match(html, /<b>总结<\/b>/);
  assert.match(html, /<span>账单导出<\/span><span>踩坑：月份<\/span>/);
  assert.match(html, /<li>文件名用中文月份。<\/li>/);
  assert.match(html, /涉及：RF-101、M-01、D-0003/);
  assert.match(html, /有 1 条聊到了但没记下的决定，等你确认（D-0004）/);
  assert.match(html, /data-summarize="R-20261118-ab12"[^>]*>重新总结/);
});

test('not summarized yet, running, queued or failed', () => {
  const bare = { ...done, summary: [], topics: [], summarized: '', pending_decisions: [] };
  assert.equal(Model.summary(bare, {}).status, '还没总结');
  assert.match(Model.html(bare, {}), /还没总结.*重新总结/);
  const running = Model.summary(bare, { running: [done.id] });
  assert.equal(running.status, '正在总结…');
  assert.equal(running.canRedo, false);
  assert.doesNotMatch(Model.html(bare, { running: [done.id] }), /重新总结/);
  assert.equal(Model.summary(bare, { queued: [done.id] }).status, '排队等总结');
  assert.match(Model.html(bare, { failed: { [done.id]: '没装 claude' } }), /上次没总结出来：没装 claude/);
  assert.equal(Model.summary({ ...bare, archive_here: [], archived: [], archive_missing: [] }, {}).records, '会话记录还没存到本机');
  assert.match(Model.summary({ ...done, archive_here: [], archive_missing: [] }, {}).records, /1 个存在别的电脑上/);
});

test('only finished or cancelled requirements; a cancelled one shows only where its sessions are; text is escaped', () => {
  assert.equal(Model.summary({ ...done, state: 'open' }, {}), null);
  assert.equal(Model.html(null, {}), '');
  const dropped = Model.html({ ...done, state: 'dropped' }, {});
  assert.doesNotMatch(dropped, /总结|重新总结|等你确认/);
  assert.match(dropped, /会话记录已存到本机/);
  assert.match(Model.html({ ...done, summary: ['<img onerror=x>'] }, {}), /&lt;img onerror=x&gt;/);
  assert.doesNotMatch(Model.html({ ...done, topics: ['<script>'] }, {}), /<script>/);
});

test('rf.py history is allowed without asking in every kind of session; summarize and archive are not', () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-hi-'));
  require(path.join(main, 'state')).state.project = project;
  const { hooks } = require(path.join(main, 'claude-settings'));
  const { BUNDLED } = require(path.join(main, 'config'));
  const kinds = { 'chat, development or fix': hooks(null, false), 'requirement intake': hooks('/drafts/R-1', false), 'investigation': hooks('/drafts/probe-I-1', true), 'merge chat': hooks(null, false, true) };
  for (const [name, text] of Object.entries(kinds)) {
    const { allow } = JSON.parse(text).permissions;
    for (const a of ['search', 'show', 'session']) for (const rf of [BUNDLED, '.relayforge/bin/rf.py']) assert.ok(allow.includes(`Bash(python3 ${rf} history ${a}:*)`), `${name} ${a} ${rf}`);
    assert.ok(!allow.some((x) => /summarize|archive/.test(x)), name);
  }
});

test('every kind of session is told to look things up with rf.py history', () => {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-hip-'));
  const P = require(path.join(main, 'prompts'));
  const req = { id: 'R-1', title: '导出', file: '.relayforge/requirements/R-1.md', state: 'open' };
  const issue = { id: 'I-001', num: '#001', title: 'x', thread: [] };
  const texts = {
    develop: P.develop(req, 'S-1'), following: P.following(req, 'S-1'), intake: P.intake(req, 'S-1', [], false, '/d'), map: P.mapEdit(),
    fixing: P.fixing({ id: 'F-001', branch: 'fix/f-001' }, [issue], '', false), probe: P.investigating(issue, '', '/d', false),
    merging: P.mergingRemote({ remote: 'origin', branch: 'main', current: 'main', behind: 1, ahead: 1 }),
  };
  for (const [name, text] of Object.entries(texts)) assert.match(text, /rf\.py history：history search/, name);
  assert.match(P.HISTORY_GUIDE, /history show 编号/);
  assert.match(P.HISTORY_GUIDE, /history session 会话编号/);
});

function loadSummary(answers) {
  const calls = [], sent = [];
  const project = { current: '/p/a' };
  const mocks = {
    './backend': { uiText: require('../app/src/main/locale').uiText, rf: async (args, where) => { calls.push([where, ...args].join(' ')); return (answers[args[0]] || []).shift() || { ok: true, stdout: '{}', stderr: '' }; },
      parse: (t, f) => { try { return JSON.parse(t); } catch (_) { return f; } }, lastLine: (x) => String(x).trim().split('\n').pop() },
    './state': { send: (channel, payload) => sent.push([channel, JSON.parse(JSON.stringify(payload))]), get state() { return { project: project.current }; } },
    './requirements': { pushReqs: () => sent.push(['reqs']) },
  };
  const context = vm.createContext({ require: (id) => mocks[id] || require(id), module: { exports: {} }, setTimeout, clearTimeout });
  vm.runInContext(fs.readFileSync(path.join(main, 'summary.js'), 'utf8'), context);
  return { summary: context.module.exports, calls, sent, project };
}
const tick = () => new Promise((r) => setImmediate(r));

test('summaries run one at a time in the background; a failure is kept for the panel and can be retried', async () => {
  const { summary, calls, sent } = loadSummary({ summarize: [
    { ok: true, stdout: JSON.stringify({ id: 'R-a', proposed: ['D-0004'] }), stderr: '' },
    { ok: false, stdout: '', stderr: 'x\n没总结出来：没有 claude', timedOut: false },
  ] });
  summary.start('R-a');
  const queued = summary.start('R-b');
  assert.deepEqual([...queued.running], ['R-a']);
  assert.deepEqual([...queued.queued], ['R-b']);
  assert.deepEqual([...summary.start('R-a').running], ['R-a']); // already running: not twice
  for (let i = 0; i < 6; i++) await tick();
  assert.deepEqual(calls, ['/p/a summarize R-a --json', '/p/a summarize R-b --json']);
  assert.ok(sent.some(([c, p]) => c === 'notice' && /1 条聊到了但没记下的决定/.test(p)));
  assert.equal(summary.view().failed['R-b'], '没总结出来：没有 claude');
  assert.ok(sent.filter(([c]) => c === 'reqs').length >= 2);
  summary.start('R-b');
  assert.equal(summary.view().failed['R-b'], undefined);
  assert.match(summary.start('; rm -rf /').error, /需求编号/);
});

test('the archive sweep runs once per project when it is opened, and refreshes the list when it saved something', async () => {
  const { summary, calls, sent } = loadSummary({ archive: [{ ok: true, stdout: JSON.stringify({ items: [{ id: 'R-a' }], changed: true }), stderr: '' }] });
  await summary.sweep('/p/a');
  await summary.sweep('/p/a');
  assert.deepEqual(calls, ['/p/a archive sweep --json']);
  assert.ok(sent.some(([c]) => c === 'reqs'));
});
