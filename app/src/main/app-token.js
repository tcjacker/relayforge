// 应用的标记：判断「是不是你在应用里点的」（见 backend/relayforge/core/trust.py）。启动时生成一个随机标记，只放进应用自己调用 rf.py 的
// 子进程的环境（backend.js 的 rf()），不放进 process.env，所以应用开的会话（它们的环境来自 cleanEnv()）拿不到；
// $RELAYFORGE_HOME/apps/<主进程 pid>.json 里只留它的 SHA-256（600），rf.py 还要看自己的父进程是不是这个 pid。退出时删掉登记文件。
// 登记文件里还有会话令牌的 socket 的位置（session-token.js）。
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { RF_HOME } = require('./config');

const TOKEN_ENV = 'RELAYFORGE_APP_TOKEN';
const token = crypto.randomBytes(24).toString('hex');
const file = path.join(RF_HOME, 'apps', process.pid + '.json');
let registered = false;

function write(extra) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, JSON.stringify({ pid: process.pid, token_sha256: crypto.createHash('sha256').update(token).digest('hex'), ...extra }), { mode: 0o600 });
  fs.chmodSync(file, 0o600);
}
function register() {
  if (registered) return;
  try {
    write({});
    registered = true;
    // 以前没退干净的应用留下的登记和 socket：进程已经不在了就删掉
    for (const name of fs.readdirSync(path.dirname(file))) {
      const pid = Number(name.replace(/\.(json|sock)$/, ''));
      if (!pid || pid === process.pid) continue;
      try { process.kill(pid, 0); } catch (error) { if (error.code === 'ESRCH') { try { fs.unlinkSync(path.join(path.dirname(file), name)); } catch (_) { /* 别的应用刚删掉 */ } } }
    }
  } catch (_) { /* 写不了：rf.py 就把应用当成 AI 的会话（不带保密值、不能同意），不会更宽 */ }
  // 会话的身份（session-token.js）：开 unix socket 给 rf.py 验会话令牌，socket 的位置也记进登记文件。开不了的话，会话一律按日常聊天算
  if (registered) {
    // 会话经应用代写记录（proxy.js）：Codex 的沙箱写不了项目目录之外的外置记录
    try {
      const { PYTHON, BUNDLED } = require('./config');
      require('./session-token').setRunner(require('./proxy').runner({
        python: PYTHON, script: BUNDLED, codex: require('./codex-sessions'),
      }));
      require('./session-token').setDelegate(require('./subagents').delegate);
    } catch (_) { /* 装不上：会话写不了外置记录时会看到「应用还没准备好代写」 */ }
    require('./session-token').listen(path.join(path.dirname(file), process.pid + '.sock'))
      .then((socket) => { if (registered) { try { write({ socket }); } catch (_) { /* 记不上：会话按日常聊天算 */ } } })
      .catch(() => {});
  }
}
function unregister() { if (registered) { try { fs.unlinkSync(file); } catch (_) { /* 已经没了 */ } registered = false; require('./session-token').close(); } }
// 交给 rf.py 子进程的环境变量
function envFor() { register(); return { [TOKEN_ENV]: token }; }

module.exports = { TOKEN_ENV, envFor, register, unregister, file };
