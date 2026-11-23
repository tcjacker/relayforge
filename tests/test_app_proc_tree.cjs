// 收拾整棵进程树（应用这一侧）：proc-tree.js 杀整棵树，backend.js 调 rf.py 超时连同后代一起停，
// session-procs.js 按会话编号收拾会话留下的进程（关会话、应用崩溃后下次启动），我们自己的 rf.py 不杀。用真的进程验证。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');

const main = path.join(__dirname, '..', 'app', 'src', 'main');
const posix = process.platform !== 'win32';

// 一个会躲的孙子：自己开新会话（像 Claude Code 的 Bash），把 pid 写下来，然后一直睡
const HIDER = `
const { spawn } = require('child_process');
const kid = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 120000)'], { detached: true, stdio: 'ignore' });
require('fs').writeFileSync(process.argv[1], String(kid.pid));
setTimeout(() => {}, 120000);
`;

function alive(pid) {
  try { process.kill(pid, 0); } catch (error) { return error.code === 'EPERM'; }
  try { return !execFileSync('ps', ['-o', 'stat=', '-p', String(pid)], { encoding: 'utf8' }).trim().startsWith('Z'); } catch (_) { return false; }
}
async function gone(pid, ms = 5000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (!alive(pid)) return true; await new Promise((r) => setTimeout(r, 100)); }
  return false;
}
async function pidFrom(file, ms = 10000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { const n = Number(fs.readFileSync(file, 'utf8')); if (n) return n; } catch (_) { /* 还没写 */ }
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error('孙子进程没有写出 pid');
}
// 按应用的做法签名的进程记录（RF_HOME 不同：在那个 RF_HOME 下签）
function signed(home, record) {
  return execFileSync(process.execPath, ['-e', `process.stdout.write(JSON.stringify(require(${JSON.stringify(path.join(main, 'record-sign'))}).sign(${JSON.stringify(record)})))`],
    { env: { ...process.env, RELAYFORGE_HOME: home } }).toString();
}
function scratch(t) {
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-tree-')));
  const pids = [];
  t.after(() => { for (const p of pids) { try { process.kill(p, 'SIGKILL'); } catch (_) { /* 已经没了 */ } } fs.rmSync(dir, { recursive: true, force: true }); });
  return { dir, pids };
}

test('killTree takes down a grandchild that started a session of its own', { skip: !posix }, async (t) => {
  const { dir, pids } = scratch(t);
  const file = path.join(dir, 'kid.pid');
  const top = spawn(process.execPath, ['-e', HIDER, file], { stdio: 'ignore' });
  pids.push(top.pid);
  const kid = await pidFrom(file);
  pids.push(kid);
  const { killTree, descendants } = require(path.join(main, 'proc-tree'));
  assert.ok(descendants(top.pid).includes(kid));
  killTree(top.pid);
  assert.ok(await gone(top.pid), 'top still alive');
  assert.ok(await gone(kid), 'grandchild still alive');
  killTree(process.pid); killTree(1); // 不杀自己，也不碰 launchd / init
  assert.ok(alive(process.pid));
});

test('an rf.py call that runs out of time is stopped together with what it started', { skip: !posix }, async (t) => {
  const { dir, pids } = scratch(t);
  const file = path.join(dir, 'kid.pid');
  const { exec } = require(path.join(main, 'backend'));
  const pending = exec(process.execPath, ['-e', HIDER, file], dir, {}, 1500);
  const kid = await pidFrom(file);
  pids.push(kid);
  const out = await pending;
  assert.equal(out.timedOut, true);
  assert.ok(await gone(kid), 'grandchild still alive after the timeout');
});

test('a closed session leaves nothing behind but our own rf.py, found by its session id', { skip: !posix }, async (t) => {
  const { dir, pids } = scratch(t);
  const sid = 'test-' + process.pid + '-' + Date.now();
  const file = path.join(dir, 'kid.pid');
  const env = { ...process.env, RELAYFORGE_SESSION_ID: sid };
  // 会话里起的东西：一个会躲的（它的孙子也带着这个编号），和我们自己的 rf.py（会话结束时的同步，要让它写完）
  const top = spawn(process.execPath, ['-e', HIDER, file], { env, stdio: 'ignore', detached: true });
  const ours = path.join(dir, 'rf.py');
  fs.writeFileSync(ours, 'import time\ntime.sleep(120)\n');
  const sync = spawn('python3', [ours, 'capture'], { env, stdio: 'ignore', detached: true });
  pids.push(top.pid, sync.pid);
  const kid = await pidFrom(file);
  pids.push(kid);
  const sp = require(path.join(main, 'session-procs'));
  assert.deepEqual(new Set(sp.tagged(sid)), new Set([top.pid, kid]));
  assert.ok(sp.reap(sid) >= 1);
  assert.ok(await gone(top.pid) && await gone(kid), 'session processes still alive');
  assert.ok(alive(sync.pid), 'our own rf.py was killed');
  assert.deepEqual(sp.tagged('some-other-session'), []);
});

test('after a crash the next start cleans up what the dead app\'s sessions left', { skip: !posix }, async (t) => {
  const { dir, pids } = scratch(t);
  const sid = 'crash-' + process.pid + '-' + Date.now();
  const file = path.join(dir, 'kid.pid');
  const home = path.join(dir, 'home');
  const top = spawn(process.execPath, ['-e', HIDER, file], { env: { ...process.env, RELAYFORGE_SESSION_ID: sid }, stdio: 'ignore', detached: true });
  pids.push(top.pid);
  const kid = await pidFrom(file);
  pids.push(kid);
  // 一个已经死了的「应用」记下的会话
  const dead = spawn(process.execPath, ['-e', '0']);
  await new Promise((r) => dead.on('exit', r));
  fs.mkdirSync(path.join(home, 'run', 'sessions'), { recursive: true });
  fs.writeFileSync(path.join(home, 'run', 'sessions', `${dead.pid}-${sid}.json`), signed(home, { owner: dead.pid, owner_start: 'gone', session: sid }));
  // 新启动的应用（新的进程，RF_HOME 指到这里）
  execFileSync(process.execPath, ['-e', `process.exit(require(${JSON.stringify(path.join(main, 'session-procs'))}).sweep() ? 0 : 3)`],
    { env: { ...process.env, RELAYFORGE_HOME: home } });
  assert.ok(await gone(top.pid) && await gone(kid), 'leftovers still alive');
  assert.deepEqual(fs.readdirSync(path.join(home, 'run', 'sessions')), []);
});

// 沙箱里的进程，环境从外面读不到（按会话编号找不到）：在需求的工作目录里、成了孤儿、在沙箱里的才收拾；项目主目录里的、
// 别的会话也开在这个目录时，不收拾（可能是你自己的，或者分不清是谁的）
test('sandboxed leftovers are found by their worktree, never in the main project folder', { skip: process.platform !== 'darwin' || !fs.existsSync('/usr/bin/sandbox-exec') }, async (t) => {
  const { dir, pids } = scratch(t);
  const tree = path.join(dir, 'proj', '.worktrees', 'R-1'), mainDir = path.join(dir, 'proj');
  fs.mkdirSync(tree, { recursive: true });
  // 在沙箱里起一个孤儿：sh 起了 sleep 就退出，sleep 挂到 launchd 下
  const orphan = (cwd, file) => {
    execFileSync('/usr/bin/sandbox-exec', ['-p', '(version 1)(allow default)', 'sh', '-c', `nohup sleep 300 >/dev/null 2>&1 & echo $! > ${file}`],
      { cwd, env: { ...process.env, RELAYFORGE_SESSION_ID: 'invisible' } });
    const pid = Number(fs.readFileSync(file, 'utf8'));
    pids.push(pid);
    return pid;
  };
  const inTree = orphan(tree, path.join(dir, 'a.pid'));
  const inMain = orphan(mainDir, path.join(dir, 'b.pid'));
  const sp = require(path.join(main, 'session-procs'));
  assert.deepEqual(sp.tagged('invisible'), [], 'the environment of a sandboxed process cannot be read from outside');
  assert.deepEqual(sp.sandboxedOrphansIn(tree), [inTree]);
  assert.deepEqual(sp.sandboxedOrphansIn(mainDir), []); // 不是 .worktrees/ 下的：一个都不算
  sp.reap('invisible', { root: tree, shared: true }); // 别的会话也开在这里：不收拾
  assert.ok(alive(inTree));
  sp.reap('invisible', { root: mainDir });
  assert.ok(alive(inMain), 'a process in the main project folder may be yours');
  sp.reap('invisible', { root: tree });
  assert.ok(await gone(inTree), 'the sandboxed leftover in the worktree is still alive');
  assert.ok(alive(inMain));
});

test('after a crash the sandboxed leftovers in the dead session\'s worktree are cleaned up too', { skip: process.platform !== 'darwin' || !fs.existsSync('/usr/bin/sandbox-exec') }, async (t) => {
  const { dir, pids } = scratch(t);
  const tree = path.join(dir, 'proj', '.worktrees', 'F-2');
  fs.mkdirSync(tree, { recursive: true });
  const file = path.join(dir, 'c.pid');
  execFileSync('/usr/bin/sandbox-exec', ['-p', '(version 1)(allow default)', 'sh', '-c', `nohup sleep 300 >/dev/null 2>&1 & echo $! > ${file}`], { cwd: tree });
  const leftover = Number(fs.readFileSync(file, 'utf8'));
  pids.push(leftover);
  const home = path.join(dir, 'home');
  const dead = spawn(process.execPath, ['-e', '0']);
  await new Promise((r) => dead.on('exit', r));
  fs.mkdirSync(path.join(home, 'run', 'sessions'), { recursive: true });
  fs.writeFileSync(path.join(home, 'run', 'sessions', `${dead.pid}-s.json`), signed(home, { owner: dead.pid, owner_start: 'gone', session: 's', root: tree }));
  execFileSync(process.execPath, ['-e', `require(${JSON.stringify(path.join(main, 'session-procs'))}).sweep()`], { env: { ...process.env, RELAYFORGE_HOME: home } });
  assert.ok(await gone(leftover), 'the leftover is still alive');
});

test('a forged session record (not signed by the app) gets nothing killed on the next start', { skip: !posix }, async (t) => {
  const { dir, pids } = scratch(t);
  const home = path.join(dir, 'home');
  const victim = spawn('sleep', ['300'], { stdio: 'ignore', detached: true });
  pids.push(victim.pid);
  const start = execFileSync('ps', ['-o', 'lstart=', '-p', String(victim.pid)], { encoding: 'utf8' }).trim();
  fs.mkdirSync(path.join(home, 'run', 'sessions'), { recursive: true });
  // 会话在沙箱里读得到别的进程的启动时间，能写出一条「对得上」的记录；没有应用的签名就不算
  const forged = path.join(home, 'run', 'sessions', '1-forged.json');
  fs.writeFileSync(forged, JSON.stringify({ owner: 1, owner_start: 'never', session: 'forged', root: null, pid: victim.pid, start }));
  execFileSync(process.execPath, ['-e', `require(${JSON.stringify(path.join(main, 'session-procs'))}).sweep()`], { env: { ...process.env, RELAYFORGE_HOME: home } });
  await new Promise((r) => setTimeout(r, 300));
  assert.ok(alive(victim.pid), 'the victim was killed');
  assert.ok(!fs.existsSync(forged), 'the forged record is removed');
  // 同一条记录换成应用签的：照样收拾（说明拦下它的是签名，不是别的）
  fs.writeFileSync(forged, signed(home, { owner: 1, owner_start: 'never', session: 'forged', root: null, pid: victim.pid, start }));
  execFileSync(process.execPath, ['-e', `require(${JSON.stringify(path.join(main, 'session-procs'))}).sweep()`], { env: { ...process.env, RELAYFORGE_HOME: home } });
  assert.ok(await gone(victim.pid));
});
