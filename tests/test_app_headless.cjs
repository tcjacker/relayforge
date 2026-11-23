// 无界面运行的会话（app/src/main/headless.js）和它需要你时的处理（app/src/main/hitl.js、renderer/scripts/hitl-model.js）：
// 输出整理成可读的过程；这一轮做完按「说完了 / 在问你」接上原来的流程，钩子报过的不再报；要你批准的请求从文件读出来、写回你的决定。
const test = require('node:test');
const assert = require('node:assert/strict');
const locale = require('../app/src/main/locale');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const step = () => new Promise(setImmediate);
const main = path.join(__dirname, '..', 'app', 'src', 'main');
const plain = (x) => JSON.parse(JSON.stringify(x));

function harness(t) {
  const home = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-headless-')));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const sent = [], progress = [], phases = [], bound = [], notes = [], opened = [], after = [], spawned = [], killed = [], threads = [];
  const state = { project: '/project', runs: new Map(), win: null };
  let now = 0;
  class Clock extends Date { static now() { return now; } }
  const attention = { mergeProgress(id, kind, text) { phases.push([id, kind]); progress.push([id, kind, text]); }, setPhase: (id, p) => phases.push([id, p]), stopped() {}, readEvents() {} };
  const mocks = {
    './config': { RF_HOME: home },
    '../../package.json': require('../app/package.json'),
    './state': { state, send: (channel, payload) => sent.push([channel, payload]) },
    './attention': attention,
    './session-token': { bind: (sig, pid) => bound.push(['bind', sig, pid]), unbind: (sig) => bound.push(['unbind', sig]) },
    './monitor': { ended() {} },
    './codex-trust': { trusted: () => true },
    './codex-sessions': { bindThread: (id, thread) => threads.push([id, thread]) },
    './proc-tree': { killTree: (pid) => killed.push(pid) },
    './sessions': { open: async (o) => opened.push(o), stop: (run) => {
      state.runs.delete(run.id);
      if (run.stopHeadless) return run.stopHeadless();
      run.term.killed = true;
    } },
    electron: { Notification: Object.assign(function (o) { notes.push(o); this.on = () => {}; this.show = () => {}; }, { isSupported: () => true }) },
    child_process: { spawn: (file, args, opts) => {
      const c = new EventEmitter();
      c.stdout = new PassThrough();
      c.stderr = new PassThrough();
      c.stdin = new PassThrough();
      c.sent = [];
      c.stdin.on('data', (data) => c.sent.push(JSON.parse(data)));
      c.pid = 4242;
      spawned.push({ file, args, opts, c });
      return c;
    } },
  };
  const req = (id) => id === './locale' ? locale : (Object.hasOwn(mocks, id) ? mocks[id] : require(id));
  const load = (name) => { const ctx = vm.createContext({ require: req, module: { exports: {} }, console, setTimeout, clearTimeout, Date: Clock }); vm.runInContext(fs.readFileSync(path.join(main, name), 'utf8'), ctx); return ctx.module.exports; };
  const stuck = mocks['./stuck'] = load('stuck.js');
  const inbox = load('inbox.js');
  mocks['./inbox'] = { ...inbox, afterHeadless: (id) => { after.push(id); return inbox.afterHeadless(id); } };
  mocks['./codex-app-server'] = load('codex-app-server.js');
  const hitl = load('hitl.js');
  mocks['./hitl'] = hitl;
  const headless = load('headless.js');
  const at = (time) => { now = time; };
  const tick = (run, time) => { at(time); stuck.watchHeadless(run, hitl.tick().some((a) => a.run === run.id)); };
  return { home, state, sent, progress, phases, bound, notes, opened, after, spawned, killed, threads, hitl, inbox, headless, stuck, at, tick };
}

function begin(h, agent = 'claude', id = 'fix:F-1', options) {
  const run = { id, title: '问题 1 的修复', sessionId: 'sid-1', agent };
  h.state.runs.set(id, run);
  h.headless.start(run, '/bin/' + agent, ['-p'], { cwd: '/wt', env: { A: '1', RELAYFORGE_EVENTS: '/events' } }, 'SIG', options);
  const child = h.spawned[h.spawned.length - 1].c;
  const out = (events) => child.stdout.write(Buffer.from(events.map((e) => JSON.stringify(e)).join('\n') + '\n'));
  return { run, child, out };
}

test('a background run gets the approval environment, shows what it does as plain lines, and hands its turn on', (t) => {
  const h = harness(t);
  const { run, child, out } = begin(h);
  const env = h.spawned[0].opts.env;
  assert.equal(env.A, '1');
  assert.equal(env.RELAYFORGE_HEADLESS, '1');
  assert.ok(env.RELAYFORGE_APPROVALS.startsWith(path.join(h.home, 'approvals') + path.sep)); // 钩子据此把批准请求交给应用（core/approvals.py）
  assert.deepEqual(plain(h.spawned[0].opts.stdio), ['ignore', 'pipe', 'pipe']);
  assert.deepEqual(plain(h.bound[0]), ['bind', 'SIG', 4242]);
  out([{ type: 'system', subtype: 'init' },
    { type: 'assistant', message: { content: [{ type: 'text', text: '先看看失败的测试。' }, { type: 'tool_use', name: 'Bash', input: { command: 'npm test' } }] } },
    { type: 'user', message: { content: [{ type: 'tool_result', is_error: true, content: 'exit 1' }] } },
    { type: 'result', result: '修好了，测试都过了。', duration_ms: 4200 }]);
  assert.match(run.log, /先看看失败的测试。/);
  assert.match(run.log, /· Bash npm test/);
  assert.match(run.log, /✗ exit 1/);
  assert.match(run.log, /用时 4 秒/);
  assert.ok(h.sent.some(([c, p]) => c === 'term:data' && p.id === 'fix:F-1'));
  child.emit('exit', 0);
  assert.equal(h.state.runs.has('fix:F-1'), false);
  assert.deepEqual(plain(h.progress), [['fix:F-1', 'reply', '修好了，测试都过了。']]); // 钩子没报过：按会话停下来通知原来的流程
  assert.deepEqual(plain(h.after), ['fix:F-1']); // 排着的话再跑一轮交过去
  assert.deepEqual(plain(h.sent.find(([c]) => c === 'term:exit')[1]), { id: 'fix:F-1', code: 0, headless: true, kind: 'reply' });
  assert.deepEqual(plain(h.bound.pop()), ['unbind', 'SIG']);
});

async function codexTurn(h, resume, id = 'fix:F-1') {
  const codex = { threadId: resume ? 'native-1' : null, sandbox: 'workspace-write', model: 'gpt-example',
    approvalPolicy: 'on-request', prompt: '查看目录。\nRelayForge 会话标识：sid-1', images: resume ? [] : ['/wt/check.png'] };
  const { run, child, out } = begin(h, 'codex', id, { codex });
  const threadId = 'native-1';
  const turnId = 'turn-1';
  const respond = (rpc, result, error) => out([{ id: rpc.id, result, error }]);
  const event = (method, params) => out([{ method, params: { threadId, turnId, ...params } }]);
  assert.equal(child.sent[0].method, 'initialize');
  assert.equal(child.sent[0].params.clientInfo.name, 'relayforge');
  respond(child.sent[0], {});
  await step();
  assert.deepEqual(child.sent[1], { method: 'initialized' });
  const threadRpc = child.sent[2];
  assert.equal(threadRpc.method, resume ? 'thread/resume' : 'thread/start');
  assert.deepEqual(threadRpc.params, { cwd: '/wt', model: 'gpt-example', sandbox: 'workspace-write',
    approvalPolicy: 'on-request', approvalsReviewer: 'user', ...(resume ? { threadId, excludeTurns: true } : { ephemeral: false }) });
  const thread = { id: threadId, path: path.join(h.home, 'rollout.jsonl') };
  respond(threadRpc, { thread, sandbox: { type: 'workspaceWrite' }, approvalsReviewer: 'user', approvalPolicy: 'on-request' });
  await step();
  assert.deepEqual(plain(h.threads.at(-1)), ['sid-1', thread]);
  assert.equal(run.transcript, thread.path);
  assert.deepEqual(child.sent[3].params, { threadId, input: [{ type: 'text', text: codex.prompt, text_elements: [] },
    ...codex.images.map((path) => ({ type: 'localImage', path }))] });
  const turn = { id: turnId, status: 'inProgress', items: [] };
  respond(child.sent[3], { turn });
  event('turn/started', { turn });
  await step();
  return { run, child, respond, event, out,
    finish(status = 'completed', error = null) {
      event('turn/completed', { turn: { ...turn, status, error } });
      assert.equal(child.stdin.writableEnded, true);
      child.emit('close', 0);
    } };
}

test('Codex uses stdio, resumes the same native ID and renders completed items and the turn error', async (t) => {
  const h = harness(t);
  const first = await codexTurn(h, false);
  assert.deepEqual(plain(h.spawned[0].opts.stdio), ['pipe', 'pipe', 'pipe']);
  assert.deepEqual(plain(h.spawned[0].opts.env), { A: '1', RELAYFORGE_EVENTS: '/events' });
  first.event('item/completed', { item: { id: 'cmd', type: 'commandExecution', command: 'pytest', exitCode: 1 } });
  first.event('item/completed', { item: { id: 'patch', type: 'fileChange', changes: [{ path: 'a.js' }] } });
  first.event('item/completed', { item: { id: 'msg', type: 'agentMessage', text: '已运行测试。' } });
  first.finish();
  assert.match(first.run.log, /后台运行开始[\s\S]*跑 pytest[\s\S]*退出码 1[\s\S]*改 a.js[\s\S]*已运行测试。[\s\S]*这一轮做完了/);
  assert.deepEqual(plain(h.after), [first.run.id]);
  const resumed = await codexTurn(h, true);
  const error = { message: '连接失败' };
  resumed.event('error', { error, willRetry: false });
  resumed.finish('failed', error);
  assert.equal(resumed.run.log.match(/连接失败/g).length, 1);
  assert.deepEqual(plain(h.progress.map((entry) => entry[1])), ['reply', 'exit']);
});

test('Codex cannot start a turn with a wider sandbox than requested', async (t) => {
  const h = harness(t);
  const { child, out } = begin(h, 'codex', 'fix:F-1', { codex: {
    sandbox: 'read-only', approvalPolicy: 'untrusted', prompt: '检查目录',
  } });
  out([{ id: child.sent[0].id, result: {} }]);
  await step();
  out([{ id: child.sent[2].id, result: { thread: { id: 'native' }, sandbox: { type: 'workspaceWrite' },
    approvalPolicy: 'untrusted', approvalsReviewer: 'user' } }]);
  await step();
  assert.equal(child.sent.length, 3);
  assert.deepEqual(h.killed, [child.pid]);
  child.emit('close', null, 'SIGKILL');
  assert.equal(h.progress[0][1], 'exit');
});

test('a turn that ends on a question waits for your reply; a reply runs it again in the background with your words', async (t) => {
  const h = harness(t);
  const rpc = await codexTurn(h, false);
  rpc.event('item/completed', { item: { id: 'msg', type: 'agentMessage', text: '有两种修法。\n要改接口还是只改调用方？' } });
  rpc.finish();
  assert.deepEqual(plain(h.progress.map((p) => p.slice(0, 2))), [['fix:F-1', 'answer']]);
  assert.deepEqual(plain(h.after), []); // 在问你：等你回复，排着的话回复时一起带上
  const q = h.hitl.snapshot().questions;
  assert.equal(q.length, 1);
  assert.match(q[0].text, /要改接口还是只改调用方？/);
  assert.equal(h.notes.length, 1); // 弹了一次系统通知
  assert.deepEqual(plain(await h.hitl.reply('fix:F-1', '  ')), { error: '回复不能是空的。' });
  assert.deepEqual(plain(await h.hitl.reply('fix:F-1', '只改调用方')), { ok: true });
  assert.deepEqual(plain(h.opened), [{ id: 'fix:F-1', say: '只改调用方', headless: true }]);
  assert.equal(h.hitl.snapshot().questions.length, 0);
});

test('when the hook already said the turn is over, the process exit does not say it again', (t) => {
  const h = harness(t);
  const { run, child, out } = begin(h);
  run.hookKind = 'reply'; // attention.js 记下的：Stop 钩子报过「说完了」
  out([{ type: 'result', result: '好了吗？' }]);
  child.emit('exit', 0);
  assert.deepEqual(plain(h.progress), []);
  assert.deepEqual(plain(h.phases.pop()), ['fix:F-1', 'reply']); // 以钩子为准，不因为末尾有问号就当成在问你
  assert.equal(h.hitl.snapshot().questions.length, 0);
});

test('a run that could not start, or died silently, ends as exited; one that was taken over reports nothing', (t) => {
  const h = harness(t);
  const a = begin(h);
  a.child.emit('error', new Error('spawn claude ENOENT'));
  assert.deepEqual(plain(h.progress), [['fix:F-1', 'exit', 'AI 会话在后台没跑完就退出了。']]);
  assert.match(a.run.log, /后台运行没能开始：spawn claude ENOENT/);
  const b = begin(h, 'claude', 'test:R-1');
  h.state.runs.delete('test:R-1'); // sessions.stop 已经收拾过（接管、关掉）
  b.child.emit('exit', 143);
  assert.equal(h.progress.length, 1);
});

test('typing into a background run is not passed on; opening it shows what it has done so far', (t) => {
  const h = harness(t);
  const { run, out } = begin(h);
  out([{ type: 'assistant', message: { content: [{ type: 'text', text: '在跑测试' }] } }]);
  h.headless.typedInto(run);
  h.headless.typedInto(run);
  assert.equal((run.log.match(/终端不收键盘/g) || []).length, 1);
  h.sent.length = 0;
  h.headless.replay(run);
  assert.match(h.sent[0][1].data, /正在后台跑[\s\S]*在跑测试/);
});

test('an unfinished background run stops at the silence limit and offers terminal takeover', (t) => {
  const h = harness(t);
  const { run, child, out } = begin(h);
  const limit = h.stuck.HEADLESS_SILENCE_MS;
  h.at(limit - 1);
  out([{ type: 'ignored_event' }]); // 无需显示的输出也说明后台进程有动静
  run.last = '还需要什么？';
  run.hookKind = 'reply';
  h.tick(run, 2 * limit - 2);
  assert.equal(h.state.runs.get(run.id), run);
  h.tick(run, 2 * limit - 1);
  assert.equal(child.killed, true);
  assert.equal(h.state.runs.has(run.id), false);
  assert.equal(h.progress.length, 1);
  assert.deepEqual(h.progress[0].slice(0, 2), [run.id, 'exit']);
  assert.match(h.progress[0][2], /15 分钟没有输出/);
  assert.ok(run.log.includes(h.progress[0][2]));
  assert.deepEqual(plain(h.sent.find(([c]) => c === 'term:exit')[1]), { id: run.id, code: 124, headless: true, kind: 'exit' });
  child.emit('exit', 143);
  assert.equal(h.progress.length, 1);
  assert.deepEqual(h.after, []);
  assert.equal(h.hitl.snapshot().questions.length, 0);
  const model = require('../app/src/renderer/scripts/hitl-model');
  assert.match(model.html(h.hitl.snapshot(), false), /后台会话卡住了[\s\S]*没有输出[\s\S]*data-hitl="take" data-run="fix:F-1"/);
  h.state.project = '/other';
  assert.equal(h.hitl.snapshot().stuck.length, 0);
  h.state.project = '/project';
  h.hitl.takeover(run.id);
  assert.equal(h.hitl.snapshot().stuck.length, 0);
  assert.deepEqual(plain(h.sent.find(([c]) => c === 'hitl:takeover')[1]), { id: run.id });
});

test('even a background run with recurring output stops at the total limit', (t) => {
  const h = harness(t);
  const { run, child } = begin(h, 'codex');
  const limit = h.stuck.HEADLESS_LIMIT_MS;
  const pulse = h.stuck.HEADLESS_SILENCE_MS / 2;
  for (let at = pulse; at < limit; at += pulse) {
    h.at(at);
    child.stderr.emit('data', Buffer.from('模型仍在工作'));
    h.tick(run, at);
    assert.equal(h.state.runs.get(run.id), run);
  }
  h.tick(run, limit);
  assert.equal(child.killed, true);
  assert.match(run.log, /已运行 60 分钟/);
});

test('only the session with a pending approval pauses its total and silence timers', (t) => {
  const h = harness(t);
  const { run, out } = begin(h);
  const other = begin(h, 'codex', 'test:R-1').run;
  other.sessionId = 'sid-2';
  const silence = h.stuck.HEADLESS_SILENCE_MS;
  const limit = h.stuck.HEADLESS_LIMIT_MS;
  h.tick(run, silence - 1);
  const dir = h.hitl.dirOf(h.state.project);
  fs.mkdirSync(dir, { recursive: true });
  const request = path.join(dir, 'a-00000001.json');
  fs.writeFileSync(request, JSON.stringify({ id: 'a-00000001', session: run.sessionId, input: {} }));
  h.tick(run, silence - 1);
  h.tick(other, silence);
  assert.equal(h.state.runs.has(other.id), false);
  const resumed = silence + limit;
  h.tick(run, resumed);
  assert.equal(h.state.runs.get(run.id), run);
  fs.unlinkSync(request);
  h.tick(run, resumed);
  // 保留审批前已过去的有效时间，审批之后有输出才重新开始计算无输出时长。
  out([{ type: 'ignored_event' }]);
  const remaining = limit - silence + 1;
  const pulse = silence / 2;
  for (let elapsed = pulse; elapsed < remaining; elapsed += pulse) {
    h.at(resumed + elapsed);
    out([{ type: 'ignored_event' }]);
    h.tick(run, resumed + elapsed);
    assert.equal(h.state.runs.get(run.id), run);
  }
  h.tick(run, resumed + remaining);
  assert.match(run.log, /已运行 60 分钟/);
});

test('approval requests written by the hook are listed with their session, and your decision is written back', (t) => {
  const h = harness(t);
  const { run } = begin(h);
  const dir = h.hitl.dirOf('/project');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'a-0000beef.json'), JSON.stringify({ id: 'a-0000beef', session: 'sid-1', tool: 'Bash', input: { command: 'rm -rf build', description: '清掉旧的构建' }, at: '2026-11-22T10:00:00+08:00' }));
  h.hitl.tick();
  const pushed = h.sent.filter(([c]) => c === 'hitl').pop()[1];
  assert.deepEqual(plain(pushed.approvals), [{ id: 'a-0000beef', run: run.id, title: '问题 1 的修复', tool: 'Bash', what: 'rm -rf build', why: '清掉旧的构建', at: '2026-11-22T10:00:00+08:00' }]);
  assert.equal(h.notes.filter((n) => /等你批准/.test(n.title)).length, 1);
  h.hitl.tick();
  assert.equal(h.notes.filter((n) => /等你批准/.test(n.title)).length, 1); // 同一条只通知一次
  assert.deepEqual(plain(h.hitl.decide('../x', true)), { error: '没有这个批准请求。' });
  assert.deepEqual(plain(h.hitl.decide('a-00000000', true)), { error: '这个请求已经结束了（会话不等了，或者已经回答过）。' });
  assert.deepEqual(plain(h.hitl.decide('a-0000beef', false, '先别删')), { ok: true });
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir, 'a-0000beef.decision'), 'utf8')), { behavior: 'deny', message: '先别删' });
});

test('taking over stops the background run, refuses what it was waiting on, and asks the window for a terminal', (t) => {
  const h = harness(t);
  const { run } = begin(h);
  const dir = h.hitl.dirOf('/project');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'a-0000cafe.json'), JSON.stringify({ id: 'a-0000cafe', session: 'sid-1', tool: 'Bash', input: { command: 'make' } }));
  assert.deepEqual(plain(h.hitl.takeover('fix:F-1')), { ok: true });
  assert.equal(run.term.killed, true);
  assert.equal(JSON.parse(fs.readFileSync(path.join(dir, 'a-0000cafe.decision'), 'utf8')).behavior, 'deny');
  assert.deepEqual(plain(h.sent.filter(([c]) => c === 'hitl:takeover').pop()[1]), { id: 'fix:F-1' });
});

test('Codex approvals use the RPC request ID; inbox waits, then sends a marked steer and records its acknowledgement', async (t) => {
  const h = harness(t);
  const rpc = await codexTurn(h, false);
  rpc.out([{ id: 'cmd-approval', method: 'item/commandExecution/requestApproval',
    params: { threadId: 'native-1', turnId: 'turn-1', itemId: 'cmd', command: 'ls /fixture', cwd: '/wt', reason: '查看测试目录' } }]);
  const card = h.hitl.snapshot().approvals[0];
  assert.equal(card.what, 'ls /fixture');
  assert.match(card.why, /\/wt[\s\S]*查看测试目录/);
  assert.deepEqual(plain(h.phases.at(-1)), [rpc.run.id, 'approve']);
  const queued = h.inbox.post({ to: rpc.run.id, from: 'testing', text: '测试结论已更新' });
  assert.equal(queued.session, null); // Stop 钩子不会把这条话同时交一遍。
  assert.equal(rpc.child.sent.at(-1).method, 'turn/start');
  assert.deepEqual(plain(h.hitl.decide(card.id, true)), { ok: true });
  assert.deepEqual(rpc.child.sent.at(-2), { id: 'cmd-approval', result: { decision: 'accept' } });
  assert.equal(h.hitl.snapshot().approvals.length, 0);
  const steer = rpc.child.sent.at(-1);
  assert.equal(steer.method, 'turn/steer');
  assert.equal(steer.params.expectedTurnId, 'turn-1');
  assert.match(steer.params.input[0].text, new RegExp(queued.id + '〕\n测试结论已更新'));
  assert.equal(queued.state, 'queued');
  rpc.respond(steer, { turnId: 'turn-1' });
  await step();
  assert.equal(queued.via, 'steer');
  assert.equal(queued.state, 'sent');
  rpc.finish();
});

test('Codex permission grants apply to the current turn and expired RPC cards cannot grant anything', async (t) => {
  const h = harness(t);
  const rpc = await codexTurn(h, false);
  const permissions = { fileSystem: { write: ['/fixture'] } };
  const params = { threadId: 'native-1', turnId: 'turn-1', itemId: 'permissions', cwd: '/wt', permissions };
  rpc.out([{ id: 12, method: 'item/permissions/requestApproval', params }]);
  const first = h.hitl.snapshot().approvals[0];
  assert.deepEqual(JSON.parse(first.what), permissions);
  rpc.event('serverRequest/resolved', { requestId: 12 });
  assert.equal(h.hitl.snapshot().approvals.length, 0);
  assert.ok(h.hitl.decide(first.id, true).error);
  rpc.out([{ id: 13, method: 'item/permissions/requestApproval', params }]);
  h.hitl.decide(h.hitl.snapshot().approvals[0].id, true);
  assert.deepEqual(rpc.child.sent.at(-1), { id: 13, result: { permissions, scope: 'turn' } });
  rpc.finish();
});

test('Codex question cards can send an active reply, and takeover denies approval then kills the tree before opening the terminal', async (t) => {
  const h = harness(t);
  const rpc = await codexTurn(h, false);
  rpc.event('item/completed', { item: { id: 'msg', type: 'agentMessage', text: '只改调用方吗？' } });
  assert.equal(h.hitl.snapshot().questions[0].text, '只改调用方吗？');
  const reply = h.hitl.reply(rpc.run.id, '只改调用方');
  const steer = rpc.child.sent.at(-1);
  assert.equal(steer.method, 'turn/steer');
  assert.equal(steer.params.input[0].text, '只改调用方');
  rpc.respond(steer, { turnId: 'turn-1' });
  assert.deepEqual(plain(await reply), { ok: true });
  assert.deepEqual(h.opened, []);
  rpc.out([{ id: 10, method: 'item/fileChange/requestApproval', params: { threadId: 'native-1', turnId: 'turn-1', itemId: 'missing' } }]);
  assert.deepEqual(rpc.child.sent.at(-1), { id: 10, result: { decision: 'decline' } });
  rpc.event('item/started', { item: { id: 'f', type: 'fileChange', changes: [{ kind: { type: 'update' }, path: 'a.js', diff: '+const a = 1;' }] } });
  rpc.out([{ id: 11, method: 'item/fileChange/requestApproval', params: { threadId: 'native-1', turnId: 'turn-1', itemId: 'f' } }]);
  assert.match(h.hitl.snapshot().approvals[0].what, /a.js[\s\S]*const a = 1/);
  const takeover = h.hitl.takeover(rpc.run.id);
  assert.equal(h.hitl.takeover(rpc.run.id), takeover);
  assert.deepEqual(rpc.child.sent.at(-1), { id: 11, result: { decision: 'decline' } });
  assert.deepEqual(h.killed, [rpc.child.pid]);
  assert.ok(!h.sent.some(([c]) => c === 'hitl:takeover'));
  rpc.child.emit('close', null, 'SIGKILL');
  assert.deepEqual(plain(await takeover), { ok: true });
  assert.deepEqual(plain(h.sent.filter(([c]) => c === 'hitl:takeover').pop()[1]), { id: rpc.run.id });
  assert.deepEqual(plain(h.progress.map((p) => p[1])), ['approve']);
});

test('Codex blocking questions are answered by RPC and a steer rejected at the turn boundary stays queued', async (t) => {
  const h = harness(t);
  const rpc = await codexTurn(h, false);
  rpc.out([{ id: 'question', method: 'item/tool/requestUserInput',
    params: { threadId: 'native-1', turnId: 'turn-1', itemId: 'q', questions: [{ id: 'scope', question: '改哪里？' }] } }]);
  assert.equal(h.hitl.snapshot().questions[0].text, '改哪里？');
  const queued = h.inbox.post({ to: rpc.run.id, text: '这是程序消息' });
  assert.deepEqual(plain(await h.hitl.reply(rpc.run.id, '改调用方')), { ok: true });
  assert.deepEqual(rpc.child.sent.at(-2), { id: 'question', result: { answers: { scope: { answers: ['改调用方'] } } } });
  const steer = rpc.child.sent.at(-1);
  assert.equal(steer.method, 'turn/steer');
  rpc.respond(steer, null, { message: 'turn is no longer active' });
  await step();
  assert.equal(queued.state, 'queued');
  rpc.finish();
  assert.deepEqual(plain(h.opened), [{ id: rpc.run.id, headless: true }]);
});

test('the card escapes what the session wants to run and offers the right buttons', () => {
  const M = require(path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts', 'hitl-model.js'));
  assert.equal(M.html({ approvals: [], questions: [] }, false), '');
  const data = { approvals: [{ id: 'a-1', run: 'fix:F-1', title: '修复', tool: 'Bash', what: 'echo <b>&', why: '' }], questions: [{ id: 'test:R-1', title: '测试', text: '要继续吗？' }] };
  const html = M.html(data, false);
  assert.match(html, /后台会话需要你 · 2/);
  assert.match(html, /跑命令<\/span><code>echo &lt;b&gt;&amp;<\/code>/);
  assert.match(html, /data-hitl="allow"[\s\S]*data-hitl="deny"[\s\S]*data-hitl="take" data-run="fix:F-1"/);
  assert.match(html, /要继续吗？[\s\S]*<textarea[\s\S]*data-hitl="reply"/);
  assert.doesNotMatch(M.html(data, true), /hitl-card/); // 收起来只剩一行
});
