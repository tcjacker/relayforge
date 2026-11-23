// 提醒：Claude Code 在干活、回复完了，或者停下来等你。
// 做法是启动 Claude Code 时带上钩子（见 claude-settings.js），所有钩子都交给 rf.py hook event：它在 EVENTS 旁边记着每个会话的状态
// （包括还在跑的子代理），把已经判断好的结果一行一行追加到 EVENTS，这里只管读出来、显示。
const { uiText } = require('./locale');
const { app, Notification } = require('electron');
const { execFile } = require('child_process');
const fs = require('fs');
const { BUNDLED, EVENTS, PYTHON } = require('./config');
const { state, send } = require('./state');

const waiting = new Map(); // 需求或聊天的编号 -> { kind, text }
const running = new Set(); // 正在干活的需求或聊天的编号：安静地标出来，不算在提醒的数字里
let viewing = null, eventsRead = 0, eventsRest = '', sweeping = false;
const settledListeners = []; // 会话这一轮说完了（回复完或在问你）时要通知的人，见 merge.js
function onSettled(fn) { settledListeners.push(fn); }
function mergeProgress(id, kind, text) {
  if (['running', 'reply', 'answer', 'approve', 'exit'].includes(kind)) setPhase(id, kind === 'exit' ? 'exited' : kind);
  for (const fn of settledListeners) { try { fn(id, kind, text); } catch (_) { /* 一个听众出错不影响提醒 */ } }
}

// 每个开着的会话现在在干什么（收件箱据此决定能不能往里送话，见 inbox.js）：
// starting 刚打开、running 在干活、reply 说完了、answer 在问你、approve 在等你批准、idle 停着（被打断或很久没动静）、exited 已退出。
// 来源：Claude Code 和接上钩子的 Codex 走钩子（EVENTS），没接上钩子的 Codex 读聊天记录（watcher.js），都经过 mergeProgress 或 attention()。
const phases = new Map(); // 会话编号 -> { phase, since }
const phaseListeners = [];
function onPhase(fn) { phaseListeners.push(fn); }
function setPhase(id, phase) {
  const before = phases.get(id);
  if (before && before.phase === phase) return;
  phases.set(id, { phase, since: Date.now() });
  for (const fn of phaseListeners) { try { fn(id, phase); } catch (_) { /* 一个听众出错不影响别的 */ } }
}
function phaseOf(id) { return (phases.get(id) || { phase: null }).phase; }
function phaseStateOf(id) { return phases.get(id); }
function forgetPhase(id) { phases.delete(id); }

function pushWaiting() {
  send('waiting', Object.fromEntries(waiting));
  if (app.dock) app.dock.setBadge(waiting.size ? String(waiting.size) : '');
}
function pushRunning() { send('running', [...running]); }
function seen(id) { if (waiting.delete(id)) pushWaiting(); }
// 会话结束了：不再算它正在干活
function stopped(id) { if (running.delete(id)) pushRunning(); }

// 界面告诉我们你正在看哪个会话
function setViewing(id) { viewing = id; if (id && state.win && state.win.isFocused()) seen(id); }
// 窗口回到前台：你正看着的那个不用再提醒
function onFocus() { if (viewing) seen(viewing); }

// 一行已经判断好的状态：{ session_id, status: running | waiting | idle, kind, text }
function attention(run, event) {
  // 无界面运行的会话：钩子报过这一轮说完了（回复完或在问你），headless.js 在进程退出时就不再报一次
  if (run.headless) run.hookKind = event.status === 'waiting' && ['reply', 'answer'].includes(event.kind || 'reply') ? event.kind || 'reply' : null;
  if (event.status === 'running' || event.status === 'idle') {
    if (event.status === 'running') {
      running.add(run.id);
      mergeProgress(run.id, 'running');
    } else { running.delete(run.id); setPhase(run.id, 'idle'); }
    pushRunning();
    if (event.status === 'running' && waiting.delete(run.id)) pushWaiting(); // 它又动起来了，说明等的事已经有了回音
    return;
  }
  if (event.status !== 'waiting') return;
  mergeProgress(run.id, event.kind || 'reply');
  if (event.kind !== 'approve') { try { require('./services').turnEnded(run.id); } catch (_) { /* 重启不了不影响提醒 */ } } // 开发会话一轮停下：它的验收服务代码改过就重启
  if (running.delete(run.id)) pushRunning();
  const kind = ['reply', 'answer', 'approve'].includes(event.kind) ? event.kind : 'reply';
  const text = String(event.text || uiText('回复完了', "Reply complete"));
  const { win } = state;
  if (win && win.isFocused() && viewing === run.id) return; // 你正看着它，不用提醒
  const before = waiting.get(run.id);
  waiting.set(run.id, { kind, text });
  pushWaiting();
  if (before && before.kind === kind && before.text === text) return; // 同一件事不重复弹
  if (!Notification.isSupported()) return;
  const head = { approve: uiText('等你批准', "Waiting for your approval"), answer: uiText('在等你回答', "Waiting for your answer"), reply: uiText('回复完了', "Reply complete") }[kind];
  const note = new Notification({ title: `${run.title || 'RelayForge'} · ${head}`, body: text, silent: false });
  note.on('click', () => { if (state.win) { state.win.show(); state.win.focus(); send('goto', run.id); } });
  note.show();
}

// 问题的提醒（初判完了等你决定、修复交回了等你确认）：不是会话，不进上面的数字（问题的数字在「问题」图标上），
// 只弹一条系统通知，点一下跳到那条问题。你正看着它时不弹；同一件事不重复弹。
const told = new Map();
function issue(id, title, body) {
  const { win } = state;
  if (win && win.isFocused() && viewing === id) return;
  if (told.get(id) === title + body) return;
  told.set(id, title + body);
  if (!Notification.isSupported()) return;
  const note = new Notification({ title, body: String(body || ''), silent: false });
  note.on('click', () => { if (state.win) { state.win.show(); state.win.focus(); send('goto', id); } });
  note.show();
}

// 由应用发出的提醒会在会话旁标出来，系统通知可以点开对应会话。你正看着它时只标不弹。
function remind(id, title, text, kind = 'test') {
  waiting.set(id, { kind, text: String(text || title) });
  pushWaiting();
  const { win } = state;
  if (win && win.isFocused() && viewing === id) return;
  if (!Notification.isSupported()) return;
  const note = new Notification({ title, body: String(text || ''), silent: false });
  note.on('click', () => { if (state.win) { state.win.show(); state.win.focus(); send('goto', id); } });
  note.show();
}

// 会话里的 rf.py 发现自己不在系统的沙箱里（backend/relayforge/project/status.py 的 report_unsandboxed）：沙箱没生效，
// RelayForge 挡不住这个会话冒充你、读保密值。在它的终端里写一句、弹一条通知；每个会话只提醒一次
function unsandboxed(run) {
  if (run.unsandboxed) return;
  run.unsandboxed = true;
  // 原因按会话用的工具说：Codex 是会话申请在沙箱外执行、又被批准了（应用开的会话固定由你本人批准，见 sessions.js 的 codexSandboxFlags）；
  // Claude Code 是沙箱没生效（版本、设置，开会话前已经查过，见 sandbox-check.js）
  const text = run.agent === 'codex'
    ? uiText('这个 Codex 会话有命令在系统的沙箱外运行了（它申请越出沙箱，并且被批准了）：RelayForge 挡不住它冒充你的操作、读保密值。请检查它做了什么，必要时关掉它。', "A command in this Codex session ran outside the system sandbox after approval. Review the session's actions and close it if needed. Outside the sandbox it can act on your behalf and read secrets.")
    : uiText('这个会话有命令在系统的沙箱外运行了：RelayForge 挡不住它冒充你的操作、读保密值。请关掉它，检查 Claude Code 的版本和沙箱设置（设置 → CLI）后重新打开。', "A command in this session ran outside the system sandbox, where it can act on your behalf and read secrets. Close the session, check the Claude Code version and sandbox settings under Settings → CLI, then reopen it.");
  send('term:data', { id: run.id, data: '\r\n\x1b[33m⚠ ' + text + '\x1b[0m\r\n' });
  if (!Notification.isSupported()) return;
  const note = new Notification({ title: uiText('会话没有在沙箱里', "Session outside the sandbox"), body: text, silent: false });
  note.on('click', () => { if (state.win) { state.win.show(); state.win.focus(); send('goto', run.id); } });
  note.show();
}

function readEvents() {
  let size = 0;
  try { size = fs.statSync(EVENTS).size; } catch (_) { return; }
  if (size <= eventsRead) return;
  const fd = fs.openSync(EVENTS, 'r'), buf = Buffer.alloc(size - eventsRead);
  fs.readSync(fd, buf, 0, buf.length, eventsRead); fs.closeSync(fd);
  eventsRead = size;
  const lines = (eventsRest + buf.toString('utf8')).split('\n');
  eventsRest = lines.pop();
  for (const line of lines) {
    let event; try { event = JSON.parse(line); } catch (_) { continue; }
    const run = [...state.runs.values()].find((r) => r.sessionId === event.session_id);
    if (!run) continue;
    if (event.status === 'unsandboxed') { unsandboxed(run); continue; }
    run.hooked = true; // Codex 会话的钩子已经在报状态（你信任过了），不再另从聊天记录读这一轮开始和结束（watcher.js）
    attention(run, event);
  }
}

// 钩子只在 Claude Code 有动静时才跑。等子代理的「回复完了」、久久没有动静的「正在干活」要靠时间来了结：
// 状态文件里每个会话带着「什么时候该看一眼」（due），到点了就让 rf.py hook sweep 处理一次，结果同样追加到 EVENTS。
function sweepIfDue() {
  if (sweeping) return;
  let sessions = {};
  try { sessions = JSON.parse(fs.readFileSync(EVENTS + '.state', 'utf8')).sessions || {}; } catch (_) { return; }
  const now = Date.now() / 1000;
  if (!Object.values(sessions).some((s) => s && typeof s.due === 'number' && s.due <= now)) return;
  sweeping = true;
  execFile(PYTHON, [BUNDLED, 'hook', 'sweep'], { env: { ...process.env, RELAYFORGE_EVENTS: EVENTS } }, () => { sweeping = false; });
}

// 退出时删掉事件文件和它旁边的状态文件
function cleanup() {
  for (const f of [EVENTS, EVENTS + '.state', EVENTS + '.lock']) { try { fs.unlinkSync(f); } catch (_) { /* 没有产生过 */ } }
}

module.exports = { issue, remind, onSettled, mergeProgress, onPhase, setPhase, phaseOf, phaseStateOf, forgetPhase, seen, stopped, pushRunning, setViewing, onFocus, readEvents, sweepIfDue, cleanup };
