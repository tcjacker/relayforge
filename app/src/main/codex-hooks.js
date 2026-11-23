// Codex 会话的钩子：和 Claude Code 的（claude-settings.js）做同样的事——报告在干活、说完了、在等你批准（attention.js 读 EVENTS），
// 停下来时的检查，执行前拦下一定会被拒绝的操作并说明该怎么做（backend/relayforge/core/guard.py）。
//
// 用启动参数 -c hooks.<事件>=[…] 传进去，不改你的 ~/.codex/hooks.json。Codex 要求钩子先经信任才会运行：应用启动时检查，
// 缺了就经 Codex 的 app-server 写入（codex-trust.js）。信任按钩子的内容记（~/.codex/config.toml 的 [hooks.state]），而且同一个键只记一份，
// 所以这里的内容必须固定：rf.py 的路径如果写进命令，主应用、测试界面、各工作目录就会互相顶掉对方的信任。
// 每个会话不同的东西（rf.py 在哪、RelayForge 的会话编号、这个会话不能改哪些地方）都放进会话的环境变量，
// 钩子里的 rf.py hook codex 从环境变量读。没信任之前钩子不运行，提醒照旧靠读 Codex 的聊天记录（codex-turns.js）。
const { createHash } = require('crypto');
const { PYTHON, QUOTED, BUNDLED } = require('./config');

// Windows 上 Codex 不一定经 POSIX shell 跑命令，$变量 展不开，仍写实际路径
const commandFor = (platform) => (platform === 'win32'
  ? `${PYTHON} ${QUOTED} hook codex`
  : '$RELAYFORGE_HOOK_PYTHON "$RELAYFORGE_HOOK_RF" hook codex');
const COMMAND = commandFor(process.platform);
// 事件 -> [超时秒数, 是否在后台跑]。执行前和停下来要等结果；执行后只是报状态，放后台；Codex 的打断最多只给 3 秒
const EVENTS = {
  UserPromptSubmit: [30, false],
  PreToolUse: [30, false],
  PermissionRequest: [3600, false], // 无界面运行时要等你在应用里点批准或拒绝（core/approvals.py）；有终端的会话钩子马上返回
  PostToolUse: [30, true],
  Stop: [60, false],
  Interrupt: [3, false],
};

function flags() {
  return Object.entries(EVENTS).flatMap(([name, [timeout, background]]) =>
    ['-c', `hooks.${name}=[{matcher="*",hooks=[{type="command",command=${JSON.stringify(COMMAND)},timeout=${timeout}${background ? ',async=true' : ''}}]}]`]);
}

// Codex 记信任用的键和指纹（codex-rs/hooks 的 hook_key、discovery.rs 的 hook_hash）：键是「来源:事件:组号:序号」，-c 传进来的
// 来源固定写成 /<session-flags>/config.toml；指纹是 sha256(按键排序的紧凑 JSON {event_name, matcher?, hooks:[这一个钩子]})，
// 这几个事件不看 matcher，算的时候也不带。和 ~/.codex/config.toml 里 [hooks.state."键"] 的 trusted_hash 一样，就是信任过了
const SNAKE = { UserPromptSubmit: 'user_prompt_submit', PreToolUse: 'pre_tool_use', PermissionRequest: 'permission_request',
  PostToolUse: 'post_tool_use', Stop: 'stop', Interrupt: 'interrupt' };
const NO_MATCHER = new Set(['user_prompt_submit', 'stop', 'interrupt']);
const sorted = (v) => (Array.isArray(v) ? v.map(sorted) : v && typeof v === 'object'
  ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, sorted(v[k])])) : v);
function trustEntries(platform = process.platform, command = commandFor(platform)) {
  const source = platform === 'win32' ? 'C:\\<session-flags>\\config.toml' : '/<session-flags>/config.toml';
  return Object.entries(EVENTS).map(([name, [timeout, background]]) => {
    const event = SNAKE[name];
    const identity = { event_name: event, hooks: [{ async: background, command, timeout, type: 'command' }],
      ...(NO_MATCHER.has(event) ? {} : { matcher: '*' }) };
    const hash = 'sha256:' + createHash('sha256').update(JSON.stringify(sorted(identity))).digest('hex');
    return { name, key: `${source}:${event}:0:0`, hash, command, timeout };
  });
}

// 放进会话环境变量的那部分：钩子按它决定拦什么（guard.py 的 codex_main）
function env(spec) {
  return { RELAYFORGE_GUARD: JSON.stringify(spec || {}), RELAYFORGE_HOOK_PYTHON: PYTHON, RELAYFORGE_HOOK_RF: BUNDLED };
}

module.exports = { flags, env, trustEntries, COMMAND, EVENTS };
