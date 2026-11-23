// 无界面运行的会话需要你的时候（human-in-the-loop）：
// - 要你批准：钩子把请求写进 ~/.relayforge/approvals/<项目>/<编号>.json 等你（backend/relayforge/core/approvals.py），
//   这里读出来推给界面（右下角的卡片）、弹系统通知；你点批准或拒绝，这里写回 <编号>.decision，钩子把结果交给会话。
// - Codex 走 app-server 的 RPC 请求。同一张卡片显示实际动作，用户决定后用对应请求编号回复。
// - 在问你：界面给出问题和回复框。Codex 运行中可以直接回复，已经停下的话就在后台续上。
// - 在终端里接管：停掉后台运行，让界面用交互终端接着这个会话（同一个会话编号，聊天记录接在一起）。
const { uiText } = require('./locale');
const fs = require('fs');
const path = require('path');
const { createHash, randomUUID } = require('crypto');
const { Notification } = require('electron');
const { RF_HOME } = require('./config');
const { state, send } = require('./state');
const attention = require('./attention');

const questions = new Map(); // 会话在应用里的编号 -> { id, title, text, at }
const stalled = new Map(); // 项目 -> 已停止、等待接管的后台会话
const rpcApprovals = new Map();
const rpcQuestions = new Map();
const takeovers = new Map();
const APPROVAL_TIMEOUT = 50 * 60 * 1000; // 和 Claude Code 的钩子等待时间一致。
let shown = '';
const told = new Set();

function dirOf(root) {
  return path.join(RF_HOME, 'approvals', createHash('sha256').update(String(root)).digest('hex').slice(0, 24));
}

// 无界面会话的环境变量：钩子据此知道要等你（core/approvals.py）
function envFor(root) {
  return root ? { RELAYFORGE_HEADLESS: '1', RELAYFORGE_APPROVALS: dirOf(root) } : {};
}

function runOfSession(sessionId) {
  return [...state.runs.values()].find((r) => r.sessionId === sessionId) || null;
}

function approvals(root) {
  let names = [];
  try { names = fs.readdirSync(dirOf(root)).filter((n) => n.endsWith('.json')); } catch (_) { return []; }
  const out = [];
  for (const name of names) {
    let r;
    try { r = JSON.parse(fs.readFileSync(path.join(dirOf(root), name), 'utf8')); } catch (_) { continue; }
    if (!r || !r.id) continue;
    const run = runOfSession(r.session);
    const input = r.input || {};
    out.push({ id: r.id, run: run ? run.id : null, title: run ? run.title || run.id : uiText('后台会话', "Background session"), tool: r.tool,
      what: String(input.command || input.file_path || input.description || JSON.stringify(input)).slice(0, 400), why: r.description || input.description || '', at: r.at });
  }
  return out.sort((a, b) => String(a.at).localeCompare(String(b.at)));
}

function snapshot() {
  const root = state.project;
  const active = [...rpcApprovals.values()].filter(({ project, run }) => project === root && state.runs.get(run.id) === run);
  return { approvals: [...(root ? approvals(root) : []), ...active.map(({ card }) => card)], questions: [...questions.values()],
    stuck: [...(stalled.get(root)?.values() || [])] };
}

function notify(title, body, id) {
  if (!Notification.isSupported || !Notification.isSupported()) return;
  const note = new Notification({ title, body: String(body || '').slice(0, 200), silent: false });
  note.on('click', () => { if (state.win) { state.win.show(); state.win.focus(); if (id) send('goto', id); } });
  note.show();
}

// watcher.js 每 1.5 秒调一次：有变化才推给界面，并返回正在等的批准请求供后台计时使用。
function tick() {
  const cards = stalled.get(state.project);
  if (cards) {
    // 用户也能直接打开会话接着做，已经重新打开的会话不再提示接管。
    for (const id of cards.keys()) if (state.runs.has(id)) cards.delete(id);
    if (!cards.size) stalled.delete(state.project);
  }
  const now = snapshot();
  const key = JSON.stringify(now);
  if (key === shown) return now.approvals;
  shown = key;
  send('hitl', now);
  for (const a of now.approvals) {
    if (told.has(a.id)) continue;
    told.add(a.id);
    notify(uiText("{0} · 等你批准", "{0} · Waiting for approval", a.title), `${a.tool}：${a.what}`, a.run);
  }
  return now.approvals;
}

function decide(id, allow, message) {
  const rpc = rpcApprovals.get(id);
  if (rpc && rpc.project === state.project && state.runs.get(rpc.run.id) === rpc.run) {
    rpc.finish(allow === true);
    return { ok: true };
  }
  if (!state.project || !/^a-[0-9a-f]{8}$/.test(String(id || ''))) return { error: uiText('没有这个批准请求。', "Approval request not found.") };
  const file = path.join(dirOf(state.project), id + '.decision');
  if (!fs.existsSync(path.join(dirOf(state.project), id + '.json'))) return { error: uiText('这个请求已经结束了（会话不等了，或者已经回答过）。', "This approval request has ended. The session moved on or already received an answer.") };
  fs.writeFileSync(file, JSON.stringify({ behavior: allow ? 'allow' : 'deny', ...(message ? { message: String(message) } : {}) }));
  setTimeout(tick, 300);
  return { ok: true };
}

// 同一张批准卡既能回给文件钩子，也能回给正在等请求的 app-server。
function requestApproval(run, request, decide) {
  const id = 'rpc-' + randomUUID();
  const card = { id, run: run.id, title: run.title || run.id, tool: request.tool, what: request.what, why: request.why,
    at: new Date().toISOString() };
  function clear() {
    if (!rpcApprovals.delete(id)) return;
    clearTimeout(timer);
    told.delete(id);
    tick();
  }
  function finish(allow, resume = true) {
    clear();
    decide(allow);
    if (resume) resumeAfterApproval(run);
  }
  const timer = setTimeout(() => finish(false), APPROVAL_TIMEOUT);
  rpcApprovals.set(id, { card, run, project: state.project, finish });
  attention.mergeProgress(run.id, 'approve');
  tick();
  return clear;
}

function resumeAfterApproval(run) {
  if (state.runs.get(run.id) !== run || [...rpcApprovals.values()].some((entry) => entry.run === run)) return;
  attention.mergeProgress(run.id, 'running');
  require('./inbox').flush(run.id);
}

const waitingForReply = (id) => questions.has(id);

function offerReply(run, text) {
  if (rpcQuestions.has(run.id)) return;
  if (text) setQuestion(run, text);
  else clearQuestion(run.id);
}

function requestAnswers(run, prompts, respond) {
  if (rpcQuestions.has(run.id)) return null;
  const answers = Object.create(null);
  const remaining = [...prompts];
  function next() {
    const q = remaining[0];
    setQuestion(run, [q.question, ...(q.options || []).map((option) => option.label + ' · ' + option.description)].join('\n'));
  }
  function clear() {
    if (rpcQuestions.delete(run.id)) clearQuestion(run.id);
  }
  rpcQuestions.set(run.id, (words) => {
    answers[remaining.shift().id] = { answers: [words] };
    if (remaining.length) return next();
    clear();
    respond(answers);
    resumeAfterApproval(run);
  });
  attention.setPhase(run.id, 'answer');
  next();
  return clear;
}

function setQuestion(run, text) {
  const preview = String(text || '').trim().slice(-1200);
  if (questions.get(run.id)?.text === preview) return;
  questions.set(run.id, { id: run.id, title: run.title || run.id, text: preview, at: new Date().toISOString() });
  notify(uiText("{0} · 在问你", "{0} · Waiting for your answer", run.title || run.id), String(text || '').trim().split('\n').pop(), run.id);
  tick();
}

function clearQuestion(id) {
  const question = questions.delete(id);
  // 同一会话重新在后台运行或被接管时，先收起旧卡片。
  const cards = stalled.get(state.project);
  const stopped = cards?.delete(id);
  if (cards && !cards.size) stalled.delete(state.project);
  if (question || stopped) tick();
}

function setStuck(run, text) {
  const root = state.project;
  if (!stalled.has(root)) stalled.set(root, new Map());
  stalled.get(root).set(run.id, { id: run.id, title: run.title || run.id, text });
  notify(uiText('后台会话卡住了', 'Background session stuck'), text, run.id);
  tick();
}

// 你回复它的问题：用你的原话在后台续上
async function reply(id, text) {
  const words = String(text || '').trim();
  if (!words) return { error: uiText('回复不能是空的。', "Enter a reply.") };
  const run = state.runs.get(id);
  if (run?.steer) {
    const respond = rpcQuestions.get(id);
    if (respond) {
      respond(words);
      return { ok: true };
    }
    const ack = run.steer(words);
    if (!ack) return { error: uiText('后台会话暂时不能收话，请等这一轮结束后重试。',
      'Wait for the background turn to finish, then try again.') };
    const answered = run.last;
    const question = questions.get(id);
    try { await ack; } catch (error) { return { error: error.message }; }
    run.answered = answered;
    if (questions.get(id) === question) clearQuestion(id);
    require('./inbox').flush(id);
    return { ok: true };
  }
  clearQuestion(id);
  await require('./sessions').open({ id, say: words, headless: true });
  return { ok: true };
}

// 在终端里接管：停掉后台运行（它等着的批准一并作废），让界面开交互终端
function takeover(id) {
  if (takeovers.has(id)) return takeovers.get(id);
  const run = state.runs.get(id);
  if (run && run.headless) {
    for (const a of approvals(state.project).filter((x) => x.run === id)) decide(a.id, false, uiText('用户在终端里接管了这个会话。', "The user took over this session in the terminal."));
    for (const rpc of rpcApprovals.values()) if (rpc.run === run) rpc.finish(false, false);
    require('./sessions').stop(run);
    if (run.headlessClosed) {
      clearQuestion(id);
      const root = state.project;
      const takingOver = run.headlessClosed.then(() => {
        takeovers.delete(id);
        if (root === state.project) send('hitl:takeover', { id });
        return { ok: true };
      });
      takeovers.set(id, takingOver);
      return takingOver;
    }
  }
  clearQuestion(id);
  send('hitl:takeover', { id });
  return { ok: true };
}

module.exports = {
  envFor, dirOf, tick, snapshot, decide, requestApproval, resumeAfterApproval,
  offerReply, waitingForReply, requestAnswers, setQuestion, clearQuestion, setStuck, reply, takeover,
};
