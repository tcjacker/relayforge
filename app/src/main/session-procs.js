// 会话留下的进程：关会话、会话自己退出、应用崩溃以后，把这个会话起的进程都收拾掉。
// 关会话时应用只给 pty_bridge.py 发 SIGTERM，它再给 CLI 发 SIGHUP；但 Claude Code 的 Bash 工具在自己的会话（setsid）里，收不到挂断，
// 会话里 nohup、& 起的服务也早就挂到 launchd 下面了，按父子关系找不回来。好在它们都带着应用放进会话环境的 RELAYFORGE_SESSION_ID，
// 所以按这个变量找：环境里有这个会话编号的进程，就是这个会话起的。我们自己的 rf.py（会话结束时的同步、整理）不杀，它们要把记录写完。
// 应用崩溃时来不及收拾：开会话时记一笔（RF_HOME/run/sessions/），下次启动时主人已经不在的那些照样按会话编号收拾（sweep）。
//
// 但沙箱里的进程（Claude Code、Codex 会话里跑的命令）的环境从沙箱外读不到，按会话编号找不到它们。所以还有两条：
//   - 会话自己的进程树（pty_bridge.py 往下）：还没脱离父进程的，连同沙箱里的一起收拾；
//   - 从外面看得到的三样：父进程是 launchd（成了孤儿）、在沙箱里、当前目录。会话的工作目录是它独占的需求 / 修复工作目录
//     （.worktrees/…）时，在沙箱里、已经成了孤儿、当前目录在这个工作目录里的进程，也算它留下的。
//     项目主目录不用这一条：日常聊天都在那里，那里也可能有你自己开的进程（比如你在终端里开了沙箱的 Claude Code），不能误杀；
//     还有别的会话开在同一个工作目录里时也不用（shared），分不清是谁的。
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { PYTHON, RF_HOME } = require('./config');
const { killTree, table } = require('./proc-tree');

const TAG = 'RELAYFORGE_SESSION_ID';
const RUN_DIR = path.join(RF_HOME, 'run', 'sessions');
const OURS = /(?:^|[\s/])rf\.py\s/; // 我们自己的 rf.py：钩子起的同步、整理，让它们做完

// 每个进程的 [pid, 命令行, 环境里的会话编号]
function listTagged() {
  const out = [];
  try {
    if (fs.existsSync('/proc/self/environ')) {
      for (const name of fs.readdirSync('/proc')) {
        if (!/^\d+$/.test(name)) continue;
        try {
          const env = fs.readFileSync(`/proc/${name}/environ`, 'utf8').split('\0');
          const hit = env.find((x) => x.startsWith(TAG + '='));
          if (hit) out.push([Number(name), fs.readFileSync(`/proc/${name}/cmdline`, 'utf8').replace(/\0/g, ' '), hit.slice(TAG.length + 1)]);
        } catch (_) { /* 别人的进程、刚退出 */ }
      }
      return out;
    }
    // macOS：ps -E 在命令后面接上环境变量（只看得到自己用户的进程）
    const text = execFileSync('ps', ['-axwwE', '-o', 'pid=,command='], { encoding: 'utf8', timeout: 10000, maxBuffer: 64 << 20 });
    const find = new RegExp(`(?:^|\\s)${TAG}=([^\\s]+)`);
    for (const line of text.split('\n')) {
      const m = line.match(/^\s*(\d+)\s+(.*)$/);
      const hit = m && m[2].match(find);
      if (hit) out.push([Number(m[1]), m[2].slice(0, hit.index), hit[1]]);
    }
  } catch (_) { /* 读不了进程表：这次收拾不了 */ }
  return out;
}

// 这个会话编号起的、还活着的进程（不含我们自己的 rf.py 和应用本身）
function tagged(sid, list = listTagged()) {
  return list.filter(([pid, cmd, id]) => id === sid && pid !== process.pid && !OURS.test(cmd)).map(([pid]) => pid);
}

const TREES = `${path.sep}.worktrees${path.sep}`;
// 哪些进程在沙箱里（macOS 的 sandbox_check，Node 里调不了，交给 python3 的 ctypes）
const SANDBOXED = "import ctypes, sys\nf = ctypes.CDLL('/usr/lib/libSystem.dylib').sandbox_check\nf.restype = ctypes.c_int\n" +
  "f.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]\nprint(' '.join(a for a in sys.argv[1:] if f(int(a), None, 0)))";

// 当前目录在 root 里、已经成了孤儿、在沙箱里的进程；root 不是 .worktrees/ 下的工作目录时一个都不算
function sandboxedOrphansIn(root, tab = table()) {
  if (process.platform !== 'darwin' || !root) return [];
  let real;
  try { real = fs.realpathSync(root); } catch (_) { return []; }
  if (!(real + path.sep).includes(TREES)) return [];
  const orphans = [...tab].filter(([pid, ppid]) => ppid === 1 && pid !== process.pid).map(([pid]) => pid);
  if (!orphans.length) return [];
  let text = '';
  try { text = execFileSync('lsof', ['-a', '-d', 'cwd', '-Fpn', '-p', orphans.join(',')], { encoding: 'utf8', timeout: 15000, maxBuffer: 16 << 20, stdio: ['ignore', 'pipe', 'ignore'] }); }
  catch (error) { text = String(error.stdout || ''); } // 有的进程刚退出、或者不是你的：lsof 退出码 1，其余照常给出
  const inside = [];
  let pid = 0;
  for (const line of text.split('\n')) {
    if (line[0] === 'p') pid = Number(line.slice(1));
    else if (line[0] === 'n' && pid && (line.slice(1) === real || line.slice(1).startsWith(real + path.sep))) inside.push(pid);
  }
  if (!inside.length) return [];
  try { return execFileSync(PYTHON, ['-c', SANDBOXED, ...inside.map(String)], { encoding: 'utf8', timeout: 15000 }).split(/\s+/).filter(Boolean).map(Number); }
  catch (_) { return []; }
}

// 收拾一个会话留下的进程，返回收拾了几个。where：{ root 会话的工作目录, pid 还活着的 pty_bridge.py, shared 别的会话也开在这个目录 }
function reap(sid, { root = null, pid = null, shared = false } = {}) {
  if (!sid) return 0;
  let n = 0;
  if (pid && alive(pid)) n += killTree(pid).length ? 1 : 0;
  for (const p of tagged(String(sid))) n += killTree(p).length ? 1 : 0;
  if (!shared) for (const p of sandboxedOrphansIn(root)) n += killTree(p).length ? 1 : 0;
  return n;
}

const startTime = (pid) => { try { return execFileSync('ps', ['-o', 'lstart=', '-p', String(pid)], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 3000 }).trim(); } catch (_) { return ''; } };
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch (error) { return error.code === 'EPERM'; } };
let selfStart = null;
const fileFor = (sid) => path.join(RUN_DIR, `${process.pid}-${String(sid).replace(/[^\w.-]/g, '_')}.json`);

function record(sid, { root = null, pid = null } = {}) {
  try {
    fs.mkdirSync(RUN_DIR, { recursive: true, mode: 0o700 });
    if (selfStart === null) selfStart = startTime(process.pid);
    fs.writeFileSync(fileFor(sid), JSON.stringify(require('./record-sign').sign({ owner: process.pid, owner_start: selfStart, session: String(sid), root, pid,
      start: pid ? startTime(pid) : null, at: new Date().toISOString() })), { mode: 0o600 }); // 签名：sweep 只认应用自己写的（record-sign.js）
  } catch (_) { /* 记不上：应用崩溃时这个会话就收拾不到 */ }
}
function unrecord(sid) { try { fs.unlinkSync(fileFor(sid)); } catch (_) { /* 已经没了 */ } }

// 启动时：上次应用没退干净（崩溃、被强制退出）时那些会话留下的进程
function sweep() {
  let names = [];
  try { names = fs.readdirSync(RUN_DIR).filter((n) => n.endsWith('.json')); } catch (_) { return 0; }
  let list = null, n = 0;
  const records = [];
  for (const name of names) {
    const file = path.join(RUN_DIR, name);
    let r = null;
    try { r = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (_) { /* 坏的 */ }
    // 签名对不上（不是应用自己写的、或者被改过）：不照着杀进程，删掉
    if (r && require('./record-sign').verify(r)) records.push([file, r]); else { try { fs.unlinkSync(file); } catch (_) { /* 别的应用刚删 */ } }
  }
  const live = (r) => r.owner === process.pid || (r.owner && alive(r.owner) && (!r.owner_start || startTime(r.owner) === r.owner_start));
  const liveRoots = new Set(records.filter(([, r]) => live(r)).map(([, r]) => r.root).filter(Boolean));
  for (const [file, r] of records) {
    if (live(r)) continue; // 主人还开着：它自己管
    if (r.pid && alive(r.pid) && r.start && startTime(r.pid) === r.start) n += killTree(r.pid).length ? 1 : 0; // pid 没被别的进程复用
    list = list || listTagged();
    for (const pid of tagged(String(r.session || ''), list)) n += killTree(pid).length ? 1 : 0;
    if (!liveRoots.has(r.root)) for (const pid of sandboxedOrphansIn(r.root)) n += killTree(pid).length ? 1 : 0;
    try { fs.unlinkSync(file); } catch (_) { /* 已经没了 */ }
  }
  return n;
}

module.exports = { reap, record, unrecord, sweep, tagged, listTagged, sandboxedOrphansIn, TAG, RUN_DIR };
