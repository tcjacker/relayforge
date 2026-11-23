// 人工验收的服务：运行的是不是最新代码、代码变了自动重启、启动记录。
// 代码的状态（提交变、未提交的改动变、被忽略的文件不算、.relayforge/ 不算、文件太多时退化）、两个触发时机（开发会话一轮停下、打开标签）、
// 开关关着只标记、没运行不启动、并发只重启一次、要重新同意时不自动重启、启动记录的每种动作和上限、交回开发会话的话里带提交信息。
// 进程是很小的 node 脚本；每个测试结束都停掉，最后查没有孤儿。
const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync, spawn, spawnSync } = require('node:child_process');
const locale = require('../app/src/main/locale');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-restart-'));
const main = path.join(__dirname, '..', 'app', 'src', 'main');
const codestate = require(path.join(main, 'codestate'));
const hist = require(path.join(main, 'svc-history'));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 8000) { const end = Date.now() + ms; while (Date.now() < end) { if (await fn()) return true; await wait(40); } return false; }
const groupAlive = (pid) => { try { process.kill(-pid, 0); return true; } catch (e) { return e.code === 'EPERM'; } };
const git = (cwd, ...a) => execFileSync('git', ['-C', cwd, '-c', 'user.name=t', '-c', 'user.email=t@e.com', ...a], { encoding: 'utf8' });
function repo(name) {
  const d = path.join(TMP, name);
  fs.mkdirSync(d, { recursive: true });
  git(d, 'init', '-q', '-b', 'main');
  fs.writeFileSync(path.join(d, '.gitignore'), 'ignored/\n*.log\n.worktrees/\n');
  fs.writeFileSync(path.join(d, 'app.js'), 'setInterval(() => {}, 1000);\n');
  fs.mkdirSync(path.join(d, '.relayforge'));
  fs.writeFileSync(path.join(d, '.relayforge', 'fact.md'), 'a\n');
  git(d, 'add', '-A'); git(d, 'commit', '-qm', 'init');
  return d;
}

test('code state: commit, uncommitted edits (content, not only names), untracked files change it; ignored files, .relayforge/ and fact-only commits do not', async () => {
  const d = repo('state');
  const s0 = await codestate.stateOf(d);
  assert.match(s0.short, /^[0-9a-f]{7}$/);
  assert.equal(s0.dirty, false);
  const same = async () => (await codestate.changed(s0, d)).changed;
  assert.equal(await same(), false);
  // 不算：被忽略的文件、.relayforge/ 里的改动、.relayforge/ 的提交
  fs.mkdirSync(path.join(d, 'ignored')); fs.writeFileSync(path.join(d, 'ignored', 'x'), 'x'); fs.writeFileSync(path.join(d, 'a.log'), 'x');
  fs.writeFileSync(path.join(d, '.relayforge', 'fact.md'), 'b\n'); fs.mkdirSync(path.join(d, '.worktrees', 'R-1'), { recursive: true }); fs.writeFileSync(path.join(d, '.worktrees', 'R-1', 'y'), 'y');
  assert.equal(await same(), false, 'ignored, .relayforge and .worktrees do not count');
  git(d, 'add', '-A'); git(d, 'commit', '-qm', 'facts only');
  const afterFacts = await codestate.stateOf(d);
  assert.notEqual(afterFacts.head, s0.head);
  assert.equal(await same(), false, 'a commit that only touches .relayforge/ does not count');
  // 算：未提交的改动（同一个文件改两次，状态列表一样，内容不一样）
  fs.writeFileSync(path.join(d, 'app.js'), 'setInterval(() => {}, 2000);\n');
  const e1 = await codestate.stateOf(d);
  assert.equal(await same(), true);
  assert.equal(e1.dirty, true);
  fs.writeFileSync(path.join(d, 'app.js'), 'setInterval(() => {}, 3000);\n');
  const e2 = await codestate.stateOf(d);
  assert.notEqual(e1.fp, e2.fp, 'same status line, different content, different fingerprint');
  // 改回去：又一样了
  fs.writeFileSync(path.join(d, 'app.js'), 'setInterval(() => {}, 1000);\n');
  assert.equal(await same(), false);
  // 新文件（没跟踪的）
  fs.writeFileSync(path.join(d, 'new.js'), '1');
  assert.equal(await same(), true);
  fs.unlinkSync(path.join(d, 'new.js'));
  assert.equal(await same(), false);
  // 提交了代码：HEAD 动了，改了代码文件
  fs.writeFileSync(path.join(d, 'app.js'), '// v2\n'); git(d, 'commit', '-qam', 'code');
  assert.equal(await same(), true);
  assert.equal((await codestate.stateOf(d)).dirty, false);
  // 不是 git 目录：没有状态，当作没变
  const plain = path.join(TMP, 'plain'); fs.mkdirSync(plain);
  assert.equal(await codestate.stateOf(plain), null);
  assert.equal((await codestate.changed(s0, plain)).changed, false);
});

test('code state degrades to the status list plus modification times when there are too many files', () => {
  const d = repo('big');
  for (let i = 0; i < 6; i++) fs.writeFileSync(path.join(d, `f${i}.txt`), String(i));
  const script = `const c = require(${JSON.stringify(path.join(main, 'codestate'))}); (async () => { const a = await c.stateOf(${JSON.stringify(d)}); require('fs').writeFileSync(${JSON.stringify(path.join(d, 'f1.txt'))}, 'changed!'); const b = await c.stateOf(${JSON.stringify(d)}); console.log(JSON.stringify([a.degraded, b.degraded, a.fp !== b.fp])); })();`;
  const out = spawnSync('node', ['-e', script], { env: { ...process.env, RELAYFORGE_CODE_MAX_FILES: '3' }, encoding: 'utf8' });
  assert.equal(out.stdout.trim(), '[true,true,true]', out.stderr); // 退化了，内容改了（大小变了）照样能看出来
});

test('history: every action is recorded with time, reason, short commit, dirty flag, exit code, ms; capped; secrets masked', () => {
  const log = path.join(TMP, 'h1', 'services.log');
  const mask = (t) => String(t).split('s3cret-value').join('••••••');
  for (const action of hist.ACTIONS) hist.append(log, '后端', { action, reason: '原因 s3cret-value', head: 'abcdef1234', dirty: true, code: 2, ms: 1500 }, mask);
  const rows = hist.recent(log, '后端', 50);
  assert.deepEqual(rows.map((r) => r.action), ['启动', '停止', '重启', '自动重启', '自己退出', '启动失败', '应用退出时停掉', '清理遗留进程']);
  assert.equal(rows[0].head, 'abcdef1'); assert.equal(rows[0].dirty, true); assert.equal(rows[0].code, 2); assert.equal(rows[0].ms, 1500);
  assert.ok(rows.every((r) => typeof r.t === 'number' && r.reason === '原因 ••••••'));
  const file = hist.fileOf(log, '后端');
  assert.ok(file.startsWith(path.dirname(log)) && !fs.readFileSync(file, 'utf8').includes('s3cret-value'));
  assert.deepEqual(Object.keys(hist.all(log)), ['后端']);
  for (let i = 0; i < 230; i++) hist.append(log, '后端', { action: '启动', reason: 'n' + i });
  const last = hist.recent(log, '后端', 500);
  assert.equal(last.length, hist.MAX);
  assert.equal(last[last.length - 1].reason, 'n229');
  assert.equal(fs.readFileSync(file, 'utf8').trim().split('\n').length, hist.MAX); // 文件里也只留最近的
  hist.append(log, '前/端 x', { action: '启动' }); // 服务名里的斜杠不会跑出目录
  assert.deepEqual(Object.keys(hist.all(log)).sort(), ['前/端 x', '后端'].sort());
});

// ---------- 服务：真的 services.js，rf.py 换成替身 ----------
const worlds = [];
test.after(() => { for (const S of worlds) S.stopAll(); }); // 哪个测试中途失败了，也不留进程
function world(name, opts = {}) {
  const place = repo(name);
  const calls = [];
  const sends = [];
  const plan = { auto_restart: true, approved: true, needs: { services: [], prep: [] }, ...opts };
  const item = () => ({ name: '后端', command: `exec node app.js ${place}`, cwd: place, port: null, accept: [], approved: !plan.needsConsent, auto_restart: plan.auto_restart, place });
  const mocks = {
    './backend': {
      uiText: locale.uiText,
      rf: async (args) => {
        calls.push(args.join(' '));
        const out = { env: { id: 'ENV-001', name: '本机' }, place, log: path.join(TMP, name + '-logs', 'services.log'), services: [item()], prep: [], vars: {}, secrets: [], why: '',
          needs: { services: plan.needsConsent ? ['后端'] : [], prep: [] } };
        if (args[1] === 'launch' || args[1] === 'plan') return { ok: true, stdout: JSON.stringify(out) };
        return { ok: false, stderr: 'x' };
      },
      cleanEnv: () => ({ ...process.env }), lastLine: (x) => x, parse: (t, f) => { try { return JSON.parse(t); } catch (_) { return f; } },
    },
    './state': { state: { project: '/p' }, send: (ch, v) => sends.push([ch, v]) }, './config': { RF_HOME: path.join(TMP, name + '-home') },
    './codestate': codestate, './svc-history': hist, './record-sign': require(path.join(main, 'record-sign')),
  };
  const context = vm.createContext({ require: (id) => id === './locale' ? locale : mocks[id] || require(id), module: { exports: {} }, process, setTimeout, clearTimeout, Atomics, SharedArrayBuffer, Int32Array, Date, Promise, JSON, Buffer });
  vm.runInContext(fs.readFileSync(path.join(main, 'services.js'), 'utf8'), context);
  const S = context.module.exports;
  const sc = () => S.scopeOf('R-1', '/p');
  const svc = () => sc().services.get('后端');
  const history = () => hist.recent(sc().file, '后端', 100);
  // 先写到仓库外面再改名换上去：刚启动的进程正在读 app.js 时，读到的要么是整份旧的、要么是整份新的，
  // 不会读到写了一半的空文件（那样进程会马上退出，测试偶发失败）
  const edit = (text) => { const tmp = path.join(TMP, name + '-app.js.tmp'); fs.writeFileSync(tmp, text); fs.renameSync(tmp, path.join(place, 'app.js')); };
  const launches = () => calls.filter((c) => c.startsWith('env launch')).length;
  worlds.push(S);
  const cleanup = async () => { await S.stop({ scope: 'R-1' }); await until(() => !svc() || !svc().pid || !groupAlive(svc().pid)); };
  return { S, place, calls, plan, sc, svc, history, edit, launches, cleanup, sends };
}
const logsOf = (w) => w.sc().logs.map((l) => l.x);

test('start records the code it ran; an edit afterwards marks it stale (mark only, no restart); undoing the edit clears the mark', async () => {
  const w = world('w1');
  const r = await w.S.start({ scope: 'R-1' });
  assert.ok(r.ok, JSON.stringify(r));
  assert.equal(w.svc().status, 'running');
  assert.equal(w.svc().codeStart.dirty, false);
  assert.equal(w.history().length, 1);
  assert.equal(w.history()[0].action, '启动'); assert.equal(w.history()[0].reason, '你点的');
  assert.equal(w.history()[0].head, w.svc().codeStart.short); assert.equal(w.history()[0].dirty, false); assert.ok(w.history()[0].ms >= 0);
  assert.equal((await w.S.refresh({ scope: 'R-1' })).results.length, 0);
  assert.equal(w.svc().stale, false);
  const pid = w.svc().pid;
  w.edit('setInterval(() => {}, 5000);\n');
  await w.S.watch(); // watcher 的检查：只标记
  assert.equal(w.svc().stale, true);
  assert.equal(w.svc().pid, pid, 'marked, not restarted');
  assert.equal(w.launches(), 1);
  const view = w.S.view({ scope: 'R-1' });
  assert.equal(view.services[0].stale, true);
  w.edit('setInterval(() => {}, 1000);\n');
  await w.S.refresh({ scope: 'R-1' });
  assert.equal(w.svc().stale, false);
  await w.cleanup();
});

test('trigger 1: the development session finishes a turn -> restart (auto-restart record, log line, new code state)', async () => {
  const w = world('w2');
  await w.S.start({ scope: 'R-1' });
  const pid = w.svc().pid;
  w.edit('setInterval(() => {}, 7000);\n');
  w.S.turnEnded('R-1');
  assert.ok(await until(() => w.history().some((h) => h.action === '自动重启')), 'restarted');
  assert.ok(await until(() => w.svc().status === 'running' && w.svc().pid !== pid));
  const rec = w.history().find((h) => h.action === '自动重启');
  assert.equal(rec.reason, '开发会话改了代码'); assert.equal(rec.dirty, true); assert.ok(rec.head); assert.ok(rec.ms >= 0);
  assert.ok(logsOf(w).some((x) => x === '自动重启：开发会话改了代码'));
  assert.ok(w.sc().logs.find((l) => l.x === '自动重启：开发会话改了代码').k === 'sys');
  assert.equal(w.svc().stale, false, 'the new process runs the new code');
  assert.equal(w.history().filter((h) => h.action === '停止').length, 0, 'the stop inside a restart is not a record of its own');
  assert.ok(!groupAlive(pid));
  await w.cleanup();
});

test('trigger 2: opening the tab -> restart; nothing changed -> nothing happens', async () => {
  const w = world('w3');
  await w.S.start({ scope: 'R-1' });
  const pid = w.svc().pid;
  assert.equal((await w.S.refresh({ scope: 'R-1', trigger: 'open' })).results.length, 0);
  assert.equal(w.svc().pid, pid);
  w.edit('setInterval(() => {}, 8000);\n');
  const res = await w.S.refresh({ scope: 'R-1', trigger: 'open' });
  assert.equal(res.results[0].restarted, true);
  assert.notEqual(w.svc().pid, pid);
  assert.equal(w.history().filter((h) => h.action === '自动重启').pop().reason, '打开人工验收时发现代码改过');
  assert.ok(logsOf(w).includes('自动重启：打开人工验收时发现代码改过'));
  await w.cleanup();
});

test('switch off: only marked, never restarted; not running: never started; main scope is not a development session', async () => {
  const w = world('w4', { auto_restart: false });
  await w.S.start({ scope: 'R-1' });
  const pid = w.svc().pid;
  w.edit('setInterval(() => {}, 9000);\n');
  for (const trigger of ['turn', 'open']) {
    const res = await w.S.refresh({ scope: 'R-1', trigger });
    assert.equal(res.results[0].skipped, 'off');
  }
  w.S.turnEnded('R-1'); await wait(300);
  assert.equal(w.svc().pid, pid); assert.equal(w.svc().stale, true); assert.equal(w.launches(), 1);
  assert.ok(!w.history().some((h) => h.action === '自动重启'));
  // 没在运行：停掉以后代码再变，两个触发点都不会把它启动起来
  await w.S.stop({ scope: 'R-1', name: '后端' });
  assert.ok(await until(() => w.svc().status === 'idle'));
  w.plan.auto_restart = true;
  w.edit('setInterval(() => {}, 9500);\n');
  await w.S.refresh({ scope: 'R-1', trigger: 'open' }); w.S.turnEnded('R-1'); await wait(300);
  assert.equal(w.svc().status, 'idle'); assert.equal(w.launches(), 1);
  assert.equal(w.history().filter((h) => h.action === '停止').length, 1);
  w.S.turnEnded('main'); w.S.turnEnded('test:R-1'); w.S.turnEnded('R-404'); // 不是这个需求的开发会话：什么都不做
  await w.cleanup();
});

test('two triggers at about the same time restart only once', async () => {
  const w = world('w5');
  await w.S.start({ scope: 'R-1' });
  w.edit('setInterval(() => {}, 7100);\n');
  const [a, b, c] = await Promise.all([w.S.refresh({ scope: 'R-1', trigger: 'turn' }), w.S.refresh({ scope: 'R-1', trigger: 'open' }), w.S.refresh({ scope: 'R-1', trigger: 'open' })]);
  assert.equal([a, b, c].flatMap((x) => x.results).filter((x) => x.restarted).length, 1);
  assert.equal(w.history().filter((h) => h.action === '自动重启').length, 1);
  assert.equal(w.launches(), 2, 'one start + one restart');
  // 之后再触发：新进程跑的就是新代码，不再重启
  await w.S.refresh({ scope: 'R-1', trigger: 'turn' });
  assert.equal(w.launches(), 2);
  await w.cleanup();
});

test('start command or preparation steps need new consent -> no automatic restart, only a notice; the process keeps running', async () => {
  const w = world('w6');
  await w.S.start({ scope: 'R-1' });
  const pid = w.svc().pid;
  w.plan.needsConsent = true; // 命令或目录改了，还没同意
  w.edit('setInterval(() => {}, 7200);\n');
  const res = await w.S.refresh({ scope: 'R-1', trigger: 'turn' });
  assert.equal(res.results[0].skipped, 'consent');
  assert.equal(w.svc().pid, pid); assert.equal(w.svc().status, 'running'); assert.equal(w.svc().consent, true); assert.equal(w.svc().stale, true);
  assert.equal(w.S.view({ scope: 'R-1' }).services[0].consent, true);
  assert.ok(logsOf(w).some((x) => /需要你重新同意，没有自动重启/.test(x)));
  await w.S.refresh({ scope: 'R-1', trigger: 'open' });
  assert.equal(logsOf(w).filter((x) => /需要你重新同意/.test(x)).length, 1, 'said once');
  assert.ok(!w.history().some((h) => h.action === '自动重启'));
  await w.cleanup();
});

test('a failed automatic restart is an ordinary start failure (recorded, shown as failed)', async () => {
  const w = world('w7');
  // 第一版启动时在仓库外面留一个记号：等它真的把自己的代码读进去了再改文件，
  // 否则改得太快，第一次启动的进程读到的就是新代码（偶发：记录里只有「启动、启动失败」）
  const loaded = path.join(TMP, 'w7-loaded');
  w.edit(`require('fs').writeFileSync(${JSON.stringify(loaded)}, '1'); setInterval(() => {}, 5000);\n`);
  await w.S.start({ scope: 'R-1' });
  assert.ok(await until(() => fs.existsSync(loaded)));
  w.edit('process.exit(4);\n'); // 新代码起不来
  await w.S.refresh({ scope: 'R-1', trigger: 'turn' });
  assert.ok(await until(() => w.svc().status === 'failed'));
  const acts = w.history().map((h) => h.action);
  assert.deepEqual(acts, ['启动', '自动重启', '启动失败']); // 没配端口的进程活着就算运行，很快退出了再算失败
  assert.equal(w.history()[2].code, 4);
  await w.cleanup();
});

test('records for every kind of action: stop, restart, exited by itself, failed, quitting the app, left-over cleanup', async () => {
  const q = process.env.RELAYFORGE_QUICK_MS;
  process.env.RELAYFORGE_QUICK_MS = '200';
  const w = world('w8');
  await w.S.start({ scope: 'R-1' });
  await w.S.stop({ scope: 'R-1', name: '后端' });
  await w.S.restart({ scope: 'R-1', name: '后端' }); // 没在运行时的「重启」：直接启动，记成重启
  assert.ok(await until(() => w.svc().status === 'running'));
  await w.S.stopScope('R-1', '/p', '确认完成前停掉');
  assert.ok(await until(() => w.svc().status === 'idle'));
  await w.S.start({ scope: 'R-1', names: ['后端'], action: '启动' });
  assert.ok(await until(() => w.svc().status === 'running'));
  assert.equal(w.S.stopAll('切换项目'), 1);
  await w.S.start({ scope: 'R-1' });
  assert.ok(await until(() => w.svc().status === 'running'));
  await wait(400); // 过了「启动后很快就退出」的时间
  process.kill(-w.svc().pid, 'SIGKILL'); // 自己退出（被外面杀了）
  assert.ok(await until(() => w.svc().status === 'exited'));
  assert.equal(w.history().at(-1).action, '自己退出');
  assert.ok(w.history().at(-1).signal === 'SIGKILL' || w.history().at(-1).code != null);
  w.edit('process.exit(1);\n'); await w.S.start({ scope: 'R-1' });
  assert.ok(await until(() => w.svc().status === 'failed'));
  const rows = w.history();
  assert.deepEqual(rows.map((r) => r.action), ['启动', '停止', '重启', '停止', '启动', '应用退出时停掉', '启动', '自己退出', '启动', '启动失败']);
  assert.deepEqual(rows.map((r) => r.reason), ['你点的', '你点的', '你点的', '确认完成前停掉', '你点的', '切换项目', '你点的', '', '你点的', '启动以后很快就退出了（退出码 1）。']);
  assert.ok(rows.every((r) => r.head && typeof r.dirty === 'boolean'));
  // 遗留进程：应用崩了、进程还在，下次启动时清掉，记一条
  const orphan = spawn('sh', ['-c', 'sleep 300'], { detached: true, stdio: 'ignore' }); orphan.unref();
  await wait(150);
  const dead = spawnSync('sh', ['-c', 'echo $$']).stdout.toString().trim();
  fs.mkdirSync(w.S.RUN_DIR, { recursive: true });
  fs.writeFileSync(path.join(w.S.RUN_DIR, `${dead}-${orphan.pid}.json`), JSON.stringify(require(path.join(main, 'record-sign')).sign({ owner: Number(dead), pid: orphan.pid, start: w.S.startTime(orphan.pid), scope: 'R-1', project: '/p', service: '后端', log: w.sc().file, head: 'abc1234', dirty: true })));
  assert.equal(w.S.sweep().length, 1);
  const swept = w.history().at(-1);
  assert.equal(swept.action, '清理遗留进程'); assert.equal(swept.head, 'abc1234'); assert.equal(swept.dirty, true);
  assert.ok(await until(() => !groupAlive(orphan.pid)));
  if (q === undefined) delete process.env.RELAYFORGE_QUICK_MS; else process.env.RELAYFORGE_QUICK_MS = q;
  await w.cleanup();
});

test('what goes back to the development session says which commit was running and whether the code changed', async () => {
  const w = world('w9');
  assert.equal(await w.S.runLine('R-1'), '', 'nothing running: no line');
  await w.S.start({ scope: 'R-1' });
  const short = w.svc().codeStart.short;
  assert.equal(await w.S.runLine('R-1'), `「后端」运行的是提交 ${short}；启动后代码没有改过`);
  w.edit('setInterval(() => {}, 6100);\n');
  assert.match(await w.S.runLine('R-1'), new RegExp(`运行的是提交 ${short}；启动后代码改过了，运行的不是最新的`));
  await w.cleanup();
  // acceptance.mark 把它作为 --ran 交给 rf.py
  const calls = [];
  const mocks = {
    './backend': { uiText: locale.uiText,  rf: async (args) => { calls.push(args); return { ok: true, stdout: JSON.stringify({ id: 'R-1', n: 2, result: 'bad', say: '…' }) }; }, lastLine: (x) => x, parse: JSON.parse },
    './services': { tail: () => 'log', runLine: async () => '「后端」运行的是提交 abc1234；启动后代码改过了' }, './testsets': { tell() {} }, './requirements': { pushReqs() {} }, './issues': { newIssue: async (x) => x },
  };
  const context = vm.createContext({ require: (id) => id === './locale' ? locale : mocks[id] || require(id), module: { exports: {} } });
  vm.runInContext(fs.readFileSync(path.join(main, 'acceptance.js'), 'utf8'), context);
  await context.module.exports.mark({ req: 'R-1', n: 2, result: 'bad', say: '不对' });
  assert.deepEqual([...calls[0]], ['req', 'accept', 'R-1', '2', 'bad', '--say', '不对', '--ran', '「后端」运行的是提交 abc1234；启动后代码改过了', '--file', '-', '--json']);
  await context.module.exports.mark({ req: 'R-1', n: 1, result: 'ok' });
  assert.ok(![...calls[1]].includes('--ran'));
  const made = await context.module.exports.issue({ scope: 'main', text: '列表空了' });
  assert.match(made.desc, /当时运行的服务：「后端」运行的是提交 abc1234/);
});

test('bench model: the stale mark, the note beside it, history lines', () => {
  const BM = require(path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts', 'bench-model'));
  const t = new Date(2026, 10, 19, 15, 10).getTime();
  assert.equal(BM.histLine({ t, action: '自动重启', reason: '开发会话改了代码', head: 'abc1234', dirty: false, ms: 1500 }), '11-19 15:10 自动重启：开发会话改了代码 · abc1234 · 1.5 秒后可用');
  assert.equal(BM.histLine({ t, action: '自己退出', reason: '', head: 'abc1234', dirty: true, code: 137 }), '11-19 15:10 自己退出 · abc1234 + 未提交改动 · 退出码 137');
  assert.equal(BM.histLast([]), '');
  assert.equal(BM.isStale({ status: 'running', stale: true }), true);
  assert.equal(BM.isStale({ status: 'exited', stale: true }), false);
  assert.match(BM.staleNote({ status: 'running', stale: true, autoRestart: true }, { auto_restart: true }), /开发会话下一轮停下时/);
  assert.match(BM.staleNote({ status: 'running', stale: true }, { auto_restart: false }), /关着/);
  assert.match(BM.staleNote({ status: 'running', stale: true, consent: true }, {}), /重新同意/);
  const rows = BM.rows({ services: [{ name: '后端' }] }, [], { 后端: [{ action: '启动' }] });
  assert.equal(rows[0].hist.length, 1);
});

test('no orphan processes are left by these tests', async () => {
  await wait(500);
  const out = spawnSync('pgrep', ['-f', TMP], { encoding: 'utf8' }).stdout.trim();
  assert.equal(out, '', 'left over: ' + out);
});
