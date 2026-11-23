// 设置窗口里两个 CLI 的登录状态和登录（R-20261121-1f0c）。
// 只调 CLI 自己的命令（claude auth status / login、codex login status / login），不去读它们存的凭据文件。
const { execFile, spawn } = require('node:child_process');
const os = require('node:os');
const { uiText = (zh) => zh, cleanEnv } = require('./backend');
const settings = require('./settings');

const STATUS_TIMEOUT = 10000;
// 登录命令会一直等浏览器授权；过了这个时限还没授权就停掉，免得后台一直挂着一个登录进程
const LOGIN_TIMEOUT = 5 * 60 * 1000;
const COMMANDS = {
  claude: { status: ['auth', 'status', '--json'], login: ['auth', 'login'] },
  codex: { status: ['login', 'status'], login: ['login'] },
};
const pending = new Map();

function cli(agent) {
  if (!Object.hasOwn(COMMANDS, agent)) throw new Error(uiText('请选择 Claude Code 或 Codex。', "Select Claude Code or Codex."));
  // 只取可执行文件：应用配置里给会话加的参数不适用于 auth / login 子命令
  const { file, target, desktop } = settings.launch(agent);
  return { file, installed: !!target, desktop };
}

const lastLine = (text) => String(text || '').trim().split('\n').map((line) => line.trim()).filter(Boolean).at(-1) || '';

function plan(type) {
  if (!type) return '';
  const name = String(type);
  return name.charAt(0).toUpperCase() + name.slice(1) + uiText(' 订阅', " subscription");
}

// 把状态命令的结果读成「已登录 + 账号」或「没登录」。result：{ code, stdout, stderr, timedOut, error }
function readStatus(agent, result) {
  const { code = null, stdout = '', stderr = '', timedOut = false, error = '' } = result || {};
  if (timedOut) return { state: 'out', note: uiText('查询登录状态超时，可以重新登录。', "The sign-in check timed out. Try signing in again.") };
  if (agent === 'claude') {
    let info = null;
    try { info = JSON.parse(stdout); } catch (_) { /* 不是 JSON：按出错处理 */ }
    if (info && typeof info === 'object') {
      if (!info.loggedIn) return { state: 'out' };
      const who = info.email || (/api.?key/i.test(info.authMethod || '') ? 'API Key' : '') || info.orgName || uiText('已登录', "Signed in");
      return { state: 'in', account: [who, plan(info.subscriptionType)].filter(Boolean).join(' · ') };
    }
  } else {
    const text = (stdout + '\n' + stderr).trim();
    if (/not logged in/i.test(text)) return { state: 'out' };
    const using = text.match(/logged in using (.+)/i);
    if (code === 0 && using) {
      const how = using[1].trim();
      return { state: 'in', account: /chatgpt/i.test(how) ? uiText('ChatGPT 账号', "ChatGPT account") : /api key/i.test(how) ? 'API Key' : how };
    }
  }
  const why = error || lastLine(stderr) || lastLine(stdout) || uiText('没有给出原因', "No reason provided");
  return { state: 'out', note: uiText('没能查到登录状态（', "Couldn't check sign-in status (") + why + uiText('），可以重新登录。', "). Try signing in again.") };
}

function runStatus(file, args) {
  return new Promise((resolve) => {
    execFile(file, args, { cwd: os.homedir(), env: cleanEnv(), timeout: STATUS_TIMEOUT, killSignal: 'SIGKILL' },
      (error, stdout, stderr) => {
        if (!error) return resolve({ code: 0, stdout: String(stdout), stderr: String(stderr) });
        resolve({
          code: typeof error.code === 'number' ? error.code : null, stdout: String(stdout || ''), stderr: String(stderr || ''),
          timedOut: !!error.killed, error: typeof error.code === 'number' ? '' : error.message,
        });
      });
  });
}

// 一行该显示的状态：missing（未安装）、desktop（由桌面应用提供）、waiting（正在登录）、in、out
async function status(agent) {
  const { file, installed, desktop } = cli(agent);
  if (!installed) return { state: 'missing' };
  if (desktop) return { state: 'desktop', desktop };
  if (pending.has(agent)) return { state: 'waiting' };
  return readStatus(agent, await runStatus(file, COMMANDS[agent].status));
}

// 运行 CLI 自己的登录命令，它会打开浏览器；授权完成、失败、超时或取消后才返回
function login(agent) {
  const { file, installed, desktop } = cli(agent);
  if (!installed) throw new Error(uiText('这台电脑上没有安装这个 CLI，装好后再登录。', "Install the CLI before signing in."));
  if (desktop) throw new Error(uiText('这个 CLI 由 ', "This CLI is provided by the ") + desktop + uiText(' 桌面应用提供，请在桌面应用里登录。', " desktop app. Sign in from that app."));
  const running = pending.get(agent);
  // 正在登录就等同一次；刚点了取消、旧进程还没退出时，等它退出再重新开始
  if (running) return running.stopping() ? running.done.then(() => login(agent)) : running.done;
  const child = spawn(file, COMMANDS[agent].login, { cwd: os.homedir(), env: cleanEnv(), stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '';
  let ending = null;
  let settle;
  const done = new Promise((resolve) => { settle = resolve; });
  const entry = { child, done, stop, stopping: () => !!ending };
  pending.set(agent, entry);
  const keep = (chunk) => { output = (output + chunk).slice(-2000); };
  child.stdout?.on('data', keep);
  child.stderr?.on('data', keep);
  const timer = setTimeout(() => stop('timeout'), LOGIN_TIMEOUT);
  function stop(why) {
    if (ending) return;
    ending = why;
    try { child.kill('SIGTERM'); } catch (_) { /* 已经退出 */ }
    // 登录命令起的子进程（等浏览器回调的那个）也一起停；退出应用时（cancelAll）等不到它自己收尾
    if (!child.pid) return;
    if (why === 'cancel') require('./proc-tree').killTree(child.pid); // 就地：退出应用时定时器等不到
    else setTimeout(() => require('./proc-tree').killTree(child.pid), 1000);
  }
  async function finish(code, error) {
    clearTimeout(timer);
    let result;
    if (ending === 'cancel') result = { state: 'cancelled' };
    else if (ending === 'timeout') result = { state: 'failed', reason: uiText('授权超时：', "Authorization timed out after ") + LOGIN_TIMEOUT / 60000 + uiText(' 分钟内没有在浏览器里完成授权。', " minutes. Finish signing in from your browser and try again.") };
    else if (error) result = { state: 'failed', reason: uiText('登录命令没能运行：', "Couldn't run the sign-in command: ") + error.message };
    else if (code !== 0) result = { state: 'failed', reason: uiText('登录没有完成：', "Sign-in did not finish: ") + (lastLine(output) || uiText('登录命令以失败结束', "The sign-in command failed")) + '。' };
    else {
      pending.delete(agent); // 先放开，status 才会真去查
      const after = await status(agent).catch((e) => ({ state: 'out', note: e.message }));
      result = after.state === 'in' ? after : { state: 'failed', reason: uiText('登录命令结束了，但仍然没有登录。', "The sign-in command finished, but the CLI is still signed out.") };
    }
    if (pending.get(agent) === entry) pending.delete(agent);
    settle(result);
  }
  let closed = false;
  child.once('error', (error) => { if (!closed) { closed = true; finish(null, error); } });
  child.once('close', (code) => { if (!closed) { closed = true; finish(code, null); } });
  return done;
}

function cancel(agent) {
  pending.get(agent)?.stop('cancel');
  return { ok: true };
}

function cancelAll() { for (const entry of pending.values()) entry.stop('cancel'); }

module.exports = { status, login, cancel, cancelAll, readStatus, LOGIN_TIMEOUT };
