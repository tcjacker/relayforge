const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { EventEmitter } = require('node:events');
const base = path.join(__dirname, '..', 'app', 'src', 'main');
const renderer = path.join(base, '..', 'renderer', 'scripts');
function load(name, mocks) {
  const file = path.join(base, name + '.js');
  const localRequire = createRequire(file);
  const context = vm.createContext({ require: (id) => Object.hasOwn(mocks, id) ? mocks[id] : localRequire(id), module: { exports: {} }, process, Buffer, clearTimeout });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
  return context.module.exports;
}
test('settings survive reload, reject invalid tools, and preserve other keys', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-settings-'));
  try {
    const mocks = { './config': { RF_HOME: dir, AGENT: ['claude'] } };
    let settings = load('settings', mocks);
    assert.equal(settings.get().agent, 'claude');
    fs.writeFileSync(path.join(dir, 'settings.json'), '{"other":42}');
    settings.save({ agent: 'codex' });
    settings = load('settings', mocks);
    assert.equal(settings.get().agent, 'codex');
    assert.equal(JSON.parse(fs.readFileSync(path.join(dir, 'settings.json'))).other, 42);
    assert.throws(() => settings.save({ agent: 'sh' }));
    assert.equal(settings.get().agent, 'codex');
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
test('how long cancelled requirements keep their branches: 30 days and 20 by default, saved for rf.py to read', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-settings-'));
  try {
    const settings = load('settings', { './config': { RF_HOME: dir, AGENT: ['claude'] } });
    assert.deepEqual({ ...settings.get().droppedBranches }, { keepDays: 30, keepMax: 20 });
    settings.save({ agent: 'claude' }); // 只改工具的旧调用：不动这两项
    assert.equal(JSON.parse(fs.readFileSync(path.join(dir, 'settings.json'))).droppedBranches, undefined);
    settings.save({ agent: 'claude', droppedBranches: { keepDays: 7, keepMax: 5 } });
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir, 'settings.json'))).droppedBranches, { keepDays: 7, keepMax: 5 });
    assert.deepEqual({ ...settings.get().droppedBranches }, { keepDays: 7, keepMax: 5 });
    for (const bad of [{ keepDays: 0, keepMax: 5 }, { keepDays: 1.5, keepMax: 5 }, { keepDays: 7, keepMax: 1001 }, { keepDays: 'x', keepMax: 5 }]) {
      assert.throws(() => settings.save({ agent: 'claude', droppedBranches: bad }), /已取消需求的分支/);
    }
    assert.deepEqual({ ...settings.get().droppedBranches }, { keepDays: 7, keepMax: 5 }, 'a refused value changes nothing');
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
function sessionHarness(agent, reqs = [], known = {}, repair = null, example = null, language = 'en') {
  const launches = [], sent = [], registered = [];
  const state = { project: '/project', runs: new Map() };
  const child = () => { const p = new EventEmitter(); p.stdin = new EventEmitter(); p.stdout = new EventEmitter(); p.stderr = new EventEmitter(); p.stdio = [null, null, null, new EventEmitter()]; p.kill = () => {}; return p; };
  const sessions = load('sessions', {
    child_process: { execFile: (_file, _args, _options, done) => done(null, ''), spawn: (command, args, options) => { launches.push({ command, args, options }); return child(); } },
    './config': { EVENTS: '/events', PTY_BRIDGE: '/bridge.py', PYTHON: 'python3' },
    './demo': { session: () => example, info: () => null, transcript: () => 'offline sample transcript' }, './initialization': { forSession: (id) => id === 'repair' ? repair : null, prompt: () => 'diagnose initialization error and retry' }, './settings': { get: () => ({ agent, language }), command: (a) => [a] },
    './codex-sessions': { has: (id) => !!known[id], find: (id) => known[id] && '/transcript', nativeId: () => 'native-codex-id', register: (...a) => registered.push(a) },
    './backend': { rf: async (args) => ({ stdout: args[0] === 'map' ? '{"session_id":"map-id"}' : '{}' }), cleanEnv: () => ({}), lastLine: String },
    './state': { state, send: (channel, payload) => sent.push({ channel, payload }) },
    './requirements': { listReqs: async () => reqs, pushReqs() {} },
    './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post() {}, takeForOpen: () => '', envFor: () => ({}) }, './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, seen() {}, stopped() {} }, './codex-hooks': { flags: () => ['-c', 'hooks.Stop=[]'], env: (spec) => ({ RELAYFORGE_GUARD: JSON.stringify(spec) }) }, './claude-settings': { guardSpec: (o) => ({ kind: o.drafts ? 'intake' : null }), draftsFor: () => '/drafts', hooks: () => '{}' },
    './splits': { guide: 'split prompt' },
    './prompts': { mapEdit: () => 'map prompt', intake: () => 'intake prompt', develop: () => 'development prompt', following: () => 'follow-up prompt' },
    './session-token': require(path.join(base, 'session-token')),
  });
  return { sessions, launches, state, registered, sent };
}
test('new Codex requirement gets its prompt, images, sandbox and tracking id', async () => {
  const h = sessionHarness('codex', [{ id: 'R-test', title: 'Test', state: 'draft', worktree: '/work', sessions: [], intake: [] }]);
  await h.sessions.open({ id: 'R-test', cols: 80, rows: 24, images: ['/image.png'] });
  const launch = h.launches[0];
  // 立项中的 Codex 会话：不再用只读沙箱（连不上应用的 socket，设计稿、记录交不给应用），改为在草稿目录里用可写沙箱加联网，
  // 项目照样读得到、改不了；rf.py 按 CLAUDE_PROJECT_DIR 找项目
  assert.deepEqual(Array.from(launch.args.slice(0, 4)), ['/bridge.py', 'codex', '--sandbox', 'workspace-write']);
  assert.ok(launch.args.includes('sandbox_workspace_write.network_access=true') && !launch.args.some((a) => String(a).startsWith('approvals_reviewer=')));
  assert.equal(launch.options.env.CLAUDE_PROJECT_DIR, '/work');
  assert.match(launch.args.find((a) => String(a).startsWith('developer_instructions=')), /草稿目录（\/drafts）[\s\S]*项目在 \/work，只读/);
  assert.ok(launch.args.includes('--image'));
  assert.match(launch.args.at(-1), /intake prompt/);
  assert.match(launch.args.at(-1), /RelayForge 会话标识/);
  assert.equal(launch.options.cwd, '/drafts'); // 进程在草稿目录里跑
  assert.equal(launch.options.env.RELAYFORGE_SESSION_ID, h.registered[0][0]);
  assert.ok(launch.args.includes('hooks.Stop=[]')); // Codex 也带上钩子（codex-hooks.js）
  assert.equal(launch.options.env.RELAYFORGE_GUARD, '{"kind":"intake"}'); // 立项中：这个会话不能改哪些地方放在环境变量里
});
test('new daily chats use the selected tool and active sessions stay running', async () => {
  for (const agent of ['claude', 'codex']) {
    const h = sessionHarness(agent);
    await h.sessions.open({ id: 'session:new', blank: true, cols: 80, rows: 24 });
    assert.equal(h.launches[0].args[1], agent);
    await h.sessions.open({ id: 'session:new', blank: true });
    assert.equal(h.launches.length, 1);
  }
});
test('existing Codex sessions resume in Codex after changing the default to Claude', async () => {
  const h = sessionHarness('claude', [], { old: true });
  await h.sessions.open({ id: 'session:old' });
  assert.deepEqual(Array.from(h.launches[0].args.slice(0, 4)), ['/bridge.py', 'codex', 'resume', 'native-codex-id']);
  assert.equal(h.registered.length, 0);
});
test('Codex tracking discovers the native session id and reloads it for resume', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-codex-'));
  const oldHome = process.env.CODEX_HOME;
  process.env.CODEX_HOME = path.join(dir, 'codex');
  try {
    const mocks = { './config': { RF_HOME: dir } };
    let tracking = load('codex-sessions', mocks);
    tracking.register('app-id', '/work', 'Test', '/project', true);
    const sessions = path.join(process.env.CODEX_HOME, 'sessions', '2026', '11', '16');
    fs.mkdirSync(sessions, { recursive: true });
    const transcript = path.join(sessions, 'rollout-native-id.jsonl');
    fs.writeFileSync(transcript, JSON.stringify({ type: 'session_meta', payload: { id: 'native-id', cwd: '/work' } }) + '\n' + JSON.stringify({ text: 'RelayForge 会话标识：app-id' }) + '\n');
    assert.equal(tracking.find('app-id'), transcript);
    tracking = load('codex-sessions', mocks);
    assert.equal(tracking.nativeId('app-id'), 'native-id');
    assert.equal(tracking.chats('/project')[0].session_id, 'app-id');
    assert.equal(tracking.chats('/other').length, 0);

    tracking.register('app-server-id', '/work', '后台测试', '/project', false);
    const serverTranscript = path.join(sessions, 'rollout-server-native-id.jsonl');
    fs.writeFileSync(serverTranscript, JSON.stringify({ type: 'session_meta', payload: { id: 'server-native-id', cwd: '/work' } }) + '\n');
    tracking.bindThread('app-server-id', { id: 'server-native-id', path: serverTranscript });
    tracking = load('codex-sessions', mocks);
    assert.equal(tracking.nativeId('app-server-id'), 'server-native-id');
    assert.equal(tracking.find('app-server-id'), serverTranscript);
  } finally {
    if (oldHome === undefined) delete process.env.CODEX_HOME; else process.env.CODEX_HOME = oldHome;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('AI repair sessions receive diagnostic instructions with either selected CLI', async () => {
  for (const agent of ['claude', 'codex']) {
    const h = sessionHarness(agent, [], {}, { root: '/broken-project' });
    await h.sessions.open({ id: 'session:repair', blank: true });
    assert.equal(h.launches[0].args[1], agent);
    assert.match(h.launches[0].args.at(-1), /diagnose initialization error and retry/);
    assert.equal(h.launches[0].options.cwd, '/broken-project');
  }
});

test('opening demo records sends sample transcript and progress without spawning a model', async () => {
  const h = sessionHarness('codex', [], {}, null, { root: '/demo', sessionId: 'sample-id' });
  await h.sessions.open({ id: 'session:sample-id', blank: true });
  assert.equal(h.launches.length, 0);
  assert.ok(h.sent.some((p) => p.channel === 'term:data' && p.payload.data === 'offline sample transcript'));
  assert.ok(h.sent.some((p) => p.channel === 'state' && p.payload.demo === true));
});

test('language defaults to English and survives old settings clients and reloads', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-settings-language-'));
  const mocks = { './config': { RF_HOME: dir, AGENT: ['claude'] } };
  try {
    const settings = load('settings', mocks);
    assert.equal(settings.get().language, 'en');
    assert.equal(settings.save({ agent: 'claude', language: 'zh-CN' }).language, 'zh-CN');
    assert.equal(settings.save({ agent: 'codex' }).language, 'zh-CN');
    assert.throws(() => settings.save({ agent: 'claude', language: 'fr' }));
    assert.equal(load('settings', mocks).language(), 'zh-CN');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('both session CLIs get the selected response language when opening or resuming', async () => {
  for (const [language, expected] of [['en', /Use English/], ['zh-CN', /使用简体中文/]]) {
    for (const agent of ['claude', 'codex']) {
      const known = agent === 'codex' ? { current: true } : {};
      const h = sessionHarness(agent, [], known, null, null, language);
      await h.sessions.open({ id: 'session:current', blank: !known.current });
      const launch = h.launches[0];
      assert.equal(launch.options.env.RELAYFORGE_LANGUAGE, language);
      const guide = agent === 'claude' ? launch.args[launch.args.indexOf('--append-system-prompt') + 1]
        : JSON.parse(launch.args.find((arg) => arg.startsWith('developer_instructions=')).split('=').slice(1).join('='));
      assert.match(guide, expected);
    }
  }
});

test('English test labels preserve the canonical category in saves', () => {
  const context = vm.createContext({ location: { search: '?language=en' }, URLSearchParams });
  for (const file of ['html-text', 'tests-model']) {
    vm.runInContext(fs.readFileSync(path.join(renderer, file + '.js'), 'utf8'), context);
  }
  const model = context.TestsModel;
  const entry = { n: 1, name: 'Valid input', cat: '正常', input: [] };
  assert.equal(model.categoryName(entry.cat), 'Normal');
  assert.equal(model.caseGroups([entry], 'all')[0].cat, 'Normal');
  assert.equal(model.payload([entry]).cases[0].cat, '正常');
  assert.equal(model.payload([{ ...entry, cat: '' }]).cases[0].cat, '正常');
});
