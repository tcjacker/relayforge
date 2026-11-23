// 调用 Python 后端 rf.py 和其他命令行程序。
const { execFile } = require('child_process');
const fs = require('fs');
const path = require('path');
const { BUNDLED, PYTHON } = require('./config');
const { state } = require('./state');
const { withCliPath } = require('./cli-paths');
const { uiText } = require('./locale');

const scriptFor = (root) => (fs.existsSync(BUNDLED) ? BUNDLED : path.join(root, '.relayforge', 'bin', 'rf.py'));

// input：交给它标准输入的文字（rf.py test save --file - 用）
// 超时：连同它的后代整棵停掉（proc-tree.js）。只杀 rf.py 的话，它正在跑的驱动、构建、准备步骤和测试里起的服务都会留下来
function exec(command, args, cwd, extraEnv = {}, timeout = 0, input) {
  return new Promise((resolve) => {
    let timedOut = false, timer = null;
    const child = execFile(command, args, { cwd, maxBuffer: 16 << 20, env: withCliPath({ ...process.env, CLAUDE_CODE_SESSION_ID: '', CLAUDE_PROJECT_DIR: '', RELAYFORGE_SESSION_SIG: '', ...extraEnv }) },
      (error, stdout, stderr) => { clearTimeout(timer); resolve({ ok: !error, stdout, stderr: String(stderr || (error && error.message) || ''), timedOut: timedOut || !!(error && error.killed) }); });
    if (timeout > 0 && child.exitCode === null && child.signalCode === null) timer = setTimeout(() => { timedOut = true; if (child.pid) require('./proc-tree').killTree(child.pid); }, timeout);
    if (child.stdin) { child.stdin.on('error', () => {}); child.stdin.end(input == null ? undefined : String(input)); }
  });
}
// 对当前项目（或指定目录）运行 rf.py
function modelEnv(agent) {
  const settings = require('./settings');
  const configured = { RELAYFORGE_LANGUAGE: settings.language?.() || 'en' };
  if (process.env.RELAYFORGE_EXTRACTOR) return configured;
  const { file } = settings.launch(agent);
  return { ...configured, RELAYFORGE_EXTRACTOR: JSON.stringify(agent === 'codex' ? [file, 'exec', '--sandbox', 'read-only', '--skip-git-repo-check'] : [file, '-p']) };
}
// 应用自己调用 rf.py 时带上应用的标记（app-token.js）：rf.py 据此认出「是你在应用里点的」（运行测试集带上保密值、同意执行准备步骤）
const rf = (args, root, agent, timeout, input) => exec(PYTHON, [scriptFor(state.project), ...args, '--root', root || state.project], root || state.project,
  { ...modelEnv(agent || require('./settings').get().agent), ...require('./app-token').envFor() }, timeout, input);
// 不针对某个项目、直接用应用自带的 rf.py
const rfBundled = (args, cwd) => exec(PYTHON, [BUNDLED, ...args], cwd, ['bootstrap', 'sync'].includes(args[0]) ? modelEnv(require('./settings').get().agent) : { RELAYFORGE_LANGUAGE: require('./settings').language() });

// 应用如果是从一个 Claude Code 会话里启动的，会带着那个会话的标记；不去掉的话，
// 这里开出的会话会被当成它的子会话，聊天记录不保存，右边也就整理不出进展。
function cleanEnv() {
  const env = { ...process.env };
  for (const name of ['CLAUDE_CODE_CHILD_SESSION', 'CLAUDE_CODE_SESSION_ID', 'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_PROJECT_DIR', 'CODEX_THREAD_ID', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE', 'RELAYFORGE_APP_TOKEN', 'RELAYFORGE_SESSION_SIG']) delete env[name];
  return withCliPath(env);
}
const lastLine = (text) => text.trim().split('\n').pop() || uiText('没有给出原因', 'No reason provided');
// 命令输出的 JSON；读不懂时给 fallback
function parse(text, fallback) { try { return JSON.parse(text); } catch (_) { return fallback; } }

module.exports = { uiText, exec, rf, rfBundled, cleanEnv, lastLine, parse };
