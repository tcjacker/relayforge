// 开 Claude Code 会话之前，确认系统的沙箱会生效（沙箱的设置见 claude-settings.js 的 sandbox）。
// 沙箱是 RelayForge 挡住会话冒充你、读保密值的那道边界（backend/relayforge/core/trust.py）；它没生效时界面上看不出来，所以开之前先查：
//   - Claude Code 的版本：太旧的不认识这些设置，可能直接忽略，会话就在沙箱外运行。MIN_CLAUDE 是实际验证过的版本；
//   - 能压过我们的设置、或者削弱沙箱的设置：管理员下发的托管设置优先级最高，可以把沙箱关掉、放开跳出沙箱；
//     你自己的设置（~/.claude）和项目里的设置可以关掉文件隔离、让某些命令在沙箱外跑、放开 Apple Events（能借别的程序跳出去）。
// 查不过就不开会话，说清楚为什么、该怎么办。开了以后还有第二道：会话里的 rf.py 发现自己不在沙箱里会报给应用（attention.js 的 unsandboxed）。
const { uiText } = require('./locale');
const { execFile } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const MIN_CLAUDE = '2.1.330';
const MANAGED = process.platform === 'darwin' ? '/Library/Application Support/ClaudeCode' : process.platform === 'linux' ? '/etc/claude-code' : null;
const CACHE_MS = 60 * 1000;
let cached = null; // { key, at, result }

function newer(a, b) { // a >= b，只比数字部分
  const pa = String(a).split(/[.-]/).map(Number), pb = String(b).split(/[.-]/).map(Number);
  for (let i = 0; i < 3; i++) { if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0); }
  return true;
}

function readJson(file) { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (_) { return null; } }

// 会影响沙箱的设置文件：[文件, 是不是管理员的]
function settingFiles(root, home = os.homedir(), managed = MANAGED) {
  const out = [];
  if (managed) {
    out.push([path.join(managed, 'managed-settings.json'), true]);
    try { for (const f of fs.readdirSync(path.join(managed, 'managed-settings.d'))) if (f.endsWith('.json')) out.push([path.join(managed, 'managed-settings.d', f), true]); } catch (_) { /* 没有 */ }
  }
  out.push([path.join(home, '.claude', 'settings.json'), false], [path.join(home, '.claude', 'settings.local.json'), false]);
  if (root) out.push([path.join(root, '.claude', 'settings.json'), false], [path.join(root, '.claude', 'settings.local.json'), false]);
  return out;
}

// 一份设置里削弱沙箱的地方（说给你听的话）；managed：管理员的设置能压过我们传进去的，连「关掉沙箱」「放开跳出沙箱」也算
function weakening(data, managed) {
  const sb = data && typeof data === 'object' ? data.sandbox : null;
  if (!sb || typeof sb !== 'object') return [];
  const out = [];
  if (managed && sb.enabled === false) out.push(uiText('关掉了沙箱（sandbox.enabled: false）', "Disabled the sandbox (sandbox.enabled: false)"));
  if (managed && sb.allowUnsandboxedCommands === true) out.push(uiText('允许命令跳出沙箱（sandbox.allowUnsandboxedCommands: true）', "Allowed commands outside the sandbox (sandbox.allowUnsandboxedCommands: true)"));
  if (sb.filesystem && sb.filesystem.disabled === true) out.push(uiText('关掉了文件隔离（sandbox.filesystem.disabled）', "Disabled filesystem isolation (sandbox.filesystem.disabled)"));
  if (Array.isArray(sb.excludedCommands) && sb.excludedCommands.length) out.push(uiText('让这些命令在沙箱外运行（sandbox.excludedCommands）：', "Allowed these commands outside the sandbox (sandbox.excludedCommands): ") + sb.excludedCommands.slice(0, 5).join('、'));
  if (sb.allowAppleEvents === true) out.push(uiText('放开了 Apple Events（sandbox.allowAppleEvents，能借别的程序跳出沙箱）', "Enabled Apple Events (sandbox.allowAppleEvents), allowing other apps to bypass the sandbox"));
  if (sb.enableWeakerNestedSandbox === true) out.push(uiText('用了较弱的沙箱（sandbox.enableWeakerNestedSandbox）', "Used a weaker sandbox (sandbox.enableWeakerNestedSandbox)"));
  return out;
}

function versionOf(file) {
  return new Promise((resolve) => {
    execFile(file, ['--version'], { timeout: 15000, killSignal: 'SIGKILL', env: { ...process.env, CLAUDECODE: '', CLAUDE_CODE_SESSION_ID: '' } },
      (error, stdout) => resolve(error ? null : ((String(stdout).match(/\d+\.\d+\.\d+/) || [])[0] || null)));
  });
}

// { ok, reason, version }：file 是要启动的 Claude Code，root 是会话的工作目录；where 只给测试用（换掉你的主目录和管理员设置的目录）
async function check(file, root, where = {}) {
  const files = settingFiles(root, where.home, where.managed);
  const stamp = (f) => { try { const s = fs.statSync(f); return s.mtimeMs + ':' + s.size; } catch (_) { return '-'; } };
  const key = [file, stamp(file), ...files.map(([f]) => f + '=' + stamp(f))].join('|');
  if (cached && cached.key === key && Date.now() - cached.at < CACHE_MS) return cached.result;
  const version = await versionOf(file);
  let result;
  if (!version) {
    result = { ok: false, version, reason: uiText("读不出 Claude Code 的版本（{0}），没法确认沙箱会生效，所以没有开这个会话。请在设置里检查 CLI 的安装。", "The session could not open because RelayForge could not read the Claude Code version ({0}). Check the CLI installation in Settings.", file) };
  } else if (!newer(version, MIN_CLAUDE)) {
    result = { ok: false, version, reason: uiText("Claude Code {0} 太旧了（至少要 {1}）：旧版本可能不认识沙箱的设置，会话会在沙箱外运行，RelayForge 挡不住它冒充你的操作、读保密值。请在设置里更新 CLI 后再打开。", "Claude Code {0} is below the required version {1}. It may ignore the sandbox settings and run with access to your actions and secrets. Update the CLI in Settings, then reopen the session.", version, MIN_CLAUDE) };
  } else {
    const found = [];
    for (const [f, managed] of files) for (const why of weakening(readJson(f), managed)) found.push(`${f}：${why}`);
    result = found.length
      ? { ok: false, version, reason: uiText('这些设置会让会话的沙箱不生效或者变弱，RelayForge 挡不住会话冒充你的操作、读保密值，所以没有开这个会话：\n', "The session could not open because these settings disable or weaken the sandbox and can let it act on your behalf or read secrets:\n") + found.join('\n') + uiText('\n改掉这些设置后再打开（公司下发的设置请找管理员）。', "\nUpdate these settings and reopen the session. Contact your administrator about managed settings.") }
      : { ok: true, version, reason: '' };
  }
  cached = { key, at: Date.now(), result };
  return result;
}

module.exports = { check, weakening, newer, settingFiles, MIN_CLAUDE };
