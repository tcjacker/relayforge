// Codex 的钩子要信任过才会运行（Codex 的规定）：没信任时 Codex 会话照样能开，但状态提醒、停下来时的检查、执行前的拦截、
// 无界面会话的批准都不生效，而且 Codex 不报错（codex exec 直接跳过）。这些钩子是 RelayForge 自己的，用户没法判断该不该信任，
// 所以不问，由应用在启动、窗口回到前台时补上：
// - 装了 Codex 才查；我们传给 Codex 的钩子（codex-hooks.js）逐个算出 Codex 记信任用的键和指纹，和 ~/.codex/config.toml 里记的比。
// - 缺了或对不上：经 Codex 自己的 app-server（config/batchWrite，和 Codex 终端里「Hooks need review」写的是同一处、同一种写法）
//   只写这几个钩子的 trusted_hash。不用 --dangerously-bypass-hook-trust，别的钩子一概不碰。
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const { uiText = (zh) => zh, cleanEnv } = require('./backend');
const settings = require('./settings');
const { trustEntries } = require('./codex-hooks');
const { version } = require('../../package.json');

const RPC_MS = 15000;
let writing = null;
let failed = ''; // 写失败过的状态：窗口每次回到前台不再重试，下次启动或状态变了再试

function configFile() {
  return path.join(process.env.CODEX_HOME || path.join(os.homedir(), '.codex'), 'config.toml');
}

// config.toml 里某个键记的 trusted_hash：[hooks.state."键"] 下面一行，或者 "键" = { trusted_hash = "…" } 这种写在一行里的
function storedHash(text, key) {
  const quoted = '"' + key.replace(/\\/g, '\\\\') + '"';
  let at = text.indexOf(quoted);
  while (at !== -1) {
    const rest = text.slice(at + quoted.length);
    const end = rest.search(/\n\s*\[/);
    const found = (end === -1 ? rest : rest.slice(0, end)).match(/trusted_hash\s*=\s*"([^"]+)"/);
    if (found) return found[1];
    at = text.indexOf(quoted, at + quoted.length);
  }
  return null;
}

function installed() {
  try { return !!settings.launch('codex').target; } catch (_) { return false; }
}

// { installed, trusted, hooks: [{ name, command, timeout, state: trusted | untrusted | modified }] }
function status() {
  if (!installed()) return { installed: false, trusted: true, hooks: [] };
  let text = '';
  try { text = fs.readFileSync(configFile(), 'utf8'); } catch (_) { /* 还没有配置文件：都没信任 */ }
  const hooks = trustEntries().map((h) => {
    const stored = storedHash(text, h.key);
    return { name: h.name, command: h.command, timeout: h.timeout, state: stored === h.hash ? 'trusted' : stored ? 'modified' : 'untrusted' };
  });
  return { installed: true, trusted: hooks.every((h) => h.state === 'trusted'), hooks };
}

function trusted() { return status().trusted; }

// 用 Codex 的 app-server 写信任：initialize → initialized → config/batchWrite（只写我们这几个钩子的 trusted_hash）
function writeTrust(entries) {
  const { file, args = [] } = settings.launch('codex');
  const child = spawn(file, [...args, 'app-server'], { cwd: os.homedir(), env: cleanEnv(), stdio: ['pipe', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let buffer = '', stderr = '', done = false;
    const timer = setTimeout(() => finish(new Error(uiText('Codex 没有在 15 秒内写完信任，请稍后再试。', "Codex didn't save trust within 15 seconds. Try again shortly."))), RPC_MS);
    function finish(error) {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { child.stdin.destroy(); } catch (_) { /* 已经关了 */ }
      if (child.pid) require('./proc-tree').killTree(child.pid); else child.kill('SIGKILL');
      if (error) reject(error); else resolve();
    }
    const write = (m) => child.stdin.write(JSON.stringify(m) + '\n');
    child.stdin.on('error', () => {});
    child.stderr.on('data', (c) => { stderr = (stderr + c).slice(-1000); });
    child.once('error', (e) => finish(new Error(e.code === 'ENOENT' ? uiText('找不到 Codex，请先安装。', "Install Codex first.") : e.message)));
    child.once('close', () => finish(new Error(stderr.trim().split('\n').at(-1) || uiText('Codex 没有写入信任。', "Codex didn't save trust."))));
    child.stdout.on('data', (chunk) => {
      const lines = (buffer + chunk).split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        let m;
        try { m = JSON.parse(line); } catch (_) { continue; }
        if (m.id !== 1 && m.id !== 2) continue;
        if (m.error) return finish(new Error(uiText('Codex 拒绝了：', "Codex declined: ") + (m.error.message || JSON.stringify(m.error))));
        if (m.id === 1) {
          write({ method: 'initialized' });
          write({ id: 2, method: 'config/batchWrite', params: {
            edits: entries.map((h) => ({ keyPath: `hooks.state."${h.key}".trusted_hash`, value: h.hash, mergeStrategy: 'replace' })),
            reloadUserConfig: true } });
        } else finish();
      }
    });
    write({ id: 1, method: 'initialize', params: { clientInfo: { name: 'relayforge', title: 'RelayForge', version } } });
  });
}

async function writeMissing(before) {
  const missing = trustEntries().filter((h) => before.hooks.find((x) => x.name === h.name)?.state !== 'trusted');
  await writeTrust(missing);
  const after = status();
  if (!after.trusted) throw new Error(uiText('Codex 写完了，但重新检查时仍然不算信任。', 'Trust was still unconfirmed after Codex finished.'));
  return after;
}

// 应用启动、窗口回到前台时调用：缺信任就补上。失败只记日志，会话照常能开（无界面会话会在输出里说明，见 headless.js）
function ensure() {
  const now = status();
  if (now.trusted) { failed = ''; return Promise.resolve(now); }
  if (writing) return writing;
  const key = JSON.stringify(now);
  if (key === failed) return Promise.resolve(now);
  writing = writeMissing(now)
    .then((after) => { failed = ''; return after; })
    .catch((error) => { failed = key; console.error('写入 Codex 钩子的信任失败', error.message); return { ...now, error: error.message }; })
    .finally(() => { writing = null; });
  return writing;
}

module.exports = { status, trusted, ensure, storedHash, configFile };
