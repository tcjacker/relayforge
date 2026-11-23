const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');

const main = path.join(__dirname, '..', 'app', 'src', 'main');
const file = path.join(main, 'subagents.js');
const local = createRequire(file);
const { issuer } = local('./session-token');
const { defaults, available } = local('../renderer/scripts/subagent-model');

function scenario(parent, authorized = true) {
  const calls = [];
  const statuses = { claude: 'in', codex: 'in' };
  const selections = new Map([['R-1', { authorized }], ['R-2', { authorized: true }]]);
  const run = { id: 'R-1', sessionId: 'dev', root: '/project/.worktrees/R-1', agent: parent };
  const state = { project: '/project', runs: new Map([[run.id, run]]) };
  const req = { id: run.id, state: 'open', worktree: run.root, session_choices: { development: { cli: parent } } };
  const spawn = (cli, args, options) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.stdout.setEncoding = () => child.stdout;
    child.stderr.setEncoding = () => child.stderr;
    child.stdin = new EventEmitter();
    child.stdin.end = (input) => setImmediate(() => {
      calls.push({ cli, args: [...args], options, input });
      const message = '子任务返回 ' + cli;
      child.stdout.emit('data', JSON.stringify(cli === 'claude' ? { result: message } :
        { type: 'item.completed', item: { type: 'agent_message', text: message } }));
      child.emit('close', 0);
    });
    return child;
  };
  const mocks = {
    './state': { state },
    './cli-auth': { status: async (cli) => ({ state: statuses[cli] }) },
    './settings': { get: () => ({ subagent: defaults }), requirementSubagent: (_p, id) => selections.get(id),
      launch: (cli) => ({ file: cli }), catalogs: () => ({ claude: [['sonnet', 'Sonnet']], codex: [['gpt-5.4-mini', 'Mini']] }) },
    './requirements': { listReqs: async () => [req] },
    './backend': { cleanEnv: () => ({ RELAYFORGE_APP_TOKEN: 'app', RELAYFORGE_SESSION_SIG: 'parent', PATH: '/bin' }) },
    './sandbox-check': { check: async () => ({ ok: true }) },
    'node:child_process': { spawn },
  };
  const context = vm.createContext({ require: (name) => mocks[name] || local(name), module: { exports: {} }, setTimeout, clearTimeout });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
  const app = issuer();
  app.setDelegate(context.module.exports.delegate);
  const { token } = app.issue({ session: run.sessionId, kind: 'develop', owner: run.id });
  app.bind(token, process.pid, run.root);
  const ask = (action, fields) => app.answer(JSON.stringify({ op: 'subagent', token, action, ...fields }));
  const choose = (cli) => { selections.get('R-1').choice = { cli, model: null }; };
  return { calls, statuses, ask, choose, app, token };
}

test('已登录的工具可选，无法确认登录时不列入', () => {
  assert.deepEqual(available({ codex: { state: 'in' }, claude: { state: 'out' } }), ['codex']);
  assert.deepEqual(available({ codex: { state: 'desktop' }, claude: { state: 'missing' } }), []);
});

test('两个开发 CLI 默认跟随，也都能明确改选，输出带实际执行的 CLI 和模型依据', async () => {
  for (const [parent, other, model] of [['codex', 'claude', 'sonnet'], ['claude', 'codex', 'gpt-5.4-mini']]) {
    const { calls, ask, choose } = scenario(parent);
    assert.equal((await ask('run', { task: '查一条记录' })).result.cli, parent);
    choose(other);
    const response = await ask('run', { task: '查第二条记录', model, reason: '只需定位一处' });
    assert.equal(response.result.cli, other);
    assert.equal(response.result.model, model);
    assert.equal(response.result.reason, '只需定位一处');
    assert.equal(response.result.result, '子任务返回 ' + other);
    assert.deepEqual(calls.map((call) => call.cli), [parent, other]);
    assert.ok(calls[1].args.includes(model));
    assert.equal(calls[1].options.env.RELAYFORGE_APP_TOKEN, undefined);
    assert.equal(calls[1].options.env.RELAYFORGE_SESSION_SIG, undefined);
    assert.equal(calls[1].options.cwd, '/project/.worktrees/R-1');
  }
});

test('只配置与其他需求已授权不能授权本需求，测试会话也无法发起委派', async () => {
  const { calls, ask, choose, app, token } = scenario('codex', false);
  choose('claude');
  await assert.rejects(ask('run', { task: '检查代码' }), /没有立项对话中的 subagent 授权/);
  const tester = app.issue({ session: 'test', kind: 'test', owner: 'R-1' }).token;
  app.bind(tester, process.pid, '/project/.worktrees/R-1');
  assert.equal(app.answer(JSON.stringify({ op: 'subagent', token: tester, action: 'run' })).ok, false);
  app.unbind(token);
  assert.equal(ask('status').ok, false);
  assert.equal(calls.length, 0);
});

test('所选工具退出登录会暂停，原工具恢复或明确改选后继续', async () => {
  const { calls, ask, choose, statuses } = scenario('codex');
  const request = { task: '查一条记录' };
  choose('claude');
  statuses.claude = 'out';
  await assert.rejects(ask('run', request), /Claude Code 尚未登录/);
  assert.equal(calls.length, 0);
  statuses.claude = 'in';
  assert.equal((await ask('run', request)).result.cli, 'claude');
  statuses.claude = 'missing';
  await assert.rejects(ask('run', request), /Claude Code 尚未安装/);
  choose('codex');
  assert.equal((await ask('run', request)).result.cli, 'codex');
  assert.deepEqual(calls.map(({ cli }) => cli), ['claude', 'codex']);
});

test('解除开发会话的令牌会取消正在运行的子任务请求', async () => {
  const app = issuer();
  const { token } = app.issue({ session: 'dev', kind: 'develop', owner: 'R-1' });
  let signal;
  app.setDelegate((request) => new Promise((resolve) => {
    signal = request.signal;
    signal.addEventListener('abort', () => resolve('stopped'), { once: true });
  }));
  app.bind(token, process.pid, '/project/.worktrees/R-1');
  const result = app.answer(JSON.stringify({ op: 'subagent', token, action: 'run' }));
  await Promise.resolve();
  app.unbind(token);
  assert.equal(signal.aborted, true);
  assert.equal((await result).result, 'stopped');
});
