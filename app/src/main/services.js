// 人工验收的服务（需求下的「人工验收」标签、测试页的「人工测试」）：启动、停止、重启、等端口可连、收日志。
// 进程由这里直接管（Node 自带的 child_process），不经过模型。怎么启动（命令、目录、环境变量、验收时额外带的变量、同意了没有、
// 要执行的准备步骤）由 rf.py env launch 算好给出（要应用的标记，带保密值）；这里只照着做。
//   - 范围（scope）：需求编号（在它的工作目录里跑）或 main（项目主目录）。同一个范围的同一个服务只有一个实例；不同需求各起各的。
//   - 进程组：spawn 时 detached，子进程自己是一个进程组的组长；停的时候对负 pid 发信号，整组（npm → node → electron …）一起停。
//   - 遗留进程：每启动一个就在 $RELAYFORGE_HOME/run/services/ 记一份（应用的 pid、进程的 pid、ps 给出的启动时间）；正常停掉就删。
//     应用异常退出后，下次启动时（sweep）发现记录的主人已经不在了，进程还在而且启动时间对得上（不是被别的进程复用了 pid），就把整组停掉。
//   - 日志：标准输出和标准错误合在一起按时间排，内存里每个范围留最近若干行，同时追加到本机的日志文件（rf.py 给的位置，不在仓库里）；
//     保密值先打码再显示、再写文件。
//   - 代码的状态（codestate.js）：服务启动（含重启）时记下工作目录的 HEAD 提交和未提交改动的指纹；之后代码一变，这个服务就标成「启动后代码改过」。
//     检查的时机：watcher 每隔一会儿（只标记）、打开人工验收标签时、开发会话一轮停下时。
//   - 自动重启（每个服务有开关，默认开）：只在两个时机，不在 AI 改代码的中途——开发会话一轮停下时、打开人工验收标签时；
//     服务在运行、代码比启动时新才重启；启动命令或准备步骤要重新同意就不重启，只提示；同一个服务不并发重启。
//   - 启动记录（svc-history.js）：每个服务一份，存本机，动作、时间、原因、提交编号、退出码、用了多久。
//   - 端口：配了端口的，启动前先看端口是不是已经被占（被占如实报失败），启动后每隔一会儿做 TCP 连接，连上才算运行中，一直连不上算失败并停掉；
//     没配端口的（比如桌面应用）进程活着就算运行中，启动后很快就退出的算启动失败。
const { uiText } = require('./locale');
const { spawn, execFileSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');
const { rf, cleanEnv, lastLine, parse } = require('./backend');
const { state, send } = require('./state');
const { RF_HOME } = require('./config');
const codestate = require('./codestate');
const hist = require('./svc-history');

const MAX_LINES = 2000; // 内存里每个范围留的日志行数
const QUICK_MS = Number(process.env.RELAYFORGE_QUICK_MS || 5000); // 启动后这么快就退出的，算启动失败
const PORT_WAIT_MS = Number(process.env.RELAYFORGE_PORT_WAIT_MS || 90000); // 等端口可连最多等多久
const PROBE_MS = 400;
const STOP_GRACE_MS = 3000; // 先 SIGTERM，过这么久还在就 SIGKILL
const MASK = '••••••';
const SCOPE = /^(main|R-[\w-]+)$/;
const RUN_DIR = path.join(RF_HOME, 'run', 'services');
const MARK_MS = Number(process.env.RELAYFORGE_MARK_MS || 5000); // watcher 里两次检查代码有没有变的最短间隔
const WHY = { turn: '开发会话改了代码', open: '打开人工验收时发现代码改过' }; // 自动重启的原因
const STATUS_WORDS = { idle: '没启动', starting: '正在启动', running: '运行中', stopping: '正在停止', exited: '已退出', failed: '启动失败' };

const scopes = new Map(); // 项目|范围 -> { project, scope, services: Map(名称 -> 服务), logs: [], file, secrets: [], env }

const keyOf = (project, scope) => project + '|' + scope;
function scopeOf(scope, project = state.project) {
  const k = keyOf(project, scope);
  if (!scopes.has(k)) scopes.set(k, { project, scope, services: new Map(), logs: [], file: null, secrets: [], env: null, pending: [], flush: null, place: null, restarting: new Set() });
  return scopes.get(k);
}
const pad = (n) => String(n).padStart(2, '0');
const clock = (t) => { const d = new Date(t); return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()); };

// ---------- 打码 ----------
function scrub(text, secrets) {
  let out = String(text);
  for (const s of secrets || []) if (s && s.length >= 3 && out.includes(s)) out = out.split(s).join(MASK);
  return out;
}

// ---------- 日志 ----------
function log(sc, svc, text, kind = 'out') {
  const line = { t: Date.now(), s: svc, k: kind, x: scrub(text, sc.secrets) };
  sc.logs.push(line);
  if (sc.logs.length > MAX_LINES) sc.logs.splice(0, sc.logs.length - MAX_LINES);
  if (sc.file) { try { fs.mkdirSync(path.dirname(sc.file), { recursive: true, mode: 0o700 }); fs.appendFileSync(sc.file, `${new Date(line.t).toISOString()} [${svc}]${kind === 'sys' ? ' ·' : ''} ${line.x}\n`, { mode: 0o600 }); } catch (_) { /* 写不了日志文件不挡运行 */ } }
  sc.pending.push(line);
  if (!sc.flush) sc.flush = setTimeout(() => { sc.flush = null; const lines = sc.pending.splice(0); if (lines.length) send('svc:log', { scope: sc.scope, project: sc.project, lines }); }, 120);
}
// 按行切：一行没收完先攒着（保密值不会被切成两半漏打码）；太长的一段照样放出去
function lineReader(sc, svc, kind) {
  let rest = '';
  const take = (chunk) => {
    rest += chunk.toString('utf8');
    const parts = rest.split(/\r?\n/);
    rest = parts.pop();
    for (const p of parts) log(sc, svc, p.replace(/\r/g, ''), kind);
    if (rest.length > 8192) { log(sc, svc, rest, kind); rest = ''; }
  };
  take.end = () => { if (rest) { log(sc, svc, rest, kind); rest = ''; } };
  return take;
}
function tail(scope, n = 80, name) {
  const sc = scopes.get(keyOf(state.project, scope));
  if (!sc) return '';
  return sc.logs.filter((l) => !name || l.s === name).slice(-n).map((l) => `${clock(l.t)} [${l.s}] ${l.x}`).join('\n');
}
function clearLogs({ scope, name } = {}) {
  if (!SCOPE.test(String(scope || ''))) return { error: uiText('不认识的范围。', "Unrecognized scope.") };
  const sc = scopeOf(scope);
  sc.logs = name ? sc.logs.filter((l) => l.s !== name) : [];
  push(sc);
  return { ok: true };
}

// ---------- 进程的记录（遗留进程的清理用） ----------
function startTime(pid) {
  try { return execFileSync('ps', ['-o', 'lstart=', '-p', String(pid)], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 3000 }).trim(); } catch (_) { return ''; }
}
function alive(pid) {
  try { process.kill(pid, 0); return true; } catch (error) { return error.code === 'EPERM'; }
}
let selfStart = null;
const recordFile = (pid) => path.join(RUN_DIR, `${process.pid}-${pid}.json`);
function record(svc, sc) {
  try {
    fs.mkdirSync(RUN_DIR, { recursive: true, mode: 0o700 });
    if (selfStart === null) selfStart = startTime(process.pid);
    fs.writeFileSync(recordFile(svc.pid), JSON.stringify(require('./record-sign').sign({ owner: process.pid, owner_start: selfStart, pid: svc.pid, start: svc.lstart, scope: sc.scope, project: sc.project, service: svc.name, command: svc.command, log: sc.file || null, head: (svc.codeStart && svc.codeStart.short) || '', dirty: !!(svc.codeStart && svc.codeStart.dirty), at: new Date().toISOString() })), { mode: 0o600 }); // 签名：sweep 只认应用自己写的（record-sign.js）
  } catch (_) { /* 记不下：只是下次没法替你清理 */ }
}
function unrecord(pid) { try { fs.unlinkSync(recordFile(pid)); } catch (_) { /* 已经没了 */ } }
// 应用启动时：以前没退干净的应用留下的进程（记录的主人不在了），进程还在、启动时间对得上的，整组停掉
function sweep() {
  const cleaned = [];
  let names = [];
  try { names = fs.readdirSync(RUN_DIR).filter((n) => n.endsWith('.json')); } catch (_) { return cleaned; }
  for (const name of names) {
    const file = path.join(RUN_DIR, name);
    let r;
    try { r = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (_) { try { fs.unlinkSync(file); } catch (__) { /* 别的应用刚删 */ } continue; }
    // 签名对不上（不是应用自己写的、或者被改过）：不照着杀进程、也不往它说的日志里写，删掉
    if (!require('./record-sign').verify(r)) { try { fs.unlinkSync(file); } catch (_) { /* 已经没了 */ } continue; }
    if (r.owner === process.pid) continue;
    if (r.owner && alive(r.owner) && (!r.owner_start || startTime(r.owner) === r.owner_start)) continue; // 主人（另一个开着的应用）还在：它自己管
    if (r.pid && alive(r.pid) && r.start && startTime(r.pid) === r.start) {
      killGroup(r.pid, 'SIGTERM');
      const until = Date.now() + 1500;
      while (groupAlive(r.pid) && Date.now() < until) sleep(50);
      killGroup(r.pid, 'SIGKILL');
      cleaned.push({ pid: r.pid, service: r.service, scope: r.scope, project: r.project, command: r.command });
      if (r.log) hist.append(r.log, r.service, { action: '清理遗留进程', reason: '上次应用没退干净，进程还在', head: r.head, dirty: r.head ? r.dirty : null });
    }
    try { fs.unlinkSync(file); } catch (_) { /* 已经没了 */ }
  }
  return cleaned;
}
function groupAlive(pid) { try { process.kill(-pid, 0); return true; } catch (error) { return error.code === 'EPERM'; } }
function sleep(ms) { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); }
// 只对进程组发信号（负 pid）：组已经空了就什么都不做，不会退回去按 pid 杀（pid 可能已经被别的进程复用）
function killGroup(pid, sig) {
  try { process.kill(-pid, sig); return true; } catch (_) { return false; }
}

// ---------- 端口 ----------
function probe(port, host = '127.0.0.1', timeout = 600) {
  return new Promise((done) => {
    const sock = net.connect({ port, host });
    const end = (ok) => { sock.removeAllListeners(); sock.destroy(); done(ok); };
    sock.setTimeout(timeout, () => end(false));
    sock.once('connect', () => end(true));
    sock.once('error', () => end(false));
  });
}

// ---------- 服务的状态 ----------
function publicOf(svc) {
  return { name: svc.name, status: svc.status, words: STATUS_WORDS[svc.status] + (svc.status === 'exited' ? `（${svc.code != null ? '退出码 ' + svc.code : '信号 ' + (svc.signal || '?')}）` : ''),
    code: svc.code, signal: svc.signal || null, reason: svc.reason || '', port: svc.port || null, pid: svc.pid || null, cwd: svc.cwd || '', command: svc.command || '',
    startedAt: svc.startedAt || null, waitingPort: svc.status === 'starting' && !!svc.port, accept: svc.accept || [],
    // 启动时的代码（短提交编号、有没有未提交的改动）、启动后代码改过没有、要不要自动重启、自动重启是不是因为要重新同意而没做
    head: (svc.codeStart && svc.codeStart.short) || '', dirty: !!(svc.codeStart && svc.codeStart.dirty), stale: !!svc.stale, autoRestart: svc.autoRestart !== false, consent: !!svc.consent };
}
function viewOf(sc) {
  return { scope: sc.scope, project: sc.project, env: sc.env, file: sc.file, rev: sc.rev || 0, services: [...sc.services.values()].map(publicOf), histories: hist.all(sc.file) };
}
// 一条启动记录：动作、原因、当时代码的提交编号（短）和有没有未提交的改动、退出码、从启动到可用花了多久。保密值打码
function note(sc, svc, action, reason, extra = {}) {
  const c = svc.codeStart || {};
  return hist.append(sc.file, svc.name, { action, reason, head: c.short, dirty: c.short ? c.dirty : null, ...extra }, (t) => scrub(t, sc.secrets));
}
// rev 每变一次加一：界面拿到的状态比手里的旧（先发出的查询、后到的回答）就不用它
function push(sc) { sc.rev = (sc.rev || 0) + 1; send('svc:update', viewOf(sc)); }
function setStatus(sc, svc, status, reason) {
  svc.status = status;
  if (reason !== undefined) svc.reason = reason;
  push(sc);
}

// 照着 rf.py env launch 给的设置启动一个服务。item：{ name, command, cwd, port, accept: [{name, value}] }；launch：{ vars, secrets, log }
async function launchOne(sc, item, launch, how = {}) {
  const old = sc.services.get(item.name);
  if (old && ['starting', 'running', 'stopping'].includes(old.status)) return publicOf(old); // 同一个服务只有一个实例
  const svc = { name: item.name, command: item.command, cwd: item.cwd, port: item.port || null, accept: item.accept || [], status: 'starting', code: null, signal: null, reason: '', pid: null,
    autoRestart: item.auto_restart !== false, how: { action: how.action || '启动', reason: how.reason === undefined ? '你点的' : how.reason }, place: item.place || (launch && launch.place) || sc.place, stale: false, consent: false };
  sc.services.set(item.name, svc);
  sc.secrets = [...new Set([...(sc.secrets || []), ...((launch && launch.secrets) || [])])].sort((a, b) => b.length - a.length);
  if (launch && launch.log) sc.file = launch.log;
  push(sc);
  svc.codeStart = await codestate.stateOf(svc.place).catch(() => null); // 启动那一刻的代码：之后和它比
  if (svc.port && await probe(svc.port)) {
    svc.reason = uiText("端口 {0} 已经被占用了：可能是另一个需求的验收实例、或者别的程序在用它。先停掉占用它的，再启动。", "Port {0} is already in use by another review instance or process. Stop the process using it and try again.", svc.port);
    log(sc, svc.name, svc.reason, 'sys');
    return failed(sc, svc);
  }
  if (!fs.existsSync(svc.cwd)) { svc.reason = uiText("目录 {0} 不存在。", "Directory {0} does not exist.", svc.cwd); return failed(sc, svc); }
  // 环境：应用自己的环境去掉会话标记和 RelayForge 自己的变量（不能让验收实例拿到正式实例的项目、数据目录和标记），再加上这套环境的变量和验收时的变量
  const env = cleanEnv();
  for (const k of Object.keys(env)) if (k.startsWith('RELAYFORGE_')) delete env[k];
  Object.assign(env, (launch && launch.vars) || {});
  for (const a of svc.accept) env[a.name] = a.value;
  log(sc, svc.name, uiText("启动：{0}（在 {1}{2}）", "Starting {0} in {1}{2}", svc.command, svc.cwd, svc.port ? uiText('，等端口 ', "; waiting for port ") + svc.port + uiText(' 可连', " to open") : ''), 'sys');
  let child;
  try {
    child = spawn(svc.command, { shell: true, cwd: svc.cwd, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (error) {
    svc.reason = uiText('没能启动：', "Could not start: ") + error.message; log(sc, svc.name, svc.reason, 'sys'); return failed(sc, svc);
  }
  svc.child = child; svc.pid = child.pid; svc.startedAt = Date.now();
  if (svc.pid) { svc.lstart = startTime(svc.pid); record(svc, sc); }
  const out = lineReader(sc, svc.name, 'out'), err = lineReader(sc, svc.name, 'err');
  child.stdout.on('data', out); child.stderr.on('data', err);
  child.on('error', (error) => { log(sc, svc.name, uiText('没能启动：', "Could not start: ") + error.message, 'sys'); });
  child.on('exit', (code, signal) => {
    out.end(); err.end();
    clearTimeout(svc.timer); svc.timer = null;
    if (svc.pid) unrecord(svc.pid);
    // 进程组里还有别的进程（组长退出了，孙子还在）：一起停掉，不留孤儿
    if (svc.pid) killGroup(svc.pid, 'SIGTERM');
    svc.code = code; svc.signal = signal; svc.child = null;
    if (sc.services.get(svc.name) !== svc) return;
    const quick = Date.now() - svc.startedAt < QUICK_MS;
    if (svc.status === 'stopping' || svc.stopped) { log(sc, svc.name, uiText('已停止。', "Stopped."), 'sys'); svc.reason = ''; if (svc.stopHow) note(sc, svc, svc.stopHow.action, svc.stopHow.reason, { code, signal }); setStatus(sc, svc, 'idle'); return; }
    if (svc.status === 'failed') { push(sc); return; } // 等端口超时，已经停掉
    if (svc.status === 'starting' || quick) {
      svc.reason = svc.port && svc.status === 'starting' ? uiText("端口 {0} 还没连上，进程就退出了（{1}）。", "The process exited before port {0} opened ({1}).", svc.port, code != null ? uiText('退出码 ', "exit code ") + code : uiText('信号 ', "signal ") + signal) : uiText("启动以后很快就退出了（{0}）。", "The process exited shortly after starting ({0}).", code != null ? uiText('退出码 ', "exit code ") + code : uiText('信号 ', "signal ") + signal);
      log(sc, svc.name, svc.reason, 'sys');
      failed(sc, svc, { code, signal });
      return;
    }
    log(sc, svc.name, uiText("进程退出了（{0}）。", "The process exited ({0}).", code != null ? uiText('退出码 ', "exit code ") + code : uiText('信号 ', "signal ") + signal), 'sys');
    note(sc, svc, '自己退出', '', { code, signal });
    setStatus(sc, svc, 'exited', '');
  });
  if (!svc.port) { ready(sc, svc); return publicOf(svc); }
  const deadline = Date.now() + PORT_WAIT_MS;
  const tick = async () => {
    svc.timer = null;
    if (sc.services.get(svc.name) !== svc || svc.status !== 'starting') return;
    if (await probe(svc.port)) {
      if (svc.status !== 'starting') return;
      log(sc, svc.name, uiText("端口 {0} 可以连上了。", "Port {0} is open.", svc.port), 'sys');
      return ready(sc, svc);
    }
    if (Date.now() > deadline) {
      svc.reason = uiText("等了 {0} 秒，端口 {1} 一直连不上，已经停掉。", "Stopped after {0} seconds because port {1} did not open.", Math.round(PORT_WAIT_MS / 1000), svc.port);
      log(sc, svc.name, svc.reason, 'sys');
      failed(sc, svc);
      return terminate(svc);
    }
    svc.timer = setTimeout(tick, PROBE_MS);
  };
  svc.timer = setTimeout(tick, PROBE_MS);
  return publicOf(svc);
}

// 启动成功（端口连上了，或没配端口、进程活着）：记一条，状态变成运行中
function ready(sc, svc) {
  note(sc, svc, svc.how.action, svc.how.reason, { ms: Date.now() - svc.startedAt });
  setStatus(sc, svc, 'running');
}
// 启动失败：记一条，状态变成失败
function failed(sc, svc, extra = {}) {
  if (!svc.failNoted) { svc.failNoted = true; note(sc, svc, '启动失败', svc.reason, extra); }
  setStatus(sc, svc, 'failed');
  return publicOf(svc);
}

// 停：整组先 SIGTERM，过一会儿还在就 SIGKILL。等它真的退出再返回
function terminate(svc) {
  return new Promise((done) => {
    const child = svc.child;
    if (!child || child.exitCode !== null || child.signalCode !== null) { if (svc.pid) killGroup(svc.pid, 'SIGTERM'); return done(); }
    let finished = false;
    const end = () => { if (!finished) { finished = true; clearTimeout(hard); done(); } };
    child.once('exit', () => { if (svc.pid) killGroup(svc.pid, 'SIGKILL'); end(); });
    killGroup(svc.pid, 'SIGTERM');
    const hard = setTimeout(() => { killGroup(svc.pid, 'SIGKILL'); setTimeout(end, 500); }, STOP_GRACE_MS);
  });
}
// how：记进启动记录的 { action, reason }；不给（重启时先停再启，那一条由启动的那一步记）就不记停止
async function stopOne(sc, name, how = null) {
  const svc = sc.services.get(name);
  if (!svc || !svc.child) { if (svc && svc.status !== 'idle') { svc.reason = ''; setStatus(sc, svc, 'idle'); } return; }
  svc.stopHow = how;
  clearTimeout(svc.timer); svc.timer = null;
  if (svc.status !== 'failed') setStatus(sc, svc, 'stopping');
  log(sc, name, uiText('正在停止…', "Stopping…"), 'sys');
  await terminate(svc);
}

// ---------- 给界面的 ----------
const scopeArgs = (scope) => (scope && scope !== 'main' ? ['--req', scope] : []);
const strip = (launch) => { const out = { ...launch }; delete out.vars; delete out.secrets; return JSON.parse(scrub(JSON.stringify(out), launch.secrets || [])); };

function view({ scope } = {}) {
  if (!SCOPE.test(String(scope || ''))) return { error: uiText('不认识的范围。', "Unrecognized scope.") };
  const sc = scopeOf(scope);
  return { ...viewOf(sc), logs: sc.logs.slice(-800) };
}
// 这次会怎么启动（打码的，谁都能看）：界面在启动前显示「这次会用什么」
async function plan({ scope } = {}) {
  if (!SCOPE.test(String(scope || ''))) return { error: uiText('不认识的范围。', "Unrecognized scope.") };
  const out = await rf(['env', 'plan', ...scopeArgs(scope), '--json'], undefined, undefined, 60000);
  const made = out.ok ? parse(out.stdout, null) : null;
  if (!made) return { error: lastLine(out.stderr) };
  const sc = scopeOf(scope);
  sc.env = made.env;
  if (made.log && !sc.file) sc.file = made.log; // 启动记录在日志文件旁边：没启动过也能看到以前的
  if (made.place) sc.place = made.place;
  return { ...made, running: viewOf(sc).services, histories: hist.all(sc.file) };
}

// 启动（全部，或几个）：先问 rf.py 要启动的设置（要应用的标记）；有没同意的启动命令或准备步骤，交回界面去问你；同意过的就先执行要执行的准备步骤，再一个个启动
// 界面点的「启动」「重启」：记「启动」或「重启」，原因是你点的
function start({ scope, names, copy, action } = {}) { return begin({ scope, names, copy }, { action: action === '重启' ? '重启' : '启动', reason: '你点的' }); }
async function begin({ scope, names, copy } = {}, how) {
  if (!SCOPE.test(String(scope || ''))) return { error: uiText('不认识的范围。', "Unrecognized scope.") };
  const want = Array.isArray(names) && names.length ? names.map(String) : null;
  const out = await rf(['env', 'launch', ...scopeArgs(scope), ...(want || []).flatMap((n) => ['--service', n]), '--copy', copy === 'fresh' ? 'fresh' : 'reuse', '--json'], undefined, undefined, 10 * 60000);
  const launch = out.ok ? parse(out.stdout, null) : null;
  if (!launch) return { error: lastLine(out.stderr) };
  const sc = scopeOf(scope);
  sc.env = launch.env;
  if (launch.log) sc.file = launch.log;
  if (launch.place) sc.place = launch.place;
  sc.secrets = [...new Set([...(sc.secrets || []), ...(launch.secrets || [])])].sort((a, b) => b.length - a.length);
  if (!launch.env || !launch.services.length) return { plan: strip(launch), error: launch.why || uiText('没有可以启动的服务。', "There are no services to start.") };
  const todo = launch.services.filter((s) => !s.problem && !['starting', 'running', 'stopping'].includes((sc.services.get(s.name) || {}).status));
  const problems = launch.services.filter((s) => s.problem).map((s) => s.problem);
  const needs = { services: launch.needs.services.filter((n) => todo.some((s) => s.name === n)), prep: todo.length ? launch.needs.prep : [] };
  if (needs.services.length || needs.prep.length) return { needs, plan: strip(launch) };
  if (todo.length && launch.prep.some((p) => p.due)) {
    log(sc, '准备', uiText('执行准备步骤…', "Running preparation steps…"), 'sys');
    const ran = await rf(['env', 'prep-run', launch.env.id, ...scopeArgs(scope), '--due', '--json'], undefined, undefined, 60 * 60000);
    const res = parse(ran.stdout, null);
    for (const r of (res && res.ran) || []) log(sc, '准备', uiText("{0}：{1}（{2} 毫秒）", "{0}: {1} ({2} ms)", r.note, r.ok ? uiText('执行完了', "completed") : uiText('失败了', "failed"), r.ms) + (r.ok ? '' : '\n' + (r.tail || '')), 'sys');
    if (!res || res.error) {
      const error = (res && res.error) || lastLine(ran.stderr);
      log(sc, '准备', error, 'sys');
      return { error, prep: res, plan: strip(launch) };
    }
  }
  const started = [];
  for (const s of todo) started.push(await launchOne(sc, s, launch, how));
  return { ok: true, started, problems, plan: strip(launch), view: viewOf(sc) };
}

async function stop({ scope, name } = {}) {
  if (!SCOPE.test(String(scope || ''))) return { error: uiText('不认识的范围。', "Unrecognized scope.") };
  const sc = scopeOf(scope);
  const names = name ? [String(name)] : [...sc.services.keys()];
  await Promise.all(names.map((n) => stopOne(sc, n, { action: '停止', reason: '你点的' })));
  return viewOf(sc);
}
async function restart({ scope, name, copy } = {}) {
  if (!SCOPE.test(String(scope || ''))) return { error: uiText('不认识的范围。', "Unrecognized scope.") };
  const sc = scopeOf(scope);
  await Promise.all((name ? [String(name)] : [...sc.services.keys()]).map((n) => stopOne(sc, n)));
  return begin({ scope, names: name ? [name] : null, copy }, { action: '重启', reason: '你点的' });
}
// 一个范围用完了：放掉占内存的部分。它带着最多 2000 行日志和启动时拿到的明文保密值，不放的话会一直留到应用退出，
// 开过的需求越多攒得越多。需求完成、放弃时只放这些（服务的列表很小，界面上还要显示「没启动」）；切换项目、退出应用时整个去掉（drop）。
// 还没送到界面的几行先送出去；日志和动作记录在文件里，照样看得到
function forget(sc, drop = false) {
  if (!sc || [...sc.services.values()].some((s) => s.child)) return false;
  clearTimeout(sc.flush);
  sc.flush = null;
  const lines = sc.pending.splice(0);
  if (lines.length) send('svc:log', { scope: sc.scope, project: sc.project, lines });
  sc.secrets = [];
  sc.env = null;
  sc.logs = [];
  if (drop) scopes.delete(keyOf(sc.project, sc.scope));
  return true;
}
// 需求确认完成、放弃、工作目录收掉之前：这个范围的服务全部停掉
async function stopScope(scope, project = state.project, reason = '确认完成前停掉') {
  const sc = scopes.get(keyOf(project, scope));
  if (!sc) return;
  await Promise.all([...sc.services.keys()].map((n) => stopOne(sc, n, { action: '停止', reason })));
  forget(sc);
}
// 切换项目、应用退出：全部停掉（同步，退出时等不了 Promise）
function stopAll(reason = '退出应用') {
  const live = [];
  for (const sc of scopes.values()) for (const svc of sc.services.values()) {
    if (svc.child && svc.pid) { clearTimeout(svc.timer); svc.status = 'stopping'; killGroup(svc.pid, 'SIGTERM'); live.push(svc); note(sc, svc, '应用退出时停掉', reason); }
  }
  const until = Date.now() + 2000;
  while (live.some((s) => groupAlive(s.pid)) && Date.now() < until) sleep(50);
  for (const s of live) { killGroup(s.pid, 'SIGKILL'); unrecord(s.pid); s.child = null; s.status = 'idle'; s.stopped = true; }
  for (const sc of [...scopes.values()]) forget(sc, true);
  return live.length;
}
function running(scope, project = state.project) {
  const sc = scopes.get(keyOf(project, scope));
  return !!sc && [...sc.services.values()].some((s) => ['starting', 'running', 'stopping'].includes(s.status));
}
function open({ port } = {}) {
  const p = Number(port);
  if (!(p >= 1 && p <= 65535)) return { error: uiText('端口不对。', "Invalid port.") };
  require('electron').shell.openExternal('http://localhost:' + p);
  return { ok: true };
}
// 启动失败时交给 AI 的情况：命令、目录、退出码或原因、日志尾部（已打码）
function report(scope, name) {
  const sc = scopes.get(keyOf(state.project, scope));
  const svc = sc && sc.services.get(name);
  if (!svc) return null;
  return { code: svc.code, reason: svc.reason || '', command: svc.command, cwd: svc.cwd, accept: svc.accept, tail: tail(scope, 80, name) };
}

// ---------- 运行的是不是最新的代码 ----------
const isRunning = (svc) => svc.status === 'running';
// 一个服务：现在的代码和启动时的比，改过就标上「启动后代码改过」（改回去了就取消）
async function checkOne(sc, svc) {
  if (!isRunning(svc) || !svc.codeStart) return false;
  const r = await codestate.changed(svc.codeStart, svc.place || sc.place).catch(() => ({ changed: false }));
  if (sc.services.get(svc.name) !== svc || !isRunning(svc)) return false; // 等的时候它已经重启了或停了
  if (!!svc.stale !== r.changed) { svc.stale = r.changed; push(sc); }
  return r.changed;
}
// 检查这个范围里运行中的服务；trigger 是 'turn'（开发会话一轮停下）或 'open'（打开人工验收标签）时，代码改过的服务照开关重启；不给 trigger 只标记
async function refresh({ scope, trigger, project = state.project } = {}) {
  if (!SCOPE.test(String(scope || ''))) return { error: uiText('不认识的范围。', "Unrecognized scope.") };
  const sc = scopes.get(keyOf(project, scope));
  if (!sc) return { results: [] };
  const results = [];
  for (const svc of [...sc.services.values()]) {
    if (!isRunning(svc)) continue; // 没在运行的服务不会因为代码变了被启动
    const stale = await checkOne(sc, svc);
    if (stale && WHY[trigger]) results.push(await autoRestart(sc, svc, trigger));
  }
  return { results, view: viewOf(sc) };
}
// 自动重启一个服务：开关关着、启动命令或准备步骤要重新同意、已经在重启，都不重启。同一个服务不并发
async function autoRestart(sc, svc, trigger) {
  const name = svc.name, why = WHY[trigger];
  if (sc.restarting.has(name)) return { name, skipped: 'busy' };
  sc.restarting.add(name);
  try {
    if (sc.services.get(name) !== svc || !isRunning(svc) || state.project !== sc.project) return { name, skipped: 'changed' }; // 等的时候别处已经重启过了
    const out = await rf(['env', 'plan', ...scopeArgs(sc.scope), '--service', name, '--json'], undefined, undefined, 60000);
    const made = out.ok ? parse(out.stdout, null) : null;
    const item = made && (made.services || []).find((x) => x.name === name);
    if (!item) return { name, skipped: 'noplan' };
    if (item.auto_restart === false) return { name, skipped: 'off' }; // 开关关着：只标记
    if (item.problem) return { name, skipped: 'problem' };
    if (((made.needs || {}).services || []).includes(name) || ((made.needs || {}).prep || []).length) {
      if (!svc.consent) { svc.consent = true; log(sc, name, uiText('代码改过了，但启动命令或准备步骤需要你重新同意，没有自动重启。点「重启」时会让你看一眼再同意。', "The code changed, and the start command or preparation steps need approval again. Select Restart to review them."), 'sys'); push(sc); }
      return { name, skipped: 'consent' };
    }
    if (sc.services.get(name) !== svc || !isRunning(svc)) return { name, skipped: 'changed' };
    log(sc, name, uiText("自动重启：{0}", "Restarting automatically: {0}", why), 'sys');
    await stopOne(sc, name); // 不记停止：重启的那一条由启动的那一步记
    const res = await begin({ scope: sc.scope, names: [name] }, { action: '自动重启', reason: why });
    if (res && (res.error || res.needs)) log(sc, name, uiText('自动重启没做成：', "Automatic restart failed: ") + (res.error || uiText('启动前要你同意。', "Approval is required before starting.")), 'sys');
    return { name, restarted: !!(res && res.ok), error: (res && res.error) || null };
  } finally { sc.restarting.delete(name); }
}
// 开发会话一轮停下（attention.js）：它的服务在运行并且代码比启动时新，就重启
function turnEnded(runId) {
  if (!SCOPE.test(String(runId || '')) || runId === 'main') return;
  const sc = scopes.get(keyOf(state.project, runId));
  if (!sc || ![...sc.services.values()].some(isRunning)) return;
  refresh({ scope: runId, trigger: 'turn' }).catch(() => {});
}
// watcher 每次轮询调一下：隔一会儿看一眼运行中的服务，代码改过的只标记，不重启（AI 可能还在改）
let marking = false, markedAt = 0;
function watch() {
  if (marking || Date.now() - markedAt < MARK_MS) return Promise.resolve();
  const live = [...scopes.values()].filter((sc) => sc.project === state.project && [...sc.services.values()].some(isRunning));
  if (!live.length) return Promise.resolve();
  marking = true; markedAt = Date.now();
  return Promise.all(live.map((sc) => refresh({ scope: sc.scope, project: sc.project }).catch(() => null))).finally(() => { marking = false; });
}
// 「这里不对」交回开发会话的话里加的一行：当时运行的是哪个提交、启动后代码有没有改过
async function runLine(scope) {
  if (!SCOPE.test(String(scope || ''))) return '';
  await refresh({ scope }).catch(() => null);
  const sc = scopes.get(keyOf(state.project, scope));
  if (!sc) return '';
  return [...sc.services.values()].filter((svc) => ['running', 'starting'].includes(svc.status)).map((svc) => {
    const c = svc.codeStart;
    return uiText("「{0}」运行的是提交 {1}{2}；启动后代码{3}", "\"{0}\" is running commit {1}{2}. {3}", svc.name, (c && c.short) || uiText('（不知道）', "(unknown)"), c && c.dirty ? uiText('（启动时还有未提交的改动）', " (uncommitted changes at startup)") : '', svc.stale ? uiText('改过了，运行的不是最新的，这次可能测到的是旧代码', "The code changed since startup, so this run may have tested an older version.") : uiText('没有改过', "The code has stayed the same since startup."));
  }).join('；');
}

module.exports = { forget, scopes, refresh, turnEnded, watch, runLine, checkOne, groupAlive, killGroup, view, plan, start, stop, restart, stopScope, stopAll, running, open, clearLogs, tail, report, sweep, launchOne, scopeOf, probe, scrub, startTime, alive, RUN_DIR, STATUS_WORDS };
