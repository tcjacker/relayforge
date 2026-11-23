// 合并排队：同一时间只合并一个需求；有冲突交给 AI，它这一轮说完自动再试；轮到下一个自动开始
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const os = require('node:os');
const { readTurn } = require('../app/src/main/codex-turns');
const file = path.join(__dirname, '..', 'app', 'src', 'main', 'merge.js');

function temp(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-merge-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function load(t, answers, home = temp(t)) {
  const calls = [], sent = [], closed = [], summarized = [], opened = [], posted = [];
  const project = { project: '/project', branch: 'dev', runs: new Map() };
  const mocks = {
    './summary': { start: (id) => summarized.push(id) }, // 确认完成以后在后台写总结
    './backend': { exec: async () => ({ stdout: project.branch + '\n' }), rf: async (args) => { calls.push(args.join(' ')); const next = answers[args[2]].shift(); return next; }, parse: (t, f) => { try { return JSON.parse(t); } catch (_) { return f; } }, lastLine: (x) => x.trim().split('\n').pop() },
    './config': { RF_HOME: home, BUNDLED: '/rf/rf.py' },
    './state': { state: project, send: (channel, payload) => sent.push([channel, payload]) },
    './requirements': { pushReqs() {} },
    './issues': { pushIssues() {} },
    './services': { stopScope: async () => {} }, // 合并前停掉从工作目录启动的验收服务
    './sessions': { open: async (o) => opened.push(o) }, // 会话没开着：合并前的同步在后台无界面跑一轮
    './inbox': { post: (m) => posted.push(m) }, // 会话开着：经收件箱
  };
  const context = vm.createContext({ require: (id) => mocks[id] || require(id), module: { exports: {} }, setTimeout, clearTimeout });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
  return { merge: context.module.exports, home, project, calls, sent, closed, summarized, opened, posted, close: (id) => { closed.push(id); return true; } };
}
const ok = (id) => ({ ok: true, stdout: JSON.stringify({ id, state: 'done' }), stderr: '' });
const conflict = { ok: false, stdout: JSON.stringify({ sync: true, conflict: true, base: 'dev', files: ['a.js'], loose: [], error: '合并会冲突' }), stderr: '合并会冲突' };
const behind = { ok: false, stdout: JSON.stringify({ sync: true, conflict: false, base: 'dev', files: [], loose: ['b.js'], error: '还没包含 dev 的最新内容' }), stderr: '' };
const tick = () => new Promise((r) => setImmediate(r));
const last = (sent, channel) => JSON.parse(JSON.stringify(sent.filter((x) => x[0] === channel).pop()[1])); // 换成本测试里的数组，好用 deepEqual

test('another requirement can merge while the AI resolves a conflict and runs tests', async (t) => {
  let finishA;
  const pendingA = new Promise((resolve) => { finishA = resolve; });
  const env = load(t, { 'R-a': [pendingA, conflict, ok('R-a')], 'R-b': [ok('R-b')] });
  const { merge, calls, sent, closed, close, summarized } = env;
  const first = merge.enqueue('R-a', close);
  assert.equal((await merge.enqueue('R-b', close)).merging.phase, 'waiting');
  finishA(conflict);
  assert.equal((await first).merging.phase, 'syncing');
  await tick();
  const resolve = last(sent, 'merge:resolve');
  assert.equal(resolve.id, 'R-a');
  assert.match(resolve.prompt, /git merge dev/);
  assert.match(resolve.prompt, /a\.js/);
  assert.match(resolve.prompt, /python3 \/rf\/rf\.py test run --req R-a/); // 有冲突：只测受影响的，不跑全量
  assert.match(resolve.prompt, /不要跑整个项目的测试/);
  // 会话没开着：同一段话在后台无界面跑一轮交给 AI（headless.js），不用界面去开终端
  assert.equal(resolve.headless, true);
  const run = env.opened.find((o) => o.id === 'R-a');
  assert.ok(run && run.headless && run.say === resolve.prompt);
  assert.deepEqual([...closed], ['R-b']);
  assert.deepEqual(last(sent, 'merge').map((x) => [x.id, x.phase]), [['R-a', 'syncing']]);
  merge.settled('R-b'); // not the one syncing: nothing happens
  assert.equal(calls.length, 2);
  merge.settled('R-a'); await tick(); await tick();
  assert.equal(sent.filter((x) => x[0] === 'merge:resolve').length, 2);
  assert.match(last(sent, 'merge:resolve').prompt, /上轮结束后，程序检查发现同步还没完成/);
  merge.settled('R-a'); for (let i = 0; i < 6; i++) await tick();
  assert.deepEqual(sent.filter((x) => x[0] === 'merge:finished').map((x) => x[1].id), ['R-b', 'R-a']);
  assert.deepEqual([...closed], ['R-b', 'R-a']);
  assert.deepEqual(last(sent, 'merge'), []);
  assert.deepEqual(calls, ['req done R-a --json', 'req done R-b --json', 'req done R-a --json', 'req done R-a --json']);
  assert.deepEqual([...summarized], ['R-b', 'R-a']); // 合并好了才在后台总结，失败、排队时不总结
});

test('a failed first attempt and an aborted preparation both leave the queue', async (t) => {
  const loose = { ok: false, stdout: '', stderr: '还有 1 个文件的改动没有提交' };
  const { merge, sent, close } = load(t, { 'R-a': [loose, conflict], 'R-b': [ok('R-b')] });
  assert.match((await merge.enqueue('R-a', close)).error, /没有提交/);
  assert.deepEqual(last(sent, 'merge'), []);
  await merge.enqueue('R-a', close);
  merge.abort('R-a');
  merge.settled('R-a');
  assert.deepEqual(last(sent, 'merge'), []);
  await merge.enqueue('R-b', close);
  assert.deepEqual(sent.filter((x) => x[0] === 'merge:finished').map((x) => x[1].id), ['R-b']);
});

test('a repair that finishes testing rejoins behind a merge already in progress', async (t) => {
  let finishB;
  const pendingB = new Promise((resolve) => { finishB = resolve; });
  const fixed = { ok: true, stdout: JSON.stringify({ id: 'I-001', state: 'resolved' }), stderr: '' };
  const { merge, calls, sent, close } = load(t, { 'I-001': [behind, fixed], 'R-b': [pendingB], 'R-c': [ok('R-c')] });
  await merge.enqueue('fix:F-001', close, { issue: 'I-001', kind: 'fix' });
  const b = merge.enqueue('R-b', close);
  const c = await merge.enqueue('R-c', close);
  assert.deepEqual([c.merging.phase, c.merging.ahead, c.merging.holder], ['waiting', 1, 'R-b']);
  merge.settled('fix:F-001');
  assert.deepEqual(last(sent, 'merge').map((x) => [x.id, x.phase, x.ahead]), [
    ['R-b', 'merging', 0], ['R-c', 'waiting', 1], ['fix:F-001', 'waiting', 2],
  ]);
  await tick();
  assert.deepEqual(calls, ['issue confirm I-001 --json', 'req done R-b --json']);
  finishB(ok('R-b'));
  await b;
  await tick();
  assert.deepEqual(calls, ['issue confirm I-001 --json', 'req done R-b --json', 'req done R-c --json', 'issue confirm I-001 --json']);
  assert.equal(last(sent, 'issues:merged').id, 'fix:F-001');
  assert.deepEqual(last(sent, 'merge'), []);
});

test('uncommitted work goes back to its session even without a conflict, with no tests to run; a later failure waits for you', async (t) => {
  const clobber = { ok: false, stdout: '', stderr: '项目目录里这些文件还没提交进 Git' };
  const { merge, sent, close } = load(t, { 'R-a': [behind, clobber, ok('R-a')] });
  const first = await merge.enqueue('R-a', close);
  assert.equal(first.merging.phase, 'syncing');
  const resolve = last(sent, 'merge:resolve');
  assert.match(resolve.prompt, /先把还没提交的改动提交（b\.js）/);
  assert.match(resolve.prompt, /git merge dev。/);
  assert.doesNotMatch(resolve.prompt, /冲突（/);
  assert.match(resolve.prompt, /没有冲突，合完提交就行，不用跑测试/);
  assert.doesNotMatch(resolve.prompt, /test run/);
  merge.settled('R-a'); for (let i = 0; i < 3; i++) await tick();
  const stuck = last(sent, 'merge')[0];
  assert.deepEqual([stuck.phase, stuck.note], ['stuck', '项目目录里这些文件还没提交进 Git']);
  merge.settled('R-a'); await tick();  // stuck is not retried by the AI stopping, only by you
  assert.equal(sent.filter((x) => x[0] === 'merge:finished').length, 0);
  await merge.retry('R-a');
  assert.deepEqual(sent.filter((x) => x[0] === 'merge:finished').map((x) => x[1].id), ['R-a']);
});

test('a fix waits in the same queue and, when behind, goes back to its fix session with its own words', async (t) => {
  const { merge, calls, sent, close } = load(t, { 'I-001': [behind] });
  const first = await merge.enqueue('fix:F-001', close, { issue: 'I-001', kind: 'fix' });
  assert.equal(calls[0], 'issue confirm I-001 --json');
  assert.equal(first.merging.phase, 'syncing');
  const told = last(sent, 'issues:resolve');
  assert.equal(told.id, 'fix:F-001');
  assert.match(told.prompt, /这个修复自己的工作目录/);
  assert.match(told.prompt, /不要运行 rf\.py issue confirm/);
  assert.equal(sent.filter((x) => x[0] === 'merge:resolve').length, 0);
});

const unfinished = { ok: false, stdout: JSON.stringify({
  sync: true, merging: true, base: 'dev', files: [], loose: [],
  error: '工作目录里有一笔未完成的合并：冲突已处理，合并还没有提交。',
}), stderr: '' };

test('a Codex completion triggers a Git recheck and hands an uncommitted merge back to AI', async (t) => {
  const fixed = { ok: true, stdout: JSON.stringify({ id: 'I-001', state: 'resolved' }), stderr: '' };
  const { merge, sent, close, home } = load(t, { 'I-001': [conflict, unfinished, fixed] });
  await merge.enqueue('fix:F-001', close, { issue: 'I-001', kind: 'fix' });
  const run = { transcript: path.join(home, 'rollout.jsonl'), seen: 0 };
  fs.writeFileSync(run.transcript, '');
  function append(type, split = false) {
    const event = JSON.stringify({ type: 'event_msg', payload: { type } });
    fs.appendFileSync(run.transcript, event + (split ? '' : '\n'));
    return read();
  }
  function read() {
    const size = fs.statSync(run.transcript).size;
    const signal = readTurn(run, size);
    run.seen = size;
    return signal;
  }
  assert.equal(append('task_complete'), null); // prior turn without a matching start
  assert.equal(append('task_started'), 'running');
  assert.equal(append('task_complete', true), null);
  fs.appendFileSync(run.transcript, '\n');
  merge.settled('fix:F-001', read());
  await tick();
  const next = last(sent, 'issues:resolve').prompt;
  assert.match(next, /已经在合并中.*完成这次合并提交/);
  assert.doesNotMatch(next, /然后运行 git merge/);
  assert.match(next, /test run --fix F-001（这个修复的测试集/); // 修复按修复的范围测
  assert.equal(append('task_started'), 'running');
  merge.settled('fix:F-001', append('task_complete'));
  await tick();
  assert.equal(last(sent, 'issues:merged').issue.state, 'resolved');
});

test('approvals pause a handoff and repeated incomplete turns end in an actionable state', async (t) => {
  const { merge, calls, sent, close } = load(t, { 'R-a': [unfinished, unfinished, unfinished, unfinished] });
  await merge.enqueue('R-a', close);
  merge.settled('R-a', 'approve');
  assert.match(last(sent, 'merge')[0].note, /等你批准/);
  assert.equal(calls.length, 1);
  merge.settled('R-a', 'running');
  assert.equal(last(sent, 'merge')[0].note, '');
  for (let turn = 0; turn < 3; turn++) {
    merge.settled('R-a', 'reply');
    await tick();
  }
  assert.equal(sent.filter((event) => event[0] === 'merge:resolve').length, 3);
  assert.equal(last(sent, 'merge')[0].phase, 'stuck');
  assert.match(last(sent, 'merge')[0].note, /合并还没有提交/);
});

test('a restart rechecks confirmed tasks on the original target and does not restore aborted tasks', async (t) => {
  const initial = load(t, { 'R-a': [unfinished], 'R-b': [behind] });
  await initial.merge.enqueue('R-a', initial.close);
  await initial.merge.enqueue('R-b', initial.close);
  initial.merge.abort('R-b');
  const resumed = load(t, { 'R-a': [ok('R-a')] }, initial.home);
  resumed.project.branch = 'other';
  assert.equal(resumed.merge.list(resumed.close), 1);
  await tick();
  assert.equal(resumed.calls.length, 0);
  assert.match(last(resumed.sent, 'merge')[0].note, /切回该分支/);
  resumed.project.branch = 'dev';
  await resumed.merge.retry('R-a');
  assert.deepEqual(resumed.calls, ['req done R-a --json']);
  assert.deepEqual(last(resumed.sent, 'merge'), []);
});

test('a restarted app completes the confirmed merge when Git is already ready', async (t) => {
  const before = load(t, { 'R-a': [unfinished] });
  await before.merge.enqueue('R-a', before.close);
  const after = load(t, { 'R-a': [ok('R-a')] }, before.home);
  after.merge.list(after.close);
  await tick();
  assert.deepEqual(after.closed, ['R-a']);
  assert.equal(last(after.sent, 'merge:finished').state, 'done');
  assert.deepEqual(last(after.sent, 'merge'), []);
});

test('switching away and back rechecks the interrupted handoff in its original project', async (t) => {
  const { merge, close, sent, project, calls } = load(t, { 'R-a': [unfinished, ok('R-a')] });
  await merge.enqueue('R-a', close);
  merge.pause(project.project);
  project.project = '/other';
  assert.equal(merge.list(close), 0);
  project.project = '/project';
  assert.equal(merge.list(close), 1);
  await tick();
  assert.deepEqual(calls, ['req done R-a --json', 'req done R-a --json']);
  assert.equal(last(sent, 'merge:finished').state, 'done');
});

test('when AI needs a decision, the user answer restarts the same handoff', async (t) => {
  const { merge, calls, close, sent } = load(t, { 'R-a': [behind, ok('R-a')] });
  await merge.enqueue('R-a', close);
  merge.settled('R-a', 'answer');
  assert.equal(last(sent, 'merge')[0].phase, 'stuck');
  assert.match(last(sent, 'merge')[0].note, /需要你的答复/);
  assert.equal(calls.length, 1);
  merge.settled('R-a', 'running');
  assert.equal(last(sent, 'merge')[0].phase, 'syncing');
  merge.settled('R-a');
  await tick();
  assert.equal(last(sent, 'merge:finished').state, 'done');
});

// 合并闸门（backend/relayforge/testsets/gate.py）：合并后的代码上回归没过，交回会话修；它说完一轮再检查；
// 准备步骤没经你同意就停住；交回几次还不过停住，你可以「跳过检查，仍然合并」（--skip-gate）
test('a failed pre-merge check goes back to the session with the failing sets; a skip merges with --skip-gate', async (t) => {
  const failing = { ok: false, stdout: JSON.stringify({ error: '合并后的代码没过合并前的检查：TS-002（汇总）。', gate: { ok: false, blocked: [], failing: [
    { id: 'TS-002', title: '汇总', kind: 'depends', result: 'fail', cases: [3], names: ['两个数相加'] }] } }), stderr: '' };
  const env = load(t, { 'R-a': [failing, failing, failing, failing, ok('R-a')] });
  const { merge, calls, sent } = env;
  assert.equal((await merge.enqueue('R-a', env.close)).merging.phase, 'syncing');
  const told = env.opened.find((o) => o.id === 'R-a');
  assert.ok(told && told.headless);
  assert.match(told.say, /合并后的代码/);
  assert.match(told.say, /TS-002「汇总」没过的第 3 条（两个数相加）；单独重跑：python3 \/rf\/rf\.py test run TS-002 --case 3/);
  assert.match(told.say, /不要为了通过去改期望/);
  assert.equal(last(sent, 'merge').find((x) => x.id === 'R-a').gate.failing[0].id, 'TS-002');
  for (let i = 0; i < 3; i++) { merge.settled('R-a', 'reply', '修好了'); await tick(); }
  const stuck = last(sent, 'merge').find((x) => x.id === 'R-a');
  assert.equal(stuck.phase, 'stuck'); // 交回几次还是没过：停住等你
  assert.match(stuck.note, /跳过检查仍然合并/);
  const done = await merge.retry('R-a', true, true);
  assert.equal(done.id, 'R-a');
  assert.equal(calls.at(-1), 'req done R-a --json --skip-gate');
});

test('a pre-merge check that needs your consent first stops without handing anything to the session', async (t) => {
  const blocked = { ok: false, stdout: JSON.stringify({ error: '合并前的检查没法开始：测试环境的准备步骤 P-1 还没经你同意。', gate: { ok: false, blocked: ['测试环境的准备步骤 P-1 还没经你同意'], failing: [] } }), stderr: '' };
  const env = load(t, { 'R-a': [blocked] });
  const out = await env.merge.enqueue('R-a', env.close);
  assert.equal(out.merging.phase, 'stuck');
  assert.match(out.merging.note, /还没经你同意/);
  assert.equal(env.opened.length, 0);
});
