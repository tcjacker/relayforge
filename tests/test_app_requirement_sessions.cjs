// 需求的测试会话单独选 CLI 和模型：默认「跟开发一样」（不复制值）、老需求照跟开发处理、新开时按选择启动（令牌、权限规则、开场白照旧）、
// 已经开着或接着聊的测试会话不受后来的修改影响；新建需求的界面和需求上「测试会话」一块的 model 层。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const repo = path.join(__dirname, '..');
const main = path.join(repo, 'app', 'src', 'main');
const scripts = path.join(repo, 'app', 'src', 'renderer', 'scripts');
const picker = require('./relayforge/requirement-session-pickers.cjs');
const RS = require(path.join(scripts, 'requirement-sessions.js'));

function load(name, mocks) {
  const context = vm.createContext({ require: (id) => (Object.hasOwn(mocks, id) ? mocks[id] : require(id)), module: { exports: {} }, process, Buffer, clearTimeout });
  vm.runInContext(fs.readFileSync(path.join(main, name + '.js'), 'utf8'), context);
  return context.module.exports;
}

function harness(agent, req, known = {}) {
  const launches = [], hookCalls = [], attached = [];
  const state = { project: '/project', runs: new Map() };
  const child = () => { const p = new EventEmitter(); p.stdin = new EventEmitter(); p.stdout = new EventEmitter(); p.stderr = new EventEmitter(); p.stdio = [null, null, null, new EventEmitter()]; p.kill = () => {}; p.pid = 4242; return p; };
  const sessions = load('sessions', {
    child_process: { execFile: (_file, _args, _options, done) => done(null, ''), spawn: (command, args, options) => { launches.push({ command, args: Array.from(args), options }); return child(); } },
    './config': { EVENTS: '/events', PTY_BRIDGE: '/bridge.py', PYTHON: 'python3' },
    './demo': { session: () => null, info: () => null }, './initialization': { forSession: () => null }, './settings': { get: () => ({ agent }), command: (a) => [a] },
    './codex-sessions': { has: (id) => !!known[id], find: (id) => known[id] && '/transcript', nativeId: () => 'native-codex-id', register() {} },
    './backend': { rf: async (args) => { attached.push(args); return { ok: true, stdout: 'T-1' }; }, cleanEnv: () => ({}), lastLine: String },
    './state': { state, send() {} },
    './requirements': { listReqs: async () => [req], pushReqs() {} },
    './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post() {}, takeForOpen: () => '', envFor: () => ({}) }, './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, seen() {}, stopped() {} },
    './codex-hooks': { flags: () => [], env: () => ({}) }, './claude-settings': { guardSpec: () => ({}), draftsFor: () => '/drafts', hooks: (...args) => { hookCalls.push(args); return '{}'; } },
    './splits': { guide: 'split prompt' },
    './ui-names': { guide: () => '' }, './prompts': { mapEdit: () => '', intake: () => 'intake prompt', develop: () => 'development prompt', following: () => 'follow-up prompt', testing: () => 'testing prompt' },
    './session-token': require(path.join(main, 'session-token')),
  });
  return { sessions, launches, hookCalls, attached };
}

const model = (args) => (args.includes('--model') ? args[args.indexOf('--model') + 1] : '');
const open = (over = {}) => ({ id: 'R-1', title: '导出', state: 'open', worktree: '/work', sessions: ['d1'], dev_sessions: ['d1'], intake: [], test_sessions: [], session_choices: {}, ...over });

test('a testing session with no choice of its own follows the development session, and a later change of the development choice carries over', async () => {
  const req = open({ session_choices: { development: { cli: 'codex', model: 'gpt-5.4' } } });
  let h = harness('claude', req);
  await h.sessions.open({ id: 'test:R-1', cols: 80, rows: 24 });
  assert.deepEqual([h.launches[0].args[1], model(h.launches[0].args)], ['codex', 'gpt-5.4']);
  h.sessions.close('test:R-1');
  req.session_choices.development = { cli: 'claude', model: 'opus' }; // 开发会话的选择改了：没单独设置的测试会话跟着变
  h = harness('claude', req);
  await h.sessions.open({ id: 'test:R-1', cols: 80, rows: 24 });
  assert.deepEqual([h.launches[0].args[1], model(h.launches[0].args)], ['claude', 'opus']);
});

test('an old requirement (no saved choices at all) starts its testing session with the app setting, as before', async () => {
  for (const agent of ['claude', 'codex']) {
    const h = harness(agent, open({ session_choices: {} }));
    await h.sessions.open({ id: 'test:R-1', cols: 80, rows: 24 });
    assert.deepEqual([h.launches[0].args[1], model(h.launches[0].args)], [agent, '']);
  }
});

test('its own choice wins, with the token, the testing session\'s permission rules and the opening prompt as before', async () => {
  const req = open({ session_choices: { development: { cli: 'claude', model: 'sonnet' }, testing: { cli: 'claude', model: 'opus' } } });
  const h = harness('codex', req);
  await h.sessions.open({ id: 'test:R-1', cols: 80, rows: 24 });
  const launch = h.launches[0];
  assert.deepEqual([launch.args[1], model(launch.args)], ['claude', 'opus']); // 不是开发的 sonnet，也不是应用设置的 codex
  assert.match(launch.args.at(-1), /testing prompt/);
  assert.ok(launch.options.env.RELAYFORGE_SESSION_SIG, 'the signed session token is in its environment');
  assert.equal(launch.options.cwd, '/work');
  const attach = h.attached.find((a) => a[0] === 'req' && a[1] === 'attach');
  assert.ok(attach && attach.includes('--test'));
  const options = h.hookCalls[0].at(-1);
  assert.equal(options.role, 'test'); // 测试会话的权限规则
  assert.ok(launch.args.includes('NotebookEdit'), 'the testing session still may not edit notebooks');
});

test('Codex as the testing session: its sandbox and launch arguments are the existing ones, with the chosen model', async () => {
  const req = open({ session_choices: { development: { cli: 'claude', model: '' }, testing: { cli: 'codex', model: 'gpt-5.4-mini' } } });
  const h = harness('claude', req);
  await h.sessions.open({ id: 'test:R-1', cols: 80, rows: 24 });
  const args = h.launches[0].args;
  assert.deepEqual([args[1], model(args), args[args.indexOf('--sandbox') + 1]], ['codex', 'gpt-5.4-mini', 'workspace-write']);
  assert.ok(args.includes('sandbox_workspace_write.network_access=true') && !args.includes('approvals_reviewer="user"')); // 交互会话：沿用用户的 Codex 审核设置，不固定成人工
  assert.equal(h.hookCalls.length, 0, 'no Claude Code permission rules for Codex');
});

test('a testing session that is already open or resumed is not changed by a later choice', async () => {
  const req = open({ test_sessions: ['t1'], session_choices: { development: { cli: 'claude', model: '' }, testing: { cli: 'claude', model: 'opus' } } });
  const h = harness('claude', req, { t1: true }); // 它的聊天记录是 Codex 的：接着聊就是 Codex，不管现在的选择是什么
  await h.sessions.open({ id: 'test:R-1', cols: 80, rows: 24 });
  assert.deepEqual(h.launches[0].args.slice(1, 4), ['codex', 'resume', 'native-codex-id']);
  assert.equal(model(h.launches[0].args), '');
  assert.equal(h.sessions.close('test:R-1'), true);
  assert.equal(h.attached.filter((a) => a[1] === 'attach').length, 0, 'resumed: no new registration, no new opening prompt');
});

test('creating: nothing is sent for the testing session unless it was chosen separately; changing it later sends the right command', () => {
  const two = { intake: { cli: 'claude', model: 'sonnet' }, development: { cli: 'codex', model: 'gpt-5.4' } };
  assert.deepEqual(RS.createArgs(two), ['--intake-cli', 'claude', '--intake-model', 'sonnet', '--development-cli', 'codex', '--development-model', 'gpt-5.4']);
  assert.deepEqual(RS.createArgs({ ...two, testing: { follow: true } }), RS.createArgs(two)); // 「跟开发一样」不传参数，也不复制开发的值
  assert.deepEqual(RS.createArgs({ ...two, testing: { cli: 'claude', model: '' } }).slice(-2), ['--testing-cli', 'claude']);
  assert.deepEqual(RS.createArgs({ ...two, testing: { cli: 'claude', model: 'opus' } }).slice(-4), ['--testing-cli', 'claude', '--testing-model', 'opus']);
  assert.throws(() => RS.createArgs({ ...two, testing: { cli: 'codex', model: 'opus' } }), /测试会话/); // Codex 没有 Claude 的模型
  assert.throws(() => RS.createArgs({ ...two, development: undefined }), /立项和开发/);
});

test('read-only lines: development and testing choices, with app default and follow wording', () => {
  const dev = { cli: 'codex', model: 'gpt-5.4' };
  assert.deepEqual(RS.choiceLines({ development: dev, testing: { cli: 'claude', model: 'sonnet' } }, 'claude'), { development: 'Codex · GPT-5.4', testing: 'Claude Code · Sonnet' });
  assert.equal(RS.choiceLines({ development: dev }, 'claude').testing, '跟开发一样（Codex · GPT-5.4）');
  assert.deepEqual(RS.choiceLines({}, 'claude'), { development: '应用默认（Claude Code · 默认模型）', testing: '跟开发一样（Claude Code · 默认模型）' });
  assert.equal(RS.choiceLines(undefined, 'codex').development, '应用默认（Codex · 默认模型）');
  assert.equal(RS.choiceLines({ development: { cli: 'claude', model: '' } }, 'codex').development, 'Claude Code · 默认模型');
});

test('what the testing session uses, for display: its own, else the development one, else nothing saved', () => {
  const dev = { cli: 'codex', model: 'gpt-5.4' };
  assert.deepEqual(RS.testingChoice({ development: dev }), { follows: true, choice: dev });
  assert.deepEqual(RS.testingChoice({ development: dev, testing: { cli: 'claude', model: '' } }), { follows: false, choice: { cli: 'claude', model: '' } });
  assert.deepEqual(RS.testingChoice({}), { follows: true, choice: null }); // 老需求
  assert.deepEqual(RS.testingChoice(undefined), { follows: true, choice: null });
  assert.match(RS.CODEX_TESTING_NOTE, /用 Codex 测试时，程序对它的限制比 Claude Code 少/);
});

test('the new-requirement dialog: the testing picker starts on "跟开发一样", has no model of its own then, and switching to a CLI gives its models', () => {
  const out = picker.inspect({ initial: { intake: { cli: 'claude', model: '' }, development: { cli: 'codex', model: '' }, testing: { cli: 'codex', model: '' } }, change: { phase: 'development', cli: 'claude' }, follows: ['testing'] });
  assert.deepEqual(out.testing.choice, { follow: true }); // reset 把测试选成「跟开发一样」，改开发的 CLI 不影响它
  assert.deepEqual(out.testing.models, ['']);
  assert.deepEqual(out.testing.cliOptions.slice(0, 2), ['follow', 'claude']);
  assert.deepEqual(out.testing.modelDisabled, true);
  assert.equal(out.development.choice.cli, 'claude');
  const changed = picker.inspect({ initial: { testing: { cli: 'codex', model: '' } }, change: { phase: 'testing', cli: 'codex' }, follows: ['testing'] });
  assert.deepEqual(changed.testing.choice, { cli: 'codex', model: '' });
  assert.deepEqual(changed.testing.models, ['', 'gpt-5.4', 'gpt-5.4-mini']);
  assert.equal(changed.testing.modelDisabled, false);
  const back = picker.inspect({ initial: { testing: { cli: 'codex', model: '' } }, change: { phase: 'testing', cli: 'follow' }, follows: ['testing'] });
  assert.deepEqual(back.testing.choice, { follow: true });
});
