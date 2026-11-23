// 会话经应用代写记录（app/src/main/proxy.js、backend/relayforge/core/proxy.py）：
// 数据外置的项目，记录在项目目录之外；会话的沙箱写不进去时，rf.py 把命令交给应用，应用以这个会话的身份执行。
// 端到端的那条用 macOS 的 sandbox-exec 模拟 Codex 的沙箱（和 Codex 用的是同一套机制）：外置目录不让写。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync, spawnSync } = require('node:child_process');

const repo = path.join(__dirname, '..');
const main = path.join(repo, 'app', 'src', 'main');
const T = require(path.join(main, 'session-token'));
const P = require(path.join(main, 'proxy'));
const RF = path.join(repo, 'backend', 'rf.py');
const PYTHON = process.env.RELAYFORGE_PYTHON || 'python3';

test('only record-writing commands are handed to the app, and the session cannot pick the directory', () => {
  for (const argv of [['note', 'doing', 'x'], ['issue', 'new', 'x'], ['design', 'save', 'R-1'], ['test', 'add', 'T-1'], ['req', 'case-add', 'R-1'], ['env', 'propose', 'e']]) {
    assert.ok(P.allowed(argv), argv.join(' '));
  }
  for (const argv of [['req', 'done', 'R-1'], ['req', 'start', 'R-1'], ['test', 'run', 'T-1'], ['env', 'prep-run', 'e'], ['data', 'import', 'f'],
    ['publish', 'run'], ['issue', 'confirm', 'I-1'], ['test', 'approve-build', 'T-1'], [], ['note', 1], 'note', ['__proto__']]) {
    assert.equal(P.allowed(argv), false, JSON.stringify(argv));
  }
  assert.deepEqual(P.withoutRoot(['note', 'doing', 'x', '--root', '/elsewhere', '--root=/b', '--guess']), ['note', 'doing', 'x', '--guess']);
});

test('the app runs a handed-over command only for a session that is still open', async () => {
  const a = T.issuer();
  const runs = [];
  a.setRunner((job) => { runs.push(job); return { ok: true, code: 0, stdout: 'done\n', stderr: '' }; });
  const { token } = a.issue({ session: 's-1', kind: 'develop', owner: 'R-1' });
  assert.match((await a.answer(JSON.stringify({ op: 'run', token, argv: ['note', 'doing', 'x'] }))).error, /已经关了/);
  a.bind(token, 4242, '/work');
  const out = await a.answer(JSON.stringify({ op: 'run', token, argv: ['note', 'doing', 'x'] }));
  assert.deepEqual(out, { ok: true, code: 0, stdout: 'done\n', stderr: '' });
  assert.equal(runs[0].place, '/work');
  assert.equal(runs[0].claims.kind, 'develop');
  // 代执行的 rf.py 记成这个会话的进程：验身份时算数；执行完就不算了
  runs[0].started(777);
  assert.deepEqual(a.answer(JSON.stringify({ op: 'verify', token })).pids, [777]);
  runs[0].ended(777);
  assert.deepEqual(a.answer(JSON.stringify({ op: 'verify', token })).pids, []);
  const forged = token.split('.')[0] + '.AAAA';
  assert.equal((await a.answer(JSON.stringify({ op: 'run', token: forged, argv: ['note', 'doing', 'x'] }))).ok, false);
  a.unbind(token);
  assert.equal((await a.answer(JSON.stringify({ op: 'run', token, argv: ['note', 'doing', 'x'] }))).ok, false);
});

test('the runner refuses commands outside the list and carries the session token, never the app marker', async () => {
  const place = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-proxy-')));
  const script = path.join(place, 'show.js'); // 代替 rf.py：打印它拿到的环境、参数和目录
  fs.writeFileSync(script, 'process.stdin.resume();let i="";process.stdin.on("data",d=>i+=d);process.stdin.on("end",()=>console.log(JSON.stringify({ env: process.env, argv: process.argv.slice(2), cwd: process.cwd(), input: i })));');
  const run = P.runner({ python: process.execPath, script, env: {
    RELAYFORGE_APP_TOKEN: 'marker', CLAUDECODE: '1', CODEX_THREAD_ID: 'parent-thread',
    RELAYFORGE_PROXY_CODEX_TRANSCRIPT: '/parent-transcript.jsonl', KEEP: 'yes',
  } });
  const pids = [];
  const job = (argv, stdin = null) => ({ token: 'tok', claims: { sid: 's-9' }, argv, stdin, place, started: (p) => pids.push(p), ended() {} });
  assert.match(run(job(['req', 'done', 'R-1'])).error, /不能交给应用代写/);
  const out = await run(job(['design', 'save', 'R-1', '--root', '/elsewhere'], '<html></html>'));
  assert.equal(out.code, 0, out.stderr);
  const seen = JSON.parse(out.stdout);
  assert.deepEqual(seen.argv, ['design', 'save', 'R-1']);
  assert.equal(seen.cwd, place);
  assert.equal(seen.input, '<html></html>');
  assert.equal(seen.env.RELAYFORGE_SESSION_SIG, 'tok');
  assert.equal(seen.env.RELAYFORGE_SESSION_ID, 's-9');
  assert.equal(seen.env.RELAYFORGE_PROXIED, '1');
  assert.equal(seen.env.RELAYFORGE_APP_TOKEN, undefined);
  assert.equal(seen.env.CLAUDECODE, undefined);
  assert.equal(seen.env.CODEX_THREAD_ID, undefined);
  assert.equal(seen.env.RELAYFORGE_PROXY_CODEX_TRANSCRIPT, undefined);
  assert.equal(seen.env.KEEP, 'yes');
  assert.equal(pids.length, 1);
});

const darwin = process.platform === 'darwin' && fs.existsSync('/usr/bin/sandbox-exec');

test('a sandboxed session that cannot write the records gets them written by the app, as itself', { skip: !darwin && 'needs macOS sandbox-exec' }, async (t) => {
  const home = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-home-')));
  const project = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-proj-')));
  t.after(() => { for (const dir of [home, project]) fs.rmSync(dir, { recursive: true, force: true }); });
  const env = { ...process.env, RELAYFORGE_HOME: home, CODEX_HOME: path.join(home, 'codex'), CLAUDE_CONFIG_DIR: path.join(home, 'claude') };
  for (const name of ['CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_PROJECT_DIR', 'RELAYFORGE_APP_TOKEN', 'RELAYFORGE_SESSION_SIG', 'RELAYFORGE_SESSION_ID', 'RELAYFORGE_FACTS_IN_REPO']) delete env[name];
  delete env.CODEX_THREAD_ID;
  const oldEnv = process.env;
  let codex;
  try {
    process.env = env;
    codex = require(path.join(main, 'codex-sessions'));
  } finally {
    process.env = oldEnv;
  }
  const git = (...args) => execFileSync('git', ['-C', project, ...args], { env });
  git('init', '-q', '-b', 'main');
  git('-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '--allow-empty', '-m', 'start');
  execFileSync(PYTHON, [RF, 'init', project], { env, cwd: project });
  const store = fs.realpathSync(path.join(project, '.relayforge'));
  assert.ok(store.startsWith(home), 'records are outside the project');

  // 应用：开 socket、登记（像 app-token.js 那样，只是登记的是这个测试进程）、装上代写的执行者
  const a = T.issuer();
  const sock = await a.listen(path.join(home, 'apps', process.pid + '.sock'));
  fs.writeFileSync(path.join(home, 'apps', process.pid + '.json'), JSON.stringify({ pid: process.pid, socket: sock }), { mode: 0o600 });
  a.setRunner(P.runner({ python: PYTHON, script: RF, env, codex }));
  const session = 'S-proxy-1';
  const thread = '3ac14b10-59c0-42dc-a958-41c949f74391';
  codex.register(session, project, '代写进展');
  const transcript = path.join(env.CODEX_HOME, 'sessions', `rollout-${thread}.jsonl`);
  fs.mkdirSync(path.dirname(transcript), { recursive: true });
  fs.writeFileSync(transcript, [
    { type: 'session_meta', payload: { id: thread, cwd: project } },
    { type: 'response_item', payload: { type: 'message', role: 'user',
      content: [{ type: 'input_text', text: `记录进展\nRelayForge 会话标识：${session}` }] } },
  ].map((entry) => JSON.stringify(entry)).join('\n') + '\n');
  const { token } = a.issue({ session, kind: 'chat', owner: null });
  a.bind(token, process.pid, project); // 「会话进程」就是这个测试进程：沙箱里的 rf.py 是它的下层进程
  const profile = `(version 1)(allow default)(deny file-write* (subpath "${store}"))`;
  const inSandbox = (args) => new Promise((resolve) => {
    const child = require('node:child_process').spawn('/usr/bin/sandbox-exec', ['-p', profile, PYTHON, RF, ...args],
      { cwd: project, env: { ...env, RELAYFORGE_SESSION_SIG: token, RELAYFORGE_SESSION_ID: session, CODEX_THREAD_ID: thread } });
    let out = '', err = '';
    child.stdout.on('data', (d) => { out += d; }); child.stderr.on('data', (d) => { err += d; });
    child.on('close', (code) => resolve({ code, out, err }));
  });
  try {
    // 沙箱里确实写不了（不然这条测试什么也没测）
    const direct = spawnSync('/usr/bin/sandbox-exec', ['-p', profile, '/usr/bin/touch', path.join(store, 'x')]);
    assert.notEqual(direct.status, 0);
    const planned = await inSandbox(['note', 'plan', '验证代写进展']);
    assert.equal(planned.code, 0, planned.err);
    const claudeFolder = path.join(env.CLAUDE_CONFIG_DIR, 'projects', project.replace(/[^0-9a-zA-Z]/g, '-'));
    fs.mkdirSync(claudeFolder, { recursive: true });
    fs.writeFileSync(path.join(claudeFolder, 'other-claude-session.jsonl'), '{}\n');
    const progress = await inSandbox(['note', 'doing', '进展写入 Codex 会话', '--session', session]);
    assert.equal(progress.code, 0, progress.err);
    const sessionFiles = fs.readdirSync(path.join(store, 'sessions')).filter((file) => file.endsWith('.md'));
    assert.equal(sessionFiles.length, 1);
    const recorded = () => fs.readFileSync(path.join(store, 'sessions', sessionFiles[0]), 'utf8');
    assert.match(recorded(), /session_id: S-proxy-1/);
    assert.match(recorded(), /验证代写进展〔[^#]+#1/);
    assert.match(recorded(), /进展写入 Codex 会话/);
    const made = await inSandbox(['issue', 'new', '代写的问题', '--desc', '从沙箱里记的', '--source', 'Agent']);
    assert.equal(made.code, 0, made.err);
    const issues = fs.readdirSync(path.join(store, 'issues')).filter((f) => f.endsWith('.md'));
    assert.equal(issues.length, 1);
    assert.match(fs.readFileSync(path.join(store, 'issues', issues[0]), 'utf8'), /代写的问题/);
    fs.unlinkSync(transcript);
    const missing = await inSandbox(['note', 'doing', '找不到 Codex 对话']);
    assert.equal(missing.code, 1);
    assert.match(missing.err, /没有找到当前会话/);
    assert.deepEqual(fs.readdirSync(path.join(store, 'sessions')).filter((file) => file.endsWith('.md')), sessionFiles);
    // 只由用户触发的命令：不转交，在沙箱里照样被拒绝
    const refused = await inSandbox(['data', 'import', path.join(home, 'nothing.tar.gz')]);
    assert.notEqual(refused.code, 0);
    // 会话关了以后：不再代写
    a.unbind(token);
    const closed = await inSandbox(['issue', 'new', '关了以后', '--desc', 'x', '--source', 'Agent']);
    assert.notEqual(closed.code, 0);
    assert.match(closed.err, /已经关了/);
    assert.equal(fs.readdirSync(path.join(store, 'issues')).filter((f) => f.endsWith('.md')).length, 1);
  } finally {
    a.close();
  }
});

test('the app and rf.py agree on which commands may be handed over', () => {
  const py = JSON.parse(execFileSync(PYTHON, ['-c', 'import json,sys; sys.path.insert(0, sys.argv[1]); from relayforge.core.proxy import PROXIED; print(json.dumps(sorted([c, a] for c, a in PROXIED)))', path.join(repo, 'backend')]).toString());
  const js = [];
  for (const [cmd, actions] of Object.entries(P.PROXIED)) for (const a of actions === null ? [null] : actions) js.push([cmd, a]);
  const key = (x) => JSON.stringify(x);
  assert.deepEqual(js.map(key).sort(), py.map(key).sort());
});
