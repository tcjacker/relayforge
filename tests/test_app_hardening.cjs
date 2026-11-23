// 安全补强（应用这一侧）：会话令牌的签发和验证（含和 rf.py 真的走一遍 unix socket）、每种会话拿到的种类、
// 主进程里调用「只由用户触发的命令」的地方都走带应用标记的 rf()、每种会话都禁止这些命令、构建命令的确认框和同意。
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const locale = require('../app/src/main/locale');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { execFile } = require('node:child_process');
const repo = path.join(__dirname, '..');
const main = path.join(repo, 'app', 'src', 'main');
const T = require(path.join(main, 'session-token'));

function loadWith(name, mocks) {
  const localRequire = createRequire(path.join(main, name + '.js'));
  const context = vm.createContext({ require: (id) => (Object.hasOwn(mocks, id) ? mocks[id] : localRequire(id)), module: { exports: {} }, process, Buffer, setTimeout, clearTimeout, console });
  vm.runInContext(fs.readFileSync(path.join(main, name + '.js'), 'utf8'), context);
  return context.module.exports;
}

// ---------- 会话令牌 ----------

test('a session token carries its claims, and a tampered or foreign one is refused', () => {
  const a = T.issuer(), b = T.issuer();
  const { token, claims } = a.issue({ session: 's-1', kind: 'test', owner: 'R-1' });
  assert.deepEqual([claims.sid, claims.kind, claims.owner, typeof claims.iat, claims.nonce.length], ['s-1', 'test', 'R-1', 'number', 24]);
  assert.equal(a.check(token).kind, 'test');
  assert.equal(b.check(token), null); // 另一个应用（另一把密钥）
  const [body, sig] = token.split('.');
  const forged = Buffer.from(JSON.stringify({ ...claims, kind: 'develop' })).toString('base64url') + '.' + sig;
  assert.equal(a.check(forged), null);
  assert.equal(a.check(body), null);
  assert.equal(a.issue({ session: 'x', kind: 'root' }).claims.kind, 'chat'); // 不认识的种类按最低的算
  // 验证的回答：签名对上了，说出发给了哪个进程；没 bind 时 pid 是空的
  assert.deepEqual(a.answer(JSON.stringify({ op: 'verify', token })), { ok: true, claims, pid: null, pids: [] });
  a.bind(token, 4242);
  assert.equal(a.answer(JSON.stringify({ op: 'verify', token })).pid, 4242);
  assert.equal(a.answer(JSON.stringify({ op: 'verify', token: forged })).ok, false);
  assert.deepEqual(a.answer('not json'), { ok: false, error: 'bad request' });
  assert.equal(a.answer(JSON.stringify({ op: 'sign', claims })).ok, false); // 只有验证，没有「替我签一个」
  a.unbind(token);
  assert.equal(a.answer(JSON.stringify({ op: 'verify', token })).pid, null);
});

test('rf.py asks the app over its unix socket: the right kind under the session, the lowest anywhere else', async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-sig-'));
  const home = path.join(tmp, 'home');
  const app = T.issuer();
  try {
    const sock = await app.listen(path.join(home, 'apps', process.pid + '.sock'));
    assert.equal((fs.statSync(path.dirname(sock)).mode & 0o777).toString(8), '700');
    fs.writeFileSync(path.join(home, 'apps', process.pid + '.json'), JSON.stringify({ pid: process.pid, socket: sock }), { mode: 0o600 });
    // 异步地起 python：验证的服务就在这个进程里，同步等会把它卡住
    const ask = (token) => new Promise((resolve, reject) => execFile('python3', ['-c', 'import json, sys; sys.path.insert(0, sys.argv[1]); from relayforge.core import identity; print(json.dumps(identity.caller()))', path.join(repo, 'backend')],
      { env: { ...process.env, RELAYFORGE_HOME: home, [T.SIG_ENV]: token }, encoding: 'utf8' }, (error, stdout, stderr) => (error ? reject(new Error(stderr)) : resolve(JSON.parse(stdout)))));
    const bound = app.issue({ session: 'test-1', kind: 'test', owner: 'R-1' }).token;
    app.bind(bound, process.pid); // 这个测试进程就是「会话进程」：python 是它的子进程
    assert.deepEqual(await ask(bound), { kind: 'test', owner: 'R-1', session: 'test-1', verified: true, why: '' });
    const other = app.issue({ session: 'test-2', kind: 'test', owner: 'R-1' }).token;
    app.bind(other, 999999); // 发给别的会话进程的令牌：拿到这里用不上
    assert.match((await ask(other)).why, /不是发给这个会话的/);
    const foreign = await ask(T.issuer().issue({ session: 'x', kind: 'test', owner: 'R-1' }).token);
    assert.deepEqual([foreign.kind, foreign.verified], ['chat', false]);
    assert.match((await ask('')).why, /没有会话令牌/);
  } finally { app.close(); fs.rmSync(tmp, { recursive: true, force: true }); }
});

test('each kind of session gets its own kind and owner in the token', () => {
  const sessions = loadWith('sessions', {
    child_process: { spawn() { throw new Error('not here'); } }, './config': {}, './backend': { uiText: locale.uiText }, './state': { state: { runs: new Map() }, send() {} }, './requirements': {},
    './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post() {}, takeForOpen: () => '', envFor: () => ({}) }, './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {} }, './codex-hooks': { flags: () => [], env: () => ({}) }, './claude-settings': {}, './prompts': {}, './settings': {}, './codex-sessions': {}, './initialization': {}, './demo': {}, './session-token': T, './splits': {},
  });
  const req = { id: 'R-1', state: 'open', worktree: '/w' };
  const kinds = [
    [{ id: 'map-edit' }, { kind: 'map', owner: null }],
    [{ id: 'session:abc' }, { kind: 'chat', owner: null }],
    [{ id: 'probe:I-003', probing: true }, { kind: 'probe', owner: 'I-003' }],
    [{ id: 'test:R-1', req, tester: true }, { kind: 'test', owner: 'R-1' }],
    [{ id: 'fix:F-002' }, { kind: 'fix', owner: 'F-002' }],
    [{ id: 'R-1', req: { ...req, state: 'draft' }, drafting: true }, { kind: 'intake', owner: 'R-1' }],
    [{ id: 'R-1', req }, { kind: 'develop', owner: 'R-1' }],
    [{ id: 'R-1', req: { ...req, state: 'done', worktree: null } }, { kind: 'chat', owner: null }],
  ];
  for (const [input, want] of kinds) assert.deepEqual({ ...sessions.identity(input) }, want, input.id);
});

test('the session gets the signed token and the app itself never passes one on', async () => {
  const spawned = [];
  const r = { id: 'R-1', title: '导出', state: 'open', worktree: fs.mkdtempSync(path.join(os.tmpdir(), 'rf-wt-')), sessions: [], intake: [], dev_sessions: [] };
  const sessions = loadWith('sessions', {
    child_process: { spawn(cmd, args, opts) { spawned.push(opts); const ee = () => ({ on() {}, write() {} }); return { pid: 31337, stdin: ee(), stdout: ee(), stderr: ee(), stdio: [null, null, null, ee()], on() {}, kill() {} }; } },
    './demo': { session: () => null }, './initialization': { forSession: () => null }, './settings': { get: () => ({ agent: 'claude' }), command: () => ['claude'] },
    './codex-sessions': { has: () => false }, './config': { EVENTS: '/e', PTY_BRIDGE: 'b.py', PYTHON: 'python3' },
    './backend': { uiText: locale.uiText,  rf: async () => ({ ok: true, stdout: 'S-1\n' }), cleanEnv: () => ({}), lastLine: (x) => x },
    './state': { state: { runs: new Map(), project: '/p' }, send() {} }, './requirements': { listReqs: async () => [r], pushReqs() {} },
    './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post() {}, takeForOpen: () => '', envFor: () => ({}) }, './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, seen() {}, stopped() {} }, './codex-hooks': { flags: () => [], env: () => ({}) }, './claude-settings': { guardSpec: () => ({}), draftsFor: () => '/d', hooks: () => '{}' },
    './prompts': { develop: () => 'go', following: () => 'go', intake: () => 'go' }, './session-token': T, './splits': { guide: '拆分的说明' },
  });
  await sessions.open({ id: 'R-1', cols: 80, rows: 24 });
  const env = spawned[0].env;
  const claims = T.shared.check(env[T.SIG_ENV]);
  assert.deepEqual([claims.kind, claims.owner, claims.sid], ['develop', 'R-1', env.RELAYFORGE_SESSION_ID]);
  assert.equal(T.shared.answer(JSON.stringify({ op: 'verify', token: env[T.SIG_ENV] })).pid, 31337); // 只在这个会话进程下面算数
  // 应用自己调用 rf.py（backend.js）：不带会话令牌，带应用的标记
  const calls = [];
  const backend = loadWith('backend', {
    child_process: { execFile: (cmd, args, opts, cb) => { calls.push({ cmd, args, env: opts.env }); cb(null, '', ''); return { stdin: { on() {}, end() {} } }; } },
    './config': { BUNDLED: '/app/backend/rf.py', PYTHON: 'python3' }, './state': { state: { project: '/p' } }, './settings': { get: () => ({ agent: 'claude' }), launch: () => ({ file: 'claude' }) },
    './app-token': { envFor: () => ({ RELAYFORGE_APP_TOKEN: 'the-marker' }) }, './cli-paths': { withCliPath: (env) => env },
  });
  await backend.rf(['req', 'done', 'R-1']);
  assert.equal(calls[0].env.RELAYFORGE_APP_TOKEN, 'the-marker');
  assert.equal(calls[0].env.RELAYFORGE_SESSION_SIG, '');
  process.env.RELAYFORGE_SESSION_SIG = 'leaked';
  try { assert.equal(backend.cleanEnv().RELAYFORGE_SESSION_SIG, undefined); } finally { delete process.env.RELAYFORGE_SESSION_SIG; }
});

// 合并 main 以后：需求会话按需求里配置的 CLI 和模型启动，同时照样带会话令牌（立项、开发、测试三种都看）
test('requirement sessions use the configured CLI and model and still carry the signed token', async () => {
  const wt = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-wt-'));
  const choices = { intake: { cli: 'codex', model: 'm-intake' }, development: { cli: 'claude', model: 'm-dev' } };
  const cases = [
    [{ id: 'R-1', state: 'draft' }, 'R-1', 'codex', 'm-intake', 'intake'],
    [{ id: 'R-1', state: 'open' }, 'R-1', 'claude', 'm-dev', 'develop'],
    [{ id: 'R-1', state: 'open', stage: 'test' }, 'test:R-1', 'claude', 'm-dev', 'test'],
  ];
  for (const [over, id, cli, model, kind] of cases) {
    const spawned = [];
    const r = { title: '导出', worktree: wt, sessions: [], intake: [], dev_sessions: [], test_sessions: [], session_choices: choices, ...over };
    const sessions = loadWith('sessions', {
      child_process: { execFile: (_file, _args, _options, done) => done(null, ''), spawn(cmd, args, opts) { spawned.push({ args, env: opts.env }); const ee = () => ({ on() {}, write() {} }); return { pid: 4242, stdin: ee(), stdout: ee(), stderr: ee(), stdio: [null, null, null, ee()], on() {}, kill() {} }; } },
      './demo': { session: () => null }, './initialization': { forSession: () => null }, './settings': { get: () => ({ agent: 'claude' }), command: (agent) => [agent] },
      './codex-sessions': { has: () => false, register() {} }, './config': { EVENTS: '/e', PTY_BRIDGE: 'b.py', PYTHON: 'python3' },
      './backend': { uiText: locale.uiText,  rf: async () => ({ ok: true, stdout: 'S-1\n' }), cleanEnv: () => ({}), lastLine: (x) => x },
      './state': { state: { runs: new Map(), project: '/p' }, send() {} }, './requirements': { listReqs: async () => [r], pushReqs() {} },
      './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post() {}, takeForOpen: () => '', envFor: () => ({}) }, './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, seen() {}, stopped() {} }, './codex-hooks': { flags: () => [], env: () => ({}) }, './claude-settings': { guardSpec: () => ({}), draftsFor: () => '/d', hooks: () => '{}' },
      './prompts': { develop: () => 'go', following: () => 'go', intake: () => 'go', testing: () => 'go' }, './session-token': T, './splits': { guide: '拆分的说明' },
    });
    await sessions.open({ id, cols: 80, rows: 24 });
    assert.equal(spawned.length, 1, id + ' ' + kind);
    const { args, env } = spawned[0];
    assert.equal(args[1], cli, kind);
    assert.equal(args[args.indexOf('--model') + 1], model, kind);
    const claims = T.shared.check(env[T.SIG_ENV]);
    assert.deepEqual([claims.kind, claims.owner, claims.sid], [kind, 'R-1', env.RELAYFORGE_SESSION_ID]);
    sessions.close(id);
  }
});

// 数据外置的项目：约定不在仓库的 AGENTS.md 里，在本机记录的 CONVENTIONS.md 里；应用开会话时交给会话（Claude Code 和 Codex 都是）
test('sessions get the project rules from the local records, not from AGENTS.md', async () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-proj-'));
  const store = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-store-'));
  fs.writeFileSync(path.join(store, 'CONVENTIONS.md'), '## 项目事实的写法\n- 规则一\n');
  fs.symlinkSync(store, path.join(project, '.relayforge'));
  const wt = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-wt-'));
  for (const cli of ['claude', 'codex']) {
    const spawned = [];
    const r = { id: 'R-1', title: '导出', state: 'open', worktree: wt, sessions: [], intake: [], dev_sessions: [], test_sessions: [], session_choices: { development: { cli, model: 'm' } } };
    const sessions = loadWith('sessions', {
      child_process: { execFile: (_file, _args, _options, done) => done(null, '  --no-daemon\n'), spawn(cmd, args, opts) { spawned.push(args); const ee = () => ({ on() {}, write() {} }); return { pid: 4243, stdin: ee(), stdout: ee(), stderr: ee(), stdio: [null, null, null, ee()], on() {}, kill() {} }; } },
      './demo': { session: () => null }, './initialization': { forSession: () => null }, './settings': { get: () => ({ agent: 'claude' }), command: (agent) => [agent] },
      './codex-sessions': { has: () => false, register() {} }, './config': { EVENTS: '/e', PTY_BRIDGE: 'b.py', PYTHON: 'python3' },
      './backend': { uiText: locale.uiText,  rf: async () => ({ ok: true, stdout: 'S-1\n' }), cleanEnv: () => ({}), lastLine: (x) => x },
      './state': { state: { runs: new Map(), project }, send() {} }, './requirements': { listReqs: async () => [r], pushReqs() {} },
      './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post() {}, takeForOpen: () => '', envFor: () => ({}) }, './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, seen() {}, stopped() {} }, './codex-hooks': { flags: () => [], env: () => ({}) }, './claude-settings': { guardSpec: () => ({}), draftsFor: () => '/d', hooks: () => '{}' },
      './prompts': { develop: () => 'go', following: () => 'go', intake: () => 'go', testing: () => 'go' }, './session-token': T, './splits': { guide: '拆分的说明' },
    });
    await sessions.open({ id: 'R-1', cols: 80, rows: 24 });
    const args = spawned[0];
    const guide = cli === 'claude' ? args[args.indexOf('--append-system-prompt') + 1] : JSON.parse(args.find((a) => String(a).startsWith('developer_instructions=')).replace(/^developer_instructions=/, ''));
    assert.match(guide, /项目事实的写法[\s\S]*规则一/, cli);
    assert.equal(args.includes('--no-daemon'), cli === 'codex');
    sessions.close('R-1');
  }
});

// app-server 保留 CLI 自带参数，模型经线程 RPC 传入；会话起的进程和交互会话一样登记、这一轮结束后收拾。
test('a background Codex run keeps the configured CLI arguments and the chosen model, and its processes are recorded and reaped', async () => {
  const wt = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-wt-'));
  const r = { id: 'R-1', title: '导出', state: 'open', worktree: wt, sessions: [], intake: [], dev_sessions: [], test_sessions: [], session_choices: { development: { cli: 'codex', model: 'gpt-x' } } };
  class FakeChild {}
  const started = [], recorded = [], reaped = [], helped = [];
  const st = { runs: new Map(), project: '/p' };
  const sessions = loadWith('sessions', {
    child_process: { ChildProcess: FakeChild, execFile: (_file, args, _options, done) => { helped.push(args.join(' ')); done(null, args.includes('exec') ? '' : '  --no-daemon\n'); } },
    './headless': { start: (run, file, args, opts, sig, hooks) => { run.term = Object.assign(new FakeChild(), { pid: 99, kill() {} }); started.push({ file, args, hooks }); } },
    './session-procs': { record: (sid, where) => recorded.push([sid, where.pid]), reap: (sid) => reaped.push(sid), unrecord() {} },
    './demo': { session: () => null }, './initialization': { forSession: () => null }, './settings': { get: () => ({ agent: 'claude' }), command: (agent) => [agent, '--profile', 'work'] },
    './codex-sessions': { has: () => false, register() {} }, './config': { EVENTS: '/e', PTY_BRIDGE: 'b.py', PYTHON: 'python3' },
    './backend': { uiText: locale.uiText,  rf: async () => ({ ok: true, stdout: 'S-1\n' }), cleanEnv: () => ({}), lastLine: (x) => x },
    './state': { state: st, send() {} }, './requirements': { listReqs: async () => [r], pushReqs() {} },
    './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post() {}, takeForOpen: () => '', envFor: () => ({}) }, './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, seen() {}, stopped() {} }, './codex-hooks': { flags: () => ['-c', 'hooks.Stop=[]'], env: () => ({}) }, './claude-settings': { guardSpec: () => ({}), draftsFor: () => '/d', hooks: () => '{}' },
    './prompts': { develop: () => 'go', following: () => 'go', intake: () => 'go', testing: () => 'go' }, './session-token': T, './splits': { guide: '拆分的说明' },
  });
  await sessions.open({ id: 'R-1', headless: true });
  assert.equal(started.length, 1);
  const { file, args, hooks } = started[0];
  assert.equal(file, 'codex');
  assert.deepEqual([...args.slice(0, 5)], ['--profile', 'work', 'app-server', '--listen', 'stdio://']); // CLI 本身带的参数在子命令前面
  assert.equal(hooks.codex.model, 'gpt-x'); // 用户选的模型
  assert.ok(args.includes('hooks.Stop=[]') && args.some((arg) => arg.startsWith('developer_instructions=')));
  assert.ok(!args.includes('--no-daemon')); // app-server 本身就是独立进程
  // 越出沙箱的申请沿用用户的 Codex 审核设置（不固定 user，免得每条都要人点）；设置默认「自动」：never，根本不让申请越出沙箱；可写的会话打开联网（连得上应用的 socket）
  const values = [...args];
  assert.ok(values.includes('approval_policy="never"') && !values.some((x) => x.startsWith('approvals_reviewer=')), values.join(' '));
  assert.ok(values.includes('sandbox_workspace_write.network_access=true'));
  assert.ok(values.includes('features.multi_agent=false') && values.includes('features.multi_agent_v2=false'));
  assert.ok(!values.some((x) => /auto_review|danger-full-access|bypass/.test(x)));
  assert.deepEqual(helped, []);
  assert.equal(hooks.codex.prompt.split('\n')[0], 'go');
  const sid = recorded[0] && recorded[0][0];
  assert.deepEqual(JSON.parse(JSON.stringify(recorded)), [[sid, 99]]); // 应用崩溃了下次启动也收拾得到
  st.runs.delete('R-1'); // 这一轮正常结束（headless.js 先从开着的会话里去掉，再叫 ended）
  hooks.ended();
  await new Promise((done) => setTimeout(done, 3200)); // reapLater 等一会儿再收拾
  assert.deepEqual([...reaped], [sid]);
});

test('headless approval modes: auto never lets a session leave the sandbox, ask waits for you; Codex escalations are yours to approve', () => {
  const { approvalArgs, codexSandboxFlags } = loadWith('sessions', { './sandbox-check': {}, './inbox': {}, './attention': {}, './codex-hooks': {}, './claude-settings': {},
    './prompts': {}, './session-token': T, './splits': {}, './demo': {}, './initialization': {}, './settings': {}, './codex-sessions': {}, './config': {}, './backend': { uiText: locale.uiText },
    './state': { state: { runs: new Map() }, send() {} }, './requirements': {} });
  assert.deepEqual([...approvalArgs('claude', 'auto')], ['--permission-mode', 'auto']); // Claude Code 的 Auto：跳出沙箱照样不行（sandbox 设置）
  assert.deepEqual([...approvalArgs('claude', 'ask')], ['--permission-mode', 'manual']);
  assert.deepEqual([...approvalArgs('claude', undefined)], ['--permission-mode', 'auto']); // 默认自动
  assert.deepEqual([...approvalArgs('codex', 'auto')], ['-c', 'approval_policy="never"']);
  assert.deepEqual([...approvalArgs('codex', 'ask')], ['-c', 'approval_policy="on-request"']);
  // 接着以前的会话时不再问用哪个目录：应用选好的目录就是要用的（tui.resume_cwd，Codex 只认 current / session）
  // 不固定 approvals_reviewer：沿用用户自己的 Codex 设置（通常 auto_review），常规申请直接放行，只有极端敏感的才问人
  assert.deepEqual([...codexSandboxFlags(false)], ['-c', 'tui.resume_cwd="current"', '-c', 'sandbox_workspace_write.network_access=true']);
  assert.deepEqual([...codexSandboxFlags(true)], ['-c', 'tui.resume_cwd="current"']); // 只读的会话不联网
});

// ---------- 只由用户触发的命令 ----------

// trust.py 里的清单（USER_COMMANDS 和 APP_ONLY），按文件读出来，和 Python 那边是同一份
function userCommands() {
  const text = fs.readFileSync(path.join(repo, 'backend', 'relayforge', 'core', 'trust.py'), 'utf8');
  const block = (name) => text.slice(text.indexOf(name + ' = {'), text.indexOf('}', text.indexOf(name + ' = {')));
  const pairs = [];
  for (const name of ['USER_COMMANDS', 'APP_ONLY']) for (const m of block(name).matchAll(/\('([\w-]+)', (?:'([\w-]+)'|None)\)/g)) pairs.push([m[1], m[2] || null]);
  return pairs;
}

test('every user command the main process runs goes through rf(), which carries the app marker', () => {
  const pairs = userCommands();
  assert.ok(pairs.length >= 45, String(pairs.length));
  const files = fs.readdirSync(main).filter((f) => f.endsWith('.js'));
  const src = Object.fromEntries(files.map((f) => [f, fs.readFileSync(path.join(main, f), 'utf8')]));
  // 1. rf() 是带标记的那个入口；rfBundled 不带标记，只用于不是用户命令的
  assert.match(src['backend.js'], /const rf = \(args, root, agent, timeout, input\) => exec\(PYTHON, \[scriptFor\(state\.project\), \.\.\.args[^\n]*\n[^\n]*require\('\.\/app-token'\)\.envFor\(\)/);
  const user = new Set(pairs.map(([c, a]) => c + ' ' + (a || '')));
  const isUser = (c, a) => user.has(c + ' ' + (a || '')) || user.has(c + ' ');
  for (const [f, text] of Object.entries(src)) {
    for (const m of text.matchAll(/rfBundled\(\[\s*'([\w-]+)'(?:\s*,\s*'([\w-]+)')?/g)) assert.ok(!isUser(m[1], m[2]), `${f}: rfBundled ${m[1]} ${m[2] || ''}`);
    // 2. 别的地方不自己启动 rf.py（会话的 pty_bridge、钩子的 sweep、演示项目的构建脚本除外，它们都不是用户命令；
    //    session-procs.js 跑的是一段只查「这些进程在不在沙箱里」的 python，不是 rf.py）
    for (const m of text.matchAll(/(?:execFile|spawn|execFileSync|exec)\(\s*PYTHON\s*,\s*\[([^\]]*)/g)) {
      const ok = (f === 'backend.js') || (f === 'sessions.js' && /^\s*$|args/.test(m[1])) || (f === 'attention.js' && /'hook', 'sweep'/.test(m[1])) || (f === 'demo.js' && /build_demo/.test(m[1]))
        || (f === 'session-procs.js' && /^\s*'-c', SANDBOXED, \.\.\.inside\.map\(String\)\s*$/.test(m[1]));
      assert.ok(ok, `${f} starts python itself: ${m[1]}`);
    }
  }
  // 3. 各模块的 call() 都是 rf([前缀, ...args])；merge.js 的队伍也走 rf()
  assert.match(src['issues.js'], /async function call\(args\) \{\s*const out = await rf\(\['issue', \.\.\.args, '--json'\]\)/);
  assert.match(src['testsets.js'], /async function call\(args, timeout = TIMEOUTS\.read, input\) \{\s*const out = await rf\(\['test', \.\.\.args, '--json'\]/);
  assert.match(src['envs.js'], /async function call\(args, timeout = TIMEOUTS\.read, input\) \{\s*const out = await rf\(\['env', \.\.\.args, '--json'\]/);
  assert.match(src['merge.js'], /await rf\(\['req', 'done', item\.id, '--json'(, \.\.\.skip)?\], root\)/); // skip：你点了「跳过检查」时多一个 --skip-gate
  assert.match(src['merge.js'], /await rf\(\['issue', 'confirm', item\.issue, '--json'(, \.\.\.skip)?\], root\)/);
  // 4. 每一处写着用户命令的数组字面量，都在 rf( 或者走 rf 的 call( / args: 里
  const prefixOf = { 'issues.js': 'issue', 'testsets.js': 'test', 'envs.js': 'env' };
  let seen = 0;
  for (const [f, text] of Object.entries(src)) {
    for (const m of text.matchAll(/\[\s*'(req|issue|test|env|publish|summarize|archive|subagent)'\s*,\s*(?:'([\w-]+)'|[^\]]*)/g)) {
      if (!isUser(m[1], m[2])) continue;
      const before = text.slice(Math.max(0, m.index - 30), m.index);
      assert.ok(/rf\(\s*$|rf\(item\.args \|\| $|args:\s*$/.test(before), `${f}: ${m[0]} is not passed to rf()`);
      seen++;
    }
    const prefix = prefixOf[f];
    if (!prefix) continue;
    for (const m of text.matchAll(/call\(\[\s*'([\w-]+)'/g)) if (isUser(prefix, m[1])) seen++;
  }
  assert.ok(seen >= 15, String(seen));
});

test('every kind of session is denied the user commands; the app registration folder is readable but not writable', () => {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-deny-'));
  const { hooks } = require(path.join(main, 'claude-settings'));
  const { BUNDLED, RF_HOME } = require(path.join(main, 'config'));
  const kinds = { develop: hooks(null, false, false, true, false, false, false, { role: 'develop' }), test: hooks(null, false, false, false, false, false, false, { role: 'test', place: '/w' }),
    fix: hooks(null, false, false, true, false, false, false, { role: 'fix' }), chat: hooks(null, false), intake: hooks('/drafts/R-1', false), probe: hooks('/drafts/probe-I-1', true), merge: hooks(null, false, true) };
  for (const [name, text] of Object.entries(kinds)) {
    const { allow, deny } = JSON.parse(text).permissions;
    // 会话里的 rf.py 要读登记文件找到应用（验会话令牌、代写记录）；Claude Code 会把 Read 禁止套到沙箱上，所以不能禁读。写由沙箱挡住
    assert.ok(!deny.some((r) => r.startsWith('Read(') && r.includes('/apps')), name);
    assert.ok(JSON.parse(text).sandbox.filesystem.denyWrite.some((p) => p.endsWith('/apps')), name);
    for (const [cmd, action] of userCommands()) {
      for (const rf of [BUNDLED, '.relayforge/bin/rf.py']) {
        const rule = `Bash(python3 ${rf} ${cmd}${action ? ' ' + action : ''}:*)`;
        assert.ok(deny.includes(rule), `${name}: ${rule}`);
        assert.ok(!allow.includes(rule), `${name} allows ${rule}`);
      }
    }
    assert.ok(!deny.some((x) => / confirm:\*\)$/.test(x) && !x.includes(' issue ')), name); // rf-confirm 技能的 confirm / reject 留着（见报告）
  }
});

test('every kind of session runs its commands in the system sandbox and cannot write the app registration', () => {
  const project = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-sbx-')));
  require(path.join(main, 'state')).state.project = project;
  const { hooks } = require(path.join(main, 'claude-settings'));
  const apps = path.dirname(require(path.join(main, 'app-token')).file);
  const { RF_HOME } = require(path.join(main, 'config'));
  const kinds = { develop: hooks(null, false, false, true, false, false, false, { role: 'develop' }), test: hooks(null, false, false, false, false, false, false, { role: 'test', place: '/w' }),
    fix: hooks(null, false, false, true, false, false, false, { role: 'fix' }), chat: hooks(null, false), intake: hooks('/drafts/R-1', false), probe: hooks('/drafts/probe-I-1', true), merge: hooks(null, false, true) };
  for (const [name, text] of Object.entries(kinds)) {
    const { sandbox } = JSON.parse(text);
    assert.equal(sandbox.enabled, true, name);
    assert.equal(sandbox.allowUnsandboxedCommands, false, name); // 会话不能要求在沙箱外执行
    if (process.platform === 'darwin') assert.equal(sandbox.failIfUnavailable, true, name);
    assert.ok(sandbox.filesystem.allowWrite.includes(project), name); // 工作目录共用主目录的 .git，记录也在主目录
    assert.ok(sandbox.filesystem.denyWrite.includes(apps), name);
    assert.ok(sandbox.filesystem.denyWrite.some((p) => path.basename(p) === 'settings.json'), name);
    assert.ok(sandbox.filesystem.denyWrite.includes(`${project}/.relayforge/config.json`), name); // extractor 是应用在沙箱外执行的命令
    // 测试环境的本机部分：保密的值读不了也写不了，你的决定（同意执行的命令、允许带上保密值）能读不能写
    assert.ok(sandbox.filesystem.denyRead.some((p) => p.endsWith('/envs')) && sandbox.filesystem.denyWrite.some((p) => p.endsWith('/envs')), name);
    assert.ok(sandbox.filesystem.denyWrite.some((p) => p.endsWith('/env-consents')), name);
    assert.ok(!sandbox.filesystem.denyWrite.some((p) => p.endsWith('/env-state')), name);
    assert.equal(sandbox.network.allowUnixSockets.length, 1, name); // 只有应用的 socket
    // RF_HOME 只放行会话自己的 rf.py 要写的那几个子目录：应用的控制文件（批准、收件箱、进程记录、登记、合并队列……）一律写不了
    const { RF_HOME_WRITABLE } = require(path.join(main, 'claude-settings'));
    const rfReal = (() => { try { return fs.realpathSync(RF_HOME); } catch (_) { return RF_HOME; } })();
    assert.ok(!sandbox.filesystem.allowWrite.includes(rfReal) && !sandbox.filesystem.allowWrite.includes(RF_HOME), name);
    for (const p of sandbox.filesystem.allowWrite.filter((x) => x.startsWith(rfReal + path.sep))) assert.ok(RF_HOME_WRITABLE.includes(path.relative(rfReal, p)), `${name}: ${p}`);
    for (const d of ['approvals', 'inbox', 'run', 'keys']) assert.ok(sandbox.filesystem.denyWrite.includes(path.join(rfReal, d)), `${name}: ${d}`);
    for (const d of ['envs', 'approvals', 'inbox', 'keys']) assert.ok(sandbox.filesystem.denyRead.includes(path.join(rfReal, d)), `${name}: ${d}`);
    assert.ok(!sandbox.network.allowAllUnixSockets, name);
  }
  // Claude Code 对不上的规则一条都不能有：Write(路径) 永远对不上（只认 Edit）；* 和结尾的 :* 混用时 * 被当成普通字符
  for (const [name, text] of Object.entries(kinds)) {
    const { allow, ask, deny } = JSON.parse(text).permissions;
    for (const rule of [...allow, ...(ask || []), ...deny]) {
      assert.ok(!rule.startsWith('Write('), `${name}: ${rule}`);
      const body = (rule.match(/^Bash\((.*)\)$/) || [])[1];
      assert.ok(!(body && body.endsWith(':*') && body.slice(0, -2).includes('*')), `${name}: ${rule}`);
    }
  }
  // 测试会话：事实目录不再有 Edit 规则（会连带挡住沙箱里 rf.py test run 写结果），Edit 工具改事实目录由执行前的检查拦
  const fileGuard = JSON.parse(kinds.test).hooks.PreToolUse.find((h) => h.matcher === 'Edit|MultiEdit|Write|NotebookEdit');
  assert.ok(fileGuard && fileGuard.hooks[0].command.includes(`--facts '${project}/.relayforge'`), 'tester file guard');
});

// ---------- 构建命令的确认框 ----------

test('the build command shows in the same consent dialog and is agreed with what was shown', async () => {
  const M = require(path.join(repo, 'app', 'src', 'renderer', 'scripts', 'env-model.js'));
  const build = { kind: 'build', id: 'TS-003', set: 'TS-003', note: '测试集 TS-003「金额」的构建命令（每次运行前执行）', command: 'mvn -q compile', abs_dir: '/Users/me/bills', when: 'always', timeout: 600, print: 'build:' + 'a'.repeat(24) };
  const html = M.approvalHtml([build], null);
  assert.match(html, /运行测试集之前要先执行下面的测试集的构建命令/);
  assert.match(html, /mvn -q compile/);
  assert.match(html, /执行目录：<span class="mono">\/Users\/me\/bills<\/span>/);
  assert.match(html, /AI 的会话不能替你同意/);
  assert.match(M.approvalHtml([build, { id: 'P1', n: 1, note: '装依赖', command: 'npm ci' }], '本机'), /测试环境「本机」运行测试集之前要先执行下面的准备步骤和测试集的构建命令/);
  const calls = [];
  const testsets = loadWith('testsets', {
    './backend': { uiText: locale.uiText,  rf: async (args) => { calls.push(args); return { ok: true, stdout: JSON.stringify({ id: 'TS-003', blocked: [build], env: null, place: '/Users/me/bills' }) }; }, lastLine: (x) => x, parse: JSON.parse },
    './state': { state: { project: '/p', runs: new Map() }, send() {} }, './prompts': {}, '../renderer/scripts/tests-model': {},
  });
  const pre = await testsets.run({ ids: ['TS-003'] });
  assert.deepEqual(pre.needApproval, [build]); // 没同意：不跑，交给界面弹确认框
  assert.equal(calls.length, 1);
  await testsets.act({ action: 'approve-build', id: 'TS-003', print: build.print, dir: build.abs_dir });
  assert.deepEqual(Array.from(calls[1]), ['test', 'approve-build', 'TS-003', '--print', build.print, '--dir', '/Users/me/bills', '--json']);
  assert.match((await testsets.act({ action: 'approve-build', id: 'TS-003', print: 'whatever' })).error, /看过的是哪一条/);
  const renderer = fs.readFileSync(path.join(repo, 'app', 'src', 'renderer', 'scripts', 'envs.js'), 'utf8');
  assert.match(renderer, /action: 'approve-build', id: x\.set, print: x\.print, dir: x\.abs_dir/);
});

test('the app registers the socket next to its marker and removes both when it quits', async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-reg-'));
  const tokenMod = loadWith('app-token', { './config': { RF_HOME: tmp }, './session-token': { listen: (f) => T.shared.listen(f), close: () => T.shared.close() } });
  try {
    tokenMod.register();
    await new Promise((done) => setTimeout(done, 200));
    const reg = JSON.parse(fs.readFileSync(path.join(tmp, 'apps', process.pid + '.json'), 'utf8'));
    assert.equal(reg.token_sha256, crypto.createHash('sha256').update(tokenMod.envFor().RELAYFORGE_APP_TOKEN).digest('hex'));
    assert.ok(fs.existsSync(reg.socket));
    assert.equal(fs.statSync(path.join(tmp, 'apps', process.pid + '.json')).mode & 0o777, 0o600);
    tokenMod.unregister();
    assert.ok(!fs.existsSync(path.join(tmp, 'apps', process.pid + '.json')));
    assert.ok(!fs.existsSync(reg.socket));
  } finally { T.shared.close(); fs.rmSync(tmp, { recursive: true, force: true }); }
});
