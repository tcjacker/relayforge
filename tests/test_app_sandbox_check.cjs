// 开 Claude Code 会话之前确认沙箱会生效（sandbox-check.js）：版本太旧、读不出版本、有设置把沙箱关掉或变弱，都不开会话并说明原因；
// 开了以后会话里的 rf.py 发现自己不在沙箱里，应用在它的终端里提醒（attention.js）。
const test = require('node:test');
const assert = require('node:assert/strict');
const locale = require('../app/src/main/locale');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const main = path.join(__dirname, '..', 'app', 'src', 'main');
const SC = require(path.join(main, 'sandbox-check'));

function place(t) {
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-sbc-')));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const home = path.join(dir, 'home'), managed = path.join(dir, 'managed'), root = path.join(dir, 'project');
  for (const d of [home, managed, root]) fs.mkdirSync(d, { recursive: true });
  // 假的 claude：只会报版本
  const cli = (version) => { const f = path.join(dir, 'claude-' + version); fs.writeFileSync(f, `#!/bin/sh\necho "${version} (Claude Code)"\n`, { mode: 0o755 }); return f; };
  const write = (file, data) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, JSON.stringify(data)); };
  return { dir, home, managed, root, cli, write, where: { home, managed } };
}

test('versions compare by number', () => {
  assert.equal(SC.newer('2.1.330', '2.1.330'), true);
  assert.equal(SC.newer('2.1.331', '2.1.330'), true);
  assert.equal(SC.newer('2.2.0', '2.1.330'), true);
  assert.equal(SC.newer('2.1.99', '2.1.330'), false);
  assert.equal(SC.newer('1.9.999', '2.1.330'), false);
});

test('a new enough Claude Code with nothing weakening the sandbox may open', { skip: process.platform === 'win32' }, async (t) => {
  const p = place(t);
  const ok = await SC.check(p.cli(SC.MIN_CLAUDE), p.root, p.where);
  assert.deepEqual([ok.ok, ok.version], [true, SC.MIN_CLAUDE]);
});

test('too old, or no version at all, does not open and says why', { skip: process.platform === 'win32' }, async (t) => {
  const p = place(t);
  const old = await SC.check(p.cli('2.0.5'), p.root, p.where);
  assert.equal(old.ok, false);
  assert.match(old.reason, /2\.0\.5 太旧了/);
  assert.match(old.reason, /更新 CLI/);
  const missing = await SC.check(path.join(p.dir, 'no-such-claude'), p.root, p.where);
  assert.equal(missing.ok, false);
  assert.match(missing.reason, /读不出 Claude Code 的版本/);
});

test('settings that switch the sandbox off or weaken it do not open, and name the file', { skip: process.platform === 'win32' }, async (t) => {
  const p = place(t);
  const claude = p.cli(SC.MIN_CLAUDE);
  // 你自己的设置里关掉 enabled：压不过我们传进去的，不算
  p.write(path.join(p.home, '.claude', 'settings.json'), { sandbox: { enabled: false } });
  assert.equal((await SC.check(claude, p.root, p.where)).ok, true);
  // 管理员的设置能压过我们的
  p.write(path.join(p.managed, 'managed-settings.json'), { sandbox: { enabled: false } });
  let out = await SC.check(claude, p.root, p.where);
  assert.equal(out.ok, false);
  assert.match(out.reason, /managed-settings\.json：关掉了沙箱/);
  fs.rmSync(path.join(p.managed, 'managed-settings.json'));
  p.write(path.join(p.managed, 'managed-settings.d', 'corp.json'), { sandbox: { allowUnsandboxedCommands: true } });
  assert.match((await SC.check(claude, p.root, p.where)).reason, /允许命令跳出沙箱/);
  fs.rmSync(path.join(p.managed, 'managed-settings.d'), { recursive: true });
  // 你自己的或者项目里的：关掉文件隔离、命令在沙箱外跑、放开 Apple Events
  p.write(path.join(p.home, '.claude', 'settings.json'), { sandbox: { filesystem: { disabled: true } } });
  assert.match((await SC.check(claude, p.root, p.where)).reason, /关掉了文件隔离/);
  p.write(path.join(p.home, '.claude', 'settings.json'), {});
  p.write(path.join(p.root, '.claude', 'settings.local.json'), { sandbox: { excludedCommands: ['python3 *'] } });
  assert.match((await SC.check(claude, p.root, p.where)).reason, /沙箱外运行.*python3 \*/);
  p.write(path.join(p.root, '.claude', 'settings.local.json'), { sandbox: { allowAppleEvents: true } });
  assert.match((await SC.check(claude, p.root, p.where)).reason, /Apple Events/);
  p.write(path.join(p.root, '.claude', 'settings.local.json'), {});
  assert.equal((await SC.check(claude, p.root, p.where)).ok, true);
});

function loadWith(name, mocks) {
  const context = vm.createContext({ require: (id) => id === './locale' ? locale : (Object.hasOwn(mocks, id) ? mocks[id] : require(id)), module: { exports: {} }, process, Buffer, setTimeout, clearTimeout, console });
  vm.runInContext(fs.readFileSync(path.join(main, name + '.js'), 'utf8'), context);
  return context.module.exports;
}

test('a session whose sandbox would not take is not started; the reason shows in its terminal', async () => {
  const spawned = [], shown = [];
  const r = { id: 'R-1', title: '导出', state: 'open', worktree: fs.mkdtempSync(path.join(os.tmpdir(), 'rf-wt-')), sessions: [], intake: [], dev_sessions: [] };
  const sessions = loadWith('sessions', {
    child_process: { spawn(...a) { spawned.push(a); throw new Error('must not spawn'); } },
    './demo': { session: () => null }, './initialization': { forSession: () => null }, './settings': { get: () => ({ agent: 'claude' }), command: () => ['/old/claude'] },
    './codex-sessions': { has: () => false }, './config': { EVENTS: '/e', PTY_BRIDGE: 'b.py', PYTHON: 'python3' },
    './backend': { uiText: locale.uiText,  rf: async () => ({ ok: true, stdout: 'S-1\n' }), cleanEnv: () => ({}), lastLine: (x) => x },
    './state': { state: { runs: new Map(), project: '/p' }, send: (channel, payload) => shown.push([channel, payload]) }, './requirements': { listReqs: async () => [r], pushReqs() {} },
    './sandbox-check': { check: async (file) => ({ ok: false, reason: `Claude Code 1.0.0 太旧了（${file}）\n请更新` }) },
    './inbox': { post() {}, takeForOpen: () => '', envFor: () => ({}) }, './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, seen() {}, stopped() {}, mergeProgress() {} },
    './codex-hooks': { flags: () => [], env: () => ({}) }, './claude-settings': { guardSpec: () => ({}), draftsFor: () => '/d', hooks: () => '{}' },
    './prompts': { develop: () => 'go', following: () => 'go', intake: () => 'go' }, './session-token': require(path.join(main, 'session-token')), './splits': { guide: '' },
  });
  await sessions.open({ id: 'R-1', cols: 80, rows: 24 });
  assert.equal(spawned.length, 0);
  const text = shown.filter(([c]) => c === 'term:data').map(([, p]) => p.data).join('');
  assert.match(text, /1\.0\.0 太旧了（\/old\/claude）\r\n请更新/);
});

test('when a session reports it runs outside the sandbox, its terminal says so once', () => {
  const sent = [];
  const runs = new Map([['R-1', { id: 'R-1', sessionId: 'sid-1' }]]);
  const events = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-ev-')), 'events.jsonl');
  const attention = loadWith('attention', {
    electron: { app: { setBadgeCount() {}, dock: null }, Notification: { isSupported: () => false } },
    './config': { BUNDLED: '/b/rf.py', EVENTS: events, PYTHON: 'python3' },
    './state': { state: { runs, win: null }, send: (channel, payload) => sent.push([channel, payload]) },
  });
  fs.writeFileSync(events, [{ session_id: 'sid-1', status: 'unsandboxed' }, { session_id: 'sid-1', status: 'unsandboxed' }, { session_id: 'other', status: 'unsandboxed' }]
    .map((e) => JSON.stringify(e)).join('\n') + '\n');
  attention.readEvents();
  const warned = sent.filter(([c, p]) => c === 'term:data' && /在系统的沙箱外运行了/.test(p.data));
  assert.equal(warned.length, 1);
  assert.equal(warned[0][1].id, 'R-1');
  assert.equal(runs.get('R-1').hooked, undefined); // 这不是钩子报的状态
});
