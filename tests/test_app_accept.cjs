// 人工验收（应用这一侧）：服务的启动、停止、重启（整组停掉，不留孤儿）、等端口可连、端口被占、很快退出、一直连不上、同一个服务只有一个实例、
// 遗留进程的清理（主人不在了、启动时间对得上才停）、日志打码（显示和文件）、带给进程的环境（去掉 RelayForge 自己的变量）、
// 逐条验收交回开发会话、只由用户触发的命令在会话里禁止、界面的 model、RelayForge 自己的数据目录和窗口标记入口。
const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const locale = require('../app/src/main/locale');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-accept-'));
process.env.RELAYFORGE_HOME = path.join(TMP, 'rfhome'); // config.js 在第一次 require 时读它
process.env.RELAYFORGE_PORT_WAIT_MS = '2500';
process.env.RELAYFORGE_QUICK_MS = '1500';
const repo = path.join(__dirname, '..');
const main = path.join(repo, 'app', 'src', 'main');
const scripts = path.join(repo, 'app', 'src', 'renderer', 'scripts');
const { state } = require(path.join(main, 'state'));
state.project = fs.mkdtempSync(path.join(TMP, 'proj-'));
const S = require(path.join(main, 'services'));
const RS = require(path.join(main, 'record-sign')); // 进程记录由应用签名（sweep 只认签名对得上的）
const BM = require(path.join(scripts, 'bench-model'));

const SECRET = 'hunter2-very-secret';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 8000) { const end = Date.now() + ms; while (Date.now() < end) { if (await fn()) return true; await wait(50); } return false; }
function freePort() { return new Promise((done) => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => done(p)); }); }); }
const groupAlive = (pid) => { try { process.kill(-pid, 0); return true; } catch (e) { return e.code === 'EPERM'; } };
const pidAlive = (pid) => { try { process.kill(pid, 0); return true; } catch (e) { return e.code === 'EPERM'; } };
// 小服务：打印几行（带一个保密值和带上的环境变量），可选监听一个端口；再在后台留一个孙子进程（看整组停掉）
const SERVER = path.join(TMP, 'server.js');
fs.writeFileSync(SERVER, `
const port = Number(process.argv[2] || 0);
console.log('hello from', process.env.SVC_NAME, 'secret', process.env.DB_PASSWORD);
console.log('env project', process.env.RELAYFORGE_PROJECT || '(none)', 'home', process.env.RELAYFORGE_HOME || '(none)', 'data', process.env.APP_DATA);
console.error('to stderr');
if (port) require('net').createServer((s) => { s.on('error', () => {}); s.end('ok'); }).listen(port, '127.0.0.1', () => console.log('listening', port));
setInterval(() => {}, 1000);
`);
const grandchildFile = path.join(TMP, 'grandchild.pid');
const item = (name, extra = {}) => ({ name, command: `sleep 300 & echo $! > ${grandchildFile}.${name}; exec node ${SERVER} ${extra.port || ''}`, cwd: TMP, port: extra.port || null, accept: [{ name: 'APP_DATA', value: '/tmp/scratch/userdata' }], ...extra });
const launch = { vars: { SVC_NAME: 'demo', DB_PASSWORD: SECRET }, secrets: [SECRET], log: path.join(TMP, 'logs', 'services.log') };

test('a service with a port: starting until the port connects, then running; logs scrubbed; the whole group stops', async () => {
  process.env.RELAYFORGE_PROJECT = '/real/project'; // 正式实例的项目：不能漏给验收实例
  const port = await freePort();
  const sc = S.scopeOf('R-1');
  const first = await S.launchOne(sc, item('后端', { port }), launch);
  assert.equal(first.status, 'starting');
  assert.ok(first.waitingPort);
  assert.ok(await until(() => sc.services.get('后端').status === 'running'), 'running once the port connects');
  assert.ok(await until(() => sc.logs.some((l) => /listening/.test(l.x))));
  const text = sc.logs.map((l) => l.x).join('\n');
  assert.ok(!text.includes(SECRET) && text.includes('secret ••••••'), 'scrubbed in memory');
  assert.match(text, /env project \(none\) home \(none\) data \/tmp\/scratch\/userdata/); // RelayForge 自己的变量去掉了，验收变量带上了
  assert.ok(sc.logs.some((l) => l.k === 'err' && l.x === 'to stderr'));
  const file = fs.readFileSync(launch.log, 'utf8');
  assert.ok(file.includes('[后端]') && !file.includes(SECRET), 'scrubbed in the log file');
  assert.ok(!S.tail('R-1', 50).includes(SECRET));
  // 同一个服务只有一个实例
  const again = await S.launchOne(sc, item('后端', { port }), launch);
  assert.equal(again.pid, first.pid);
  // 记了 pid 和启动时间
  const rec = fs.readdirSync(S.RUN_DIR).map((n) => JSON.parse(fs.readFileSync(path.join(S.RUN_DIR, n), 'utf8'))).find((r) => r.pid === first.pid);
  assert.ok(rec && rec.start && rec.owner === process.pid && rec.scope === 'R-1');
  const grandchild = Number(fs.readFileSync(grandchildFile + '.后端', 'utf8'));
  assert.ok(pidAlive(grandchild));
  await S.stop({ scope: 'R-1', name: '后端' });
  assert.equal(sc.services.get('后端').status, 'idle');
  assert.ok(await until(() => !groupAlive(first.pid) && !pidAlive(grandchild)), 'leader and grandchild gone');
  assert.ok(!fs.readdirSync(S.RUN_DIR).some((n) => n.endsWith(`-${first.pid}.json`)), 'record removed');
  delete process.env.RELAYFORGE_PROJECT;
});

test('restart gives a new process; two requirements run side by side; a taken port fails plainly', async () => {
  const port = await freePort();
  const a = S.scopeOf('R-a'), b = S.scopeOf('R-b');
  const one = await S.launchOne(a, item('应用'), launch);
  const two = await S.launchOne(b, item('应用'), launch);
  assert.notEqual(one.pid, two.pid);
  assert.equal(one.status, 'running'); // 没配端口：进程活着就算运行中
  await S.stop({ scope: 'R-a', name: '应用' });
  const three = await S.launchOne(a, item('应用'), launch);
  assert.notEqual(three.pid, one.pid);
  assert.equal(b.services.get('应用').status, 'running'); // 别的需求的不受影响
  // 端口被占
  const busy = net.createServer().listen(port, '127.0.0.1');
  await new Promise((r) => busy.once('listening', r));
  const taken = await S.launchOne(a, item('后端', { port }), launch);
  assert.equal(taken.status, 'failed');
  assert.match(taken.reason, new RegExp(`端口 ${port} 已经被占用`));
  busy.close();
  await S.stop({ scope: 'R-a' }); await S.stop({ scope: 'R-b' });
  assert.ok(await until(() => !groupAlive(three.pid) && !groupAlive(two.pid)));
});

test('quick exit and a port that never connects are start failures; the process is not left behind', async () => {
  const sc = S.scopeOf('main');
  const quick = await S.launchOne(sc, { name: '坏的', command: 'echo boom; exit 3', cwd: TMP, port: null, accept: [] }, launch);
  assert.ok(['running', 'failed'].includes(quick.status));
  assert.ok(await until(() => sc.services.get('坏的').status === 'failed'));
  const s = sc.services.get('坏的');
  assert.equal(s.code, 3);
  assert.match(s.reason, /很快就退出了（退出码 3）/);
  const report = S.report('main', '坏的');
  assert.equal(report.code, 3);
  assert.match(report.tail, /boom/);
  const port = await freePort();
  const never = await S.launchOne(sc, item('不监听', { port }), { ...launch }); // 进程活着但不开这个端口
  const neverItem = { ...item('不监听2'), port: await freePort(), command: `exec node ${SERVER}` };
  const n2 = await S.launchOne(sc, neverItem, launch);
  assert.ok(await until(() => sc.services.get('不监听2').status === 'failed', 8000));
  assert.match(sc.services.get('不监听2').reason, /一直连不上，已经停掉/);
  assert.ok(await until(() => !groupAlive(n2.pid)), 'stopped after the wait');
  await S.stop({ scope: 'main' });
  assert.ok(await until(() => !groupAlive(never.pid)));
});

test('stopAll stops every group synchronously (quitting the app, switching projects)', async () => {
  const sc = S.scopeOf('R-q');
  const a = await S.launchOne(sc, item('一'), launch);
  const b = await S.launchOne(sc, item('二'), launch);
  assert.equal(S.stopAll(), 2);
  assert.ok(await until(() => !groupAlive(a.pid) && !groupAlive(b.pid)), 'groups gone (the leaders are reaped once the event loop runs)');
  assert.equal(S.running('R-q'), false);
  assert.ok(![...S.scopes.keys()].some((k) => k.endsWith('|R-q')), 'scope dropped: no logs or plaintext secrets left in memory');
});

test('a finished requirement\'s services leave no logs or secrets in memory', async () => {
  const sc = S.scopeOf('R-done');
  await S.launchOne(sc, item('一'), launch);
  assert.ok(sc.secrets.includes(SECRET));
  await S.stopScope('R-done');
  assert.deepEqual(sc.secrets, []);
  assert.deepEqual(sc.logs, []);
  assert.equal(sc.env, null);
  assert.equal(sc.services.get('一').status, 'idle'); // 服务的列表留着：界面上照样显示「没启动」
});

test('left-over processes of a crashed app are stopped on the next start; others are not touched', async () => {
  fs.mkdirSync(S.RUN_DIR, { recursive: true });
  const orphan = spawn('sh', ['-c', 'sleep 300 & sleep 300'], { detached: true, stdio: 'ignore' });
  orphan.unref();
  await wait(200);
  const dead = spawnSync('sh', ['-c', 'echo $$']).stdout.toString().trim(); // 一个已经退出的进程：当作崩掉的应用
  fs.writeFileSync(path.join(S.RUN_DIR, `${dead}-${orphan.pid}.json`), JSON.stringify(RS.sign({ owner: Number(dead), pid: orphan.pid, start: S.startTime(orphan.pid), scope: 'R-x', service: '应用', command: 'sleep' })));
  // 启动时间对不上（pid 被别的进程复用了）：不碰
  const other = spawn('sh', ['-c', 'sleep 300'], { detached: true, stdio: 'ignore' });
  other.unref();
  await wait(100);
  fs.writeFileSync(path.join(S.RUN_DIR, `${dead}-${other.pid}.json`), JSON.stringify(RS.sign({ owner: Number(dead), pid: other.pid, start: 'Mon Jan  1 00:00:00 2001', scope: 'R-y', service: '应用' })));
  // 主人还活着（另一个开着的应用）：不碰
  const mine = spawn('sh', ['-c', 'sleep 300'], { detached: true, stdio: 'ignore' });
  mine.unref();
  await wait(100);
  const owner = spawn('sleep', ['300'], { stdio: 'ignore' });
  await wait(100);
  fs.writeFileSync(path.join(S.RUN_DIR, `${owner.pid}-${mine.pid}.json`), JSON.stringify(RS.sign({ owner: owner.pid, owner_start: S.startTime(owner.pid), pid: mine.pid, start: S.startTime(mine.pid), scope: 'R-z', service: '应用' })));
  // 不是应用写的（会话伪造的：启动时间读得到，签名对不上）：不碰，记录删掉
  const victim = spawn('sh', ['-c', 'sleep 300'], { detached: true, stdio: 'ignore' });
  victim.unref();
  await wait(100);
  const forged = path.join(S.RUN_DIR, `${dead}-${victim.pid}.json`);
  fs.writeFileSync(forged, JSON.stringify({ owner: Number(dead), pid: victim.pid, start: S.startTime(victim.pid), scope: 'R-f', service: '应用', log: '/tmp/x/services.log' }));
  const cleaned = S.sweep();
  assert.deepEqual(cleaned.map((c) => c.pid), [orphan.pid]);
  assert.ok(pidAlive(victim.pid), 'a forged record does not get a process killed');
  assert.ok(!fs.existsSync(forged));
  process.kill(-victim.pid, 'SIGKILL');
  assert.ok(await until(() => !groupAlive(orphan.pid)), 'the orphan group is gone');
  assert.ok(pidAlive(other.pid), 'a reused pid is left alone');
  assert.ok(pidAlive(mine.pid), 'a living owner keeps its processes');
  assert.ok(!fs.existsSync(path.join(S.RUN_DIR, `${dead}-${orphan.pid}.json`)));
  for (const p of [other.pid, mine.pid]) process.kill(-p, 'SIGKILL');
  owner.kill('SIGKILL');
  for (const n of fs.readdirSync(S.RUN_DIR)) fs.unlinkSync(path.join(S.RUN_DIR, n));
});

test('port probe and scrub', async () => {
  const port = await freePort();
  assert.equal(await S.probe(port), false);
  const srv = net.createServer().listen(port, '127.0.0.1');
  await new Promise((r) => srv.once('listening', r));
  assert.equal(await S.probe(port), true);
  srv.close();
  assert.equal(S.scrub('a hunter2 b hunter2', ['hunter2']), 'a •••••• b ••••••');
  assert.equal(S.scrub('xy', ['xy']), 'xy'); // 太短的不打码（和 rf.py 一样）
});

test('start asks before commands not agreed to, runs due preparation steps, strips secrets from what the window gets', async () => {
  const calls = [];
  let launchOut = { scope: 'R-s', place: TMP, env: { id: 'ENV-001', name: '本机' }, services: [{ name: '应用', command: `exec node ${SERVER}`, cwd: TMP, port: null, accept: [], approved: false }],
    prep: [{ id: 'P1', due: true, approved: true }], needs: { services: ['应用'], prep: [] }, vars: { DB_PASSWORD: SECRET }, secrets: [SECRET], log: path.join(TMP, 's.log'), why: '' };
  const mocks = {
    './backend': { uiText: locale.uiText,  rf: async (args) => { calls.push(args.join(' ')); if (args[1] === 'launch') return { ok: true, stdout: JSON.stringify(launchOut) }; if (args[1] === 'prep-run') return { ok: true, stdout: JSON.stringify({ ran: [{ id: 'P1', note: '装依赖', ok: true, ms: 3 }], error: null }) }; return { ok: false, stderr: 'x' }; },
      cleanEnv: () => ({ ...process.env }), lastLine: (x) => x, parse: (t, f) => { try { return JSON.parse(t); } catch (_) { return f; } } },
    './state': { state: { project: '/p' }, send() {} }, './config': { RF_HOME: path.join(TMP, 'rfhome2') },
    './codestate': require(path.join(main, 'codestate')), './svc-history': require(path.join(main, 'svc-history')),
  };
  const context = vm.createContext({ require: (id) => id === './locale' ? locale : mocks[id] || require(id), module: { exports: {} }, process, setTimeout, clearTimeout, Atomics, SharedArrayBuffer, Int32Array, Date, Promise, JSON, Buffer });
  vm.runInContext(fs.readFileSync(path.join(main, 'services.js'), 'utf8'), context);
  const svc = context.module.exports;
  const asked = await svc.start({ scope: 'R-s' });
  assert.deepEqual(JSON.parse(JSON.stringify(asked.needs)), { services: ['应用'], prep: [] });
  assert.ok(!JSON.stringify(asked).includes(SECRET) && !('vars' in asked.plan) && !('secrets' in asked.plan));
  assert.ok(!calls.some((c) => c.includes('prep-run')), 'nothing runs before you agree');
  launchOut = { ...launchOut, services: [{ ...launchOut.services[0], approved: true }], needs: { services: [], prep: [] } };
  const ran = await svc.start({ scope: 'R-s', copy: 'fresh' });
  assert.ok(ran.ok, JSON.stringify(ran));
  assert.ok(calls.some((c) => c.startsWith('env launch --req R-s --copy fresh')));
  assert.ok(calls.some((c) => c === 'env prep-run ENV-001 --req R-s --due --json'));
  assert.equal(ran.started[0].status, 'running');
  await svc.stop({ scope: 'R-s' });
  assert.ok(await until(() => !groupAlive(ran.started[0].pid)));
});

test('a wrong one goes back to the development session with the log tail; the right one just records', async () => {
  const told = [], calls = [];
  const mocks = {
    './backend': { uiText: locale.uiText,  rf: async (args, _r, _a, _t, input) => { calls.push({ args, input }); return { ok: true, stdout: JSON.stringify({ id: 'R-1', n: Number(args[3]), result: args[4], say: args[4] === 'bad' ? '【人工验收：第 2 条验收条件不对】…' : undefined }) }; },
      lastLine: (x) => x, parse: JSON.parse },
    './services': { tail: () => '10:00:00 [应用] boom', runLine: async () => '' },
    './testsets': { tell: (run, text) => told.push([run, text]) }, './requirements': { pushReqs() {} }, './issues': { newIssue: async (x) => ({ ...x, num: '007' }) },
  };
  const context = vm.createContext({ require: (id) => id === './locale' ? locale : mocks[id] || require(id), module: { exports: {} } });
  vm.runInContext(fs.readFileSync(path.join(main, 'acceptance.js'), 'utf8'), context);
  const A = context.module.exports;
  assert.match((await A.mark({ req: 'R-1', n: 2, result: 'bad', say: '' })).error, /写一句/);
  await A.mark({ req: 'R-1', n: 1, result: 'ok' });
  assert.deepEqual([...calls[0].args], ['req', 'accept', 'R-1', '1', 'ok', '--json']);
  assert.equal(told.length, 0);
  const bad = await A.mark({ req: 'R-1', n: 2, result: 'bad', say: '点了没反应' });
  assert.deepEqual([...calls[1].args], ['req', 'accept', 'R-1', '2', 'bad', '--say', '点了没反应', '--file', '-', '--json']);
  assert.equal(calls[1].input, '10:00:00 [应用] boom');
  assert.ok(bad.log);
  assert.deepEqual(told, [['R-1', '【人工验收：第 2 条验收条件不对】…']]);
  const made = await A.issue({ scope: 'main', text: '列表空了' });
  assert.equal(made.title, '列表空了');
  assert.match(made.desc, /对着主线.*列表空了[\s\S]*boom/);
});

test('sessions may look at the plan but not launch, clean up or mark acceptance', () => {
  state.project = fs.mkdtempSync(path.join(TMP, 'p-'));
  const { hooks } = require(path.join(main, 'claude-settings'));
  const { BUNDLED } = require(path.join(main, 'config'));
  for (const [name, text] of Object.entries({ building: hooks(null, false, false, true, false, false, false, { role: 'develop' }), tester: hooks(null, false, false, false, false, false, false, { role: 'test', place: '/w' }), chat: hooks(null, false), fix: hooks(null, false, false, false, false, false, true) })) {
    const { allow, deny } = JSON.parse(text).permissions;
    for (const rf of [BUNDLED, '.relayforge/bin/rf.py']) {
      assert.ok(deny.includes(`Bash(python3 ${rf} req accept:*)`), `${name} req accept`);
      assert.ok(deny.includes(`Bash(python3 ${rf} env launch:*)`), `${name} env launch`);
      assert.ok(deny.includes(`Bash(python3 ${rf} env accept-clean:*)`), `${name} env accept-clean`);
      assert.ok(!allow.includes(`Bash(python3 ${rf} env launch:*)`));
      assert.equal(allow.includes(`Bash(python3 ${rf} env plan:*)`), ['building', 'tester', 'fix'].includes(name), `${name} env plan`);
    }
  }
});

test('bench model: status words, rows, what this start uses, the consent box, the per-condition mark, the run button', () => {
  assert.equal(BM.statusOf(null).text, '没启动');
  assert.equal(BM.statusOf({ status: 'starting', port: 8080 }).text, '正在启动 · 等端口 8080 可连');
  assert.equal(BM.statusOf({ status: 'running', port: 8080 }).text, '运行中 :8080');
  assert.equal(BM.statusOf({ status: 'exited', code: 1 }).text, '已退出（退出码 1）');
  assert.equal(BM.statusOf({ status: 'failed' }).cls, 'r');
  const plan = { env: { name: '本机' }, place: '/w', services: [{ name: '应用', command: 'npm start', cwd: '/w/app', accept: [{ name: 'RELAYFORGE_USER_DATA', value: '/s/userdata', missing: [] }], opens: '演示项目', uses_copy: false },
    { name: '后端', command: 'x', cwd: '/w', port: 8080, accept: [] }], prep: [{ id: 'P1', n: 1, note: '装依赖', command: 'npm ci', abs_dir: '/w/app', due_why: 'app/package-lock.json 变了' }] };
  const rows = BM.rows(plan, [{ name: '应用', status: 'running' }, { name: '旧的', status: 'running' }, { name: '没了', status: 'exited' }]);
  assert.deepEqual(rows.map((r) => r.name), ['应用', '后端', '旧的']);
  assert.deepEqual(BM.planLines(plan.services[0]).map((x) => x.k + '=' + x.v), ['打开的是=演示项目', 'RELAYFORGE_USER_DATA=/s/userdata']);
  const box = BM.needsHtml({ services: ['应用'], prep: ['P1'] }, plan);
  for (const w of ['启动服务「应用」', 'npm start', '/w/app', 'RELAYFORGE_USER_DATA=/s/userdata', 'npm ci', 'app/package-lock.json 变了', 'AI 的会话不能替你同意']) assert.ok(box.includes(w), w);
  assert.ok(!box.includes('后端'));
  assert.equal(BM.markOf({ accept: null }).state, 'none');
  assert.match(BM.markOf({ accept: { result: 'ok', at: '2026-11-19 10:20' } }).text, /你点了通过 · 11-19 10:20/);
  assert.match(BM.markOf({ accept: { result: 'bad', at: '2026-11-19 10:20', say: '没反应' } }).text, /上次说这里不对.*没反应/);
  assert.equal(BM.runButton({ services: 2 }).label, '去人工验收');
  assert.equal(BM.runButton({ services: 0 }).label, '跑起来让我验收');
  assert.equal(BM.runButton(null).bench, false);
  const lines = BM.append([], Array.from({ length: 30 }, (_, i) => ({ t: 0, s: i % 2 ? 'a' : 'b', x: String(i) })), 20);
  assert.equal(lines.length, 20);
  assert.equal(BM.filter(lines, 'a').length, 10);
  assert.match(BM.lineHtml({ t: Date.now(), s: '<x>', k: 'err', x: 'a<b' }), /class="bn-l err".*&lt;x&gt;.*a&lt;b/);
});

test('RelayForge as an acceptance instance: data directory, window tag, demo project', () => {
  const env = { ...process.env, RELAYFORGE_USER_DATA: path.join(TMP, 'ud'), RELAYFORGE_WINDOW_TAG: '验收用 · 需求 R-9', RELAYFORGE_HOME: path.join(TMP, 'h2') };
  const out = spawnSync(process.execPath, ['-e', `const c = require(${JSON.stringify(path.join(main, 'config'))}); console.log(JSON.stringify({ u: c.USER_DATA, t: c.WINDOW_TAG, title: c.windowTitle('demo'), home: c.RF_HOME }))`], { env, encoding: 'utf8' });
  const c = JSON.parse(out.stdout);
  assert.equal(c.u, path.join(TMP, 'ud'));
  assert.equal(c.title, '验收用 · 需求 R-9 · RelayForge · demo');
  assert.equal(c.home, path.join(TMP, 'h2'));
  const plain = JSON.parse(spawnSync(process.execPath, ['-e', `const c = require(${JSON.stringify(path.join(main, 'config'))}); console.log(JSON.stringify({ u: c.USER_DATA, title: c.windowTitle('p') }))`], { env: { ...process.env, RELAYFORGE_USER_DATA: '', RELAYFORGE_WINDOW_TAG: '' }, encoding: 'utf8' }).stdout);
  assert.deepEqual(plain, { u: null, title: 'RelayForge · p' });
  // 入口：userData 在 ready 之前设、在第一次读之前设；页面的 <title> 不盖掉带标记的标题
  const index = fs.readFileSync(path.join(main, 'index.js'), 'utf8');
  assert.ok(index.indexOf("app.setPath('userData', USER_DATA)") > 0 && index.indexOf("app.setPath('userData', USER_DATA)") < index.indexOf('app.whenReady'));
  assert.ok(index.includes("win.on('page-title-updated', (e) => e.preventDefault())"));
  assert.ok(index.includes('windowTitle(path.basename(state.project))'));
});

test('RELAYFORGE_PROJECT=demo opens the demo project, never the last real one', async () => {
  const dir = fs.mkdtempSync(path.join(TMP, 'ud-'));
  let ensured = 0;
  const last = fs.mkdtempSync(path.join(TMP, 'last-'));
  fs.writeFileSync(path.join(dir, 'state.json'), JSON.stringify({ project: last }));
  const mocks = {
    electron: { app: { getPath: () => dir }, dialog: {}, Menu: {} },
    './config': { HOME_REPO: last, RF_HOME_REAL: '/x', YES: true, USER_DATA: dir, WINDOW_TAG: 't', windowTitle: (n) => n },
    './backend': { uiText: locale.uiText,  exec: async () => ({ ok: true, stdout: '' }), rfBundled: async () => ({ ok: true, stdout: '[]' }), lastLine: (x) => x },
    './state': { state: {}, send() {} }, './requirements': { pushReqs() {} }, './sessions': { stopAll() {} }, './initialization': { get: () => null },
    './app-menu': { refresh() {}, setWindowReady() {} },
    './demo': { ensure: async () => { ensured += 1; return '/demo/flownote'; }, info: () => null },
  };
  const old = process.env.RELAYFORGE_PROJECT;
  process.env.RELAYFORGE_PROJECT = 'demo';
  try {
    const context = vm.createContext({ require: (id) => id === './locale' ? locale : Object.hasOwn(mocks, id) ? mocks[id] : require(id), module: { exports: {} }, process, Buffer, setTimeout, clearTimeout, global: {} });
    vm.runInContext(fs.readFileSync(path.join(main, 'project.js'), 'utf8'), context);
    assert.equal(await context.module.exports.choose(), '/demo/flownote');
    assert.equal(ensured, 1);
  } finally { if (old === undefined) delete process.env.RELAYFORGE_PROJECT; else process.env.RELAYFORGE_PROJECT = old; }
});

test.after(() => { S.stopAll(); });
