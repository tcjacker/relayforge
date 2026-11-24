// 另开一个会话接替同一件事（开发、修复、测试的后续会话）时，开场白末尾带上上一个会话最后几轮的步骤（rf.py history session --brief）。
// 第一个开发会话、立项后的第一个开发会话、接着原来的会话聊时都不带；取不到时照常开会话。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const repo = path.join(__dirname, '..');
const main = path.join(repo, 'app', 'src', 'main');
const T = require(path.join(main, 'session-token'));

function loadWith(name, mocks) {
  const localRequire = createRequire(path.join(main, name + '.js'));
  const context = vm.createContext({ require: (id) => (Object.hasOwn(mocks, id) ? mocks[id] : localRequire(id)), module: { exports: {} }, process, Buffer, setTimeout, clearTimeout, console });
  vm.runInContext(fs.readFileSync(path.join(main, name + '.js'), 'utf8'), context);
  return context.module.exports;
}

async function open(id, over, recap = { ok: true, stdout: '上一个会话 S-old 最后 1 轮做了什么：\n<<<\n【1】用户：导出\n>>>\n' }) {
  const wt = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-wt-'));
  const spawned = [], calls = [];
  const r = { id: 'R-1', title: '导出', state: 'open', worktree: wt, sessions: [], intake: [], dev_sessions: [], test_sessions: [], ...over };
  const sessions = loadWith('sessions', {
    child_process: { execFile: (_file, _args, _options, done) => done(null, ''), spawn(cmd, args) { spawned.push(args); const ee = () => ({ on() {}, write() {} }); return { pid: 4244, stdin: ee(), stdout: ee(), stderr: ee(), stdio: [null, null, null, ee()], on() {}, kill() {} }; } },
    './demo': { session: () => null }, './initialization': { forSession: () => null }, './settings': { get: () => ({ agent: 'claude' }), command: (agent) => [agent] },
    './codex-sessions': { has: () => false, register() {} }, './config': { EVENTS: '/e', PTY_BRIDGE: 'b.py', PYTHON: 'python3' },
    './backend': { rf: async (args) => { calls.push(args); return args[0] === 'history' ? recap : { ok: true, stdout: 'S-new\n' }; }, cleanEnv: () => ({}), lastLine: (x) => x },
    './state': { state: { runs: new Map(), project: '/p' }, send() {} }, './requirements': { listReqs: async () => [r], pushReqs() {} },
    './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post() {}, takeForOpen: () => '', envFor: () => ({}) }, './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, seen() {}, stopped() {} }, './codex-hooks': { flags: () => [], env: () => ({}) }, './claude-settings': { guardSpec: () => ({}), draftsFor: () => '/d', hooks: () => '{}' },
    './ui-names': { guide: () => '' }, './prompts': { develop: () => 'DEVELOP', following: () => 'FOLLOWING', intake: () => 'INTAKE', testing: () => 'TESTING' }, './session-token': T, './splits': { guide: '拆分的说明' },
  });
  await sessions.open({ id, cols: 80, rows: 24 });
  sessions.close(id);
  const args = spawned[0];
  return { prompt: args[args.length - 1], asked: JSON.parse(JSON.stringify(calls.filter((a) => a[0] === 'history'))) };
}

const GONE = '00000000-0000-0000-0000-00000000dead'; // 聊天记录找不到了，只能另开

test('a follow-up development session gets the last turns of the one before it', async () => {
  const { prompt, asked } = await open('R-1', { dev_sessions: [GONE], sessions: [GONE] });
  assert.equal(prompt, 'FOLLOWING\n\n上一个会话 S-old 最后 1 轮做了什么：\n<<<\n【1】用户：导出\n>>>');
  assert.deepEqual(asked, [['history', 'session', GONE, '--brief']]);
});

test('a test session opened again gets it too', async () => {
  const { prompt, asked } = await open('test:R-1', { test_sessions: [GONE] });
  assert.match(prompt, /^TESTING\n\n上一个会话 S-old/);
  assert.deepEqual(asked, [['history', 'session', GONE, '--brief']]);
});

test('the first development session, the one right after intake, and a first test session get none', async () => {
  for (const [id, over] of [['R-1', {}], ['R-1', { intake: [GONE], sessions: [GONE] }], ['test:R-1', {}]]) {
    const { prompt, asked } = await open(id, over);
    assert.ok(['DEVELOP', 'TESTING'].includes(prompt), prompt);
    assert.deepEqual(asked, []);
  }
});

test('no record or a failure leaves the opening prompt as it was', async () => {
  for (const recap of [{ ok: true, stdout: '\n' }, { ok: false, stdout: '', stderr: '没有找到会话' }]) {
    const { prompt } = await open('R-1', { dev_sessions: [GONE], sessions: [GONE] }, recap);
    assert.equal(prompt, 'FOLLOWING');
  }
});
