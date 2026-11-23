// 会话的身份（见 backend/relayforge/core/identity.py）：应用开每个会话时发一个签过名的会话令牌，放进会话的环境变量 RELAYFORGE_SESSION_SIG。
// 令牌 = base64url(内容) "." base64url(HMAC-SHA256(密钥, 前一段))；内容是会话编号、种类、属于哪个需求 / 修复 / 问题、签发时间和一个随机串。
// 密钥只在这个进程的内存里（每次启动重新生成），不写进任何文件，所以会话里的 shell 读不到它。rf.py 自己验不了签名，
// 通过本机的 unix socket（一个文件，不是网络端口）问应用：应用验签名，并说出这个令牌发给了哪个会话进程（开出会话后用 bind 记下的 pid），
// rf.py 再确认那个进程是自己的上层进程。socket 的位置写在应用的登记文件里（app-token.js），rf.py 从自己的上层进程里找登记过的应用。
const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');

const SIG_ENV = 'RELAYFORGE_SESSION_SIG'; // 名字里不带 TOKEN / KEY / SECRET：Codex 默认把带这几个词的环境变量从命令的环境里去掉
const KINDS = ['develop', 'test', 'fix', 'probe', 'check', 'intake', 'map', 'chat'];

const b64 = (buf) => Buffer.from(buf).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const unb64 = (text) => Buffer.from(String(text).replace(/-/g, '+').replace(/_/g, '/'), 'base64');

// 一个签发者：密钥、发出去的令牌记到了哪个进程、socket 服务。应用里只有一个（下面的 shared），测试里可以各开各的
function issuer() {
  const key = crypto.randomBytes(32);
  const bound = new Map(); // nonce -> 会话进程的 pid
  const places = new Map(); // nonce -> 会话的工作目录（代写时在这里执行）
  const extra = new Map(); // nonce -> 应用代这个会话执行的 rf.py 的 pid（proxy.js）
  const active = new Map();
  let delegate = null;
  let runner = null; // 代写的执行者（proxy.js），应用启动时装上
  let server = null;
  let where = null;

  const sign = (body) => b64(crypto.createHmac('sha256', key).update(body).digest());
  function issue({ session, kind, owner }) {
    const claims = { v: 1, sid: String(session || ''), kind: KINDS.includes(kind) ? kind : 'chat', owner: owner ? String(owner) : null,
      iat: Math.floor(Date.now() / 1000), nonce: crypto.randomBytes(12).toString('hex') };
    const body = b64(JSON.stringify(claims));
    return { token: body + '.' + sign(body), claims };
  }
  // 会话进程开起来以后：这个令牌只在它和它下面的进程里算数
  function bind(token, pid, place) { const c = check(token); if (c && pid) { bound.set(c.nonce, Number(pid)); if (place) places.set(c.nonce, String(place)); } }
  function unbind(token) {
    const c = check(token);
    if (!c) return;
    for (const controller of active.get(c.nonce) || []) controller.abort();
    active.delete(c.nonce);
    bound.delete(c.nonce); places.delete(c.nonce); extra.delete(c.nonce);
  }
  function setRunner(fn) { runner = fn; }
  // 签名对得上就给出内容，否则 null
  function check(token) {
    const parts = String(token || '').split('.');
    if (parts.length !== 2) return null;
    const want = Buffer.from(sign(parts[0])), got = Buffer.from(parts[1]);
    if (want.length !== got.length || !crypto.timingSafeEqual(want, got)) return null;
    try { const c = JSON.parse(unb64(parts[0]).toString('utf8')); return c && typeof c === 'object' ? c : null; } catch (_) { return null; }
  }
  function answer(line, socketSignal) {
    let req = null;
    try { req = JSON.parse(line); } catch (_) { /* 看不懂 */ }
    if (!req || !['verify', 'run', 'subagent'].includes(req.op)) return { ok: false, error: 'bad request' };
    const c = check(req.token);
    if (!c) return { ok: false, error: '会话令牌不对' };
    if (req.op === 'verify') return { ok: true, claims: c, pid: bound.get(c.nonce) || null, pids: [...(extra.get(c.nonce) || [])] };
    // 代写（proxy.js）：只给还开着的会话，在它自己的工作目录里，以它的身份执行
    if (!bound.has(c.nonce)) return { ok: false, error: '这个会话已经关了，请在应用里重新打开它' };
    if (req.op === 'subagent') {
      if (c.kind !== 'develop' || !delegate || !['status', 'run'].includes(req.action)) {
        return { ok: false, error: '只能在需求开发会话中委派 subagent。' };
      }
      const controllers = active.get(c.nonce) || new Set();
      if (controllers.size >= 8) return { ok: false, error: '正在处理多个 subagent 请求，请稍后重试。' };
      active.set(c.nonce, controllers);
      const controller = new AbortController();
      controllers.add(controller);
      const abort = () => controller.abort();
      socketSignal?.addEventListener('abort', abort, { once: true });
      if (socketSignal?.aborted) abort();
      return Promise.resolve().then(() => delegate({
        claims: c, place: places.get(c.nonce), action: req.action, request: req, signal: controller.signal,
      })).then((result) => ({ ok: true, result })).finally(() => {
        socketSignal?.removeEventListener('abort', abort);
        controllers.delete(controller);
        if (!controllers.size) active.delete(c.nonce);
      });
    }
    if (!runner) return { ok: false, error: '应用还没准备好代写' };
    const pids = extra.get(c.nonce) || new Set();
    extra.set(c.nonce, pids);
    return runner({ token: req.token, claims: c, argv: req.argv, stdin: req.stdin, place: places.get(c.nonce) || null,
      started: (pid) => pids.add(Number(pid)), ended: (pid) => pids.delete(Number(pid)) });
  }
  // 在 file 上开 unix socket（目录 700、socket 600）；返回实际的路径。路径太长（macOS 限 104 字节）时改放在系统临时目录下
  function listen(file) {
    if (server) return Promise.resolve(where);
    let target = file;
    if (Buffer.byteLength(target) > 100) target = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-')), 'app.sock');
    try { fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 }); } catch (_) { /* 已经有了 */ }
    try { fs.unlinkSync(target); } catch (_) { /* 上次没留下 */ }
    return new Promise((resolve, reject) => {
      const s = net.createServer((conn) => {
        let buf = '', taken = false;
        const disconnected = new AbortController();
        conn.on('close', () => disconnected.abort());
        conn.setEncoding('utf8');
        conn.setTimeout(5000, () => conn.destroy());
        conn.on('error', () => {});
        conn.on('data', (chunk) => {
          buf += chunk;
          if (buf.length > 65536) return conn.destroy();
          const n = buf.indexOf('\n');
          if (n < 0 || taken) return;
          taken = true;
          conn.setTimeout(0);
          Promise.resolve().then(() => answer(buf.slice(0, n), disconnected.signal)).catch((error) => ({ ok: false, error: String(error && error.message || error) }))
            .then((out) => { try { conn.end(JSON.stringify(out) + '\n'); } catch (_) { /* 对方已经走了 */ } });
        });
      });
      s.on('error', reject);
      s.listen(target, () => {
        try { fs.chmodSync(target, 0o600); } catch (_) { /* 有的系统不让改 socket 的权限；目录本身是 700 */ }
        s.unref(); // 不因为它让进程（测试）退不出去；应用自己的窗口让它一直开着
        server = s; where = target; resolve(target);
      });
    });
  }
  function close() {
    for (const controllers of active.values()) for (const controller of controllers) controller.abort();
    active.clear();
    if (server) {
      server.close();
      try { fs.unlinkSync(where); } catch (_) { /* 已经没了 */ }
      server = null;
    }
  }
  return { issue, bind, unbind, setRunner, setDelegate: (fn) => { delegate = fn; }, check, answer, listen, close, socketPath: () => where };
}

const shared = issuer();

// 每个会话开之前：发令牌（放进会话的环境）；开起来以后 bind(令牌, 会话进程的 pid)
function forSession({ session, kind, owner }) { return shared.issue({ session, kind, owner }).token; }

module.exports = { SIG_ENV, KINDS, issuer, shared, forSession, bind: shared.bind, unbind: shared.unbind, setRunner: shared.setRunner, setDelegate: shared.setDelegate, listen: shared.listen, close: shared.close, socketPath: shared.socketPath };
