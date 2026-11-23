// 后台记录（设置里的「后台记录」，默认关）：会话每说完一轮，交给 rf.py monitor record，把新说完的几轮整理成精简记录，
// 追加到本机的 ~/.relayforge/monitor/项目/（不进仓库）。过一段时间让 Agent 读那个文件夹分析：有没有照规矩做、哪些工具没用、在哪里打转。
// 一轮说完：Claude Code 是钩子报的「回复完了 / 在问你」，Codex 是聊天记录里的这一轮结束（都经 attention.onSettled 来）。
// 等你批准：从「等你批准」到它又动起来的这段时间，随下一次记录交给 rf.py，记到那一轮上。会话关掉时把没说完的最后一轮也记下。
const { rf } = require('./backend');
const settings = require('./settings');
const { state } = require('./state');

const TIMEOUT = 60000;
const approvals = new Map(); // 会话 ID -> { since, done: [{ at, seconds }] }
const queue = new Map(); // 会话 ID -> 正在跑的记录：同一个会话一次只跑一个，按先后

// 开关只在启动时读一次，之后由保存设置时更新（ipc.js 的 settings:save），收到事件时不再读设置文件
let on = false;
try { on = settings.get().monitor === true; } catch (_) { /* 读不出设置就当关着 */ }
function enabled() { return on; }
function setEnabled(value) {
  on = value === true;
  if (!on) approvals.clear();
}

function record(run, closed) {
  const waits = approvals.get(run.sessionId);
  const done = waits ? waits.done.splice(0) : [];
  if (waits && closed) approvals.delete(run.sessionId);
  const args = ['monitor', 'record', '--session', run.sessionId, '--kind', run.kind || 'chat', '--json'];
  if (run.owner) args.push('--owner', run.owner);
  if (run.agent) args.push('--agent', run.agent);
  if (run.transcript) args.push('--transcript', run.transcript);
  if (done.length) args.push('--waits', JSON.stringify(done));
  if (closed) args.push('--closed');
  const before = queue.get(run.sessionId) || Promise.resolve();
  const next = before.then(() => rf(args, run.root, undefined, TIMEOUT)).then((out) => {
    if (out && out.ok === false && waits && !closed) waits.done.unshift(...done); // 没记成：等批准的时间留到下一次
  }).catch(() => { /* 记不下来不影响会话，下一轮会把漏的几轮一起补上 */ });
  queue.set(run.sessionId, next);
  next.finally(() => { if (queue.get(run.sessionId) === next) queue.delete(run.sessionId); });
  return next;
}

// attention.onSettled 的听众：kind 是 running / reply / answer / approve / exit
function settled(id, kind) {
  if (!on) return;
  const run = state.runs.get(id);
  if (!run || !run.sessionId) return;
  let waits = approvals.get(run.sessionId);
  if (kind === 'approve') {
    if (!waits) approvals.set(run.sessionId, waits = { since: null, done: [] });
    if (!waits.since) waits.since = Date.now();
    return;
  }
  if (waits && waits.since) {
    waits.done.push({ at: new Date(waits.since).toISOString(), seconds: Math.round((Date.now() - waits.since) / 1000) });
    waits.since = null;
  }
  if (kind === 'reply' || kind === 'answer' || kind === 'exit') record(run, false);
}

// 会话关掉或退出了：把说到一半的最后一轮也记下
function ended(run) {
  if (!on || !run || !run.sessionId) return;
  const waits = approvals.get(run.sessionId);
  if (waits && waits.since) {
    waits.done.push({ at: new Date(waits.since).toISOString(), seconds: Math.round((Date.now() - waits.since) / 1000) });
    waits.since = null;
  }
  record(run, true);
}

module.exports = { settled, ended, enabled, setEnabled };
