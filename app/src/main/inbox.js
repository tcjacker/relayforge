// 会话收件箱：程序发给会话的每一句话（测试结论、再测一轮、合并前的同步、问题退回、拆分回执……）都从这里出去，
// 不再各处直接往终端里输（设计见 docs/design/会话通道与需求冲突-设计.md 第一部分）。
//
// - 先记下来：存在本机 ~/.relayforge/inbox/<项目>.json（写法和合并队列一样），应用重启也不丢；带 key 的同一件事只发一次。
// - 标来源：每段话前面是「〔RelayForge 程序 · 来源 · 编号〕」。开场白里说明了这种话是程序转来的通知，不是用户的指示（prompts.js 的 INBOX_GUIDE）。
// - 看对方在干什么再送（attention.js 的会话状态）：说完了（reply）或停着（idle）才送；在干活、在问你问题、在等你批准时排队，
//   等它这一轮说完再送——在等批准时输进去的字和回车可能被当成对批准框的回答。后台 Codex 可以用 RPC 在运行中收话。
// - 会话没开着：先排着，让界面在后台打开它（和以前一样），打开时把排着的话放进开场白（sessions.js 调 takeForOpen）。
// - 怎么交（第 4 步）：会话的钩子能用时不往终端里粘贴正文——
//     这一轮说完时由 Stop 钩子把排给它的话交过去（backend/relayforge/core/inbox.py）；
//     会话早就停下了，只往终端里输一行固定的唤醒句（WAKE），提交时由 UserPromptSubmit 钩子把正文作为补充的上下文交过去。
//   钩子交出去的编号写在 <收件箱文件>.delivered，这里读到就记成已送出（via: stop | prompt）。钩子还不能用（Codex 没信任、
//   这个会话还没报过状态）或者唤醒以后钩子没有交（这一轮说完了还排着），退回往终端里粘贴正文（via: type）。
// - 回执：会话的聊天记录里出现了这条的编号，就算它读到了（seen）。
const fs = require('fs');
const { uiText } = require('./locale');
const path = require('path');
const { createHash, randomBytes, randomUUID } = require('crypto');
const { RF_HOME } = require('./config');
const { state, send } = require('./state');
const attention = require('./attention');
const stuck = require('./stuck');

const FROM = { testing: '测试', testsets: '测试集', issues: '问题', splits: '拆分', merge: '合并', conflicts: '冲突', app: '应用' };
const WAKE = '〔RelayForge〕有新的程序消息，正文在这条消息附带的上下文里，请按它处理。'; // 开头必须和 core/inbox.py 的 WAKE 一样
const READY = new Set(['reply', 'idle']); // 只在这两种状态下往终端里送
const KEEP = 50; // 每个会话最多留多少条已经送出去的，再早的删掉
const steering = new Set(); // 同一会话每次只交一批，等 RPC 回执再取下一批。
const loaded = new Map(); // 项目 -> 消息列表

function fileOf(root) {
  return path.join(RF_HOME, 'inbox', createHash('sha256').update(String(root)).digest('hex') + '.json');
}

function listOf(root) {
  if (!loaded.has(root)) {
    let saved = [];
    try { saved = JSON.parse(fs.readFileSync(fileOf(root), 'utf8')); } catch (error) { if (error.code !== 'ENOENT') console.error('读取会话收件箱失败', error); }
    loaded.set(root, Array.isArray(saved) ? saved : []);
  }
  return loaded.get(root);
}

function save(root) {
  const list = listOf(root);
  const kept = [];
  const done = new Map();
  for (const m of [...list].reverse()) { // 每个会话只留最近 KEEP 条已送出或已撤回的记录
    if (m.state === 'queued') { kept.push(m); continue; }
    const n = done.get(m.to) || 0;
    if (n < KEEP) { kept.push(m); done.set(m.to, n + 1); }
  }
  kept.reverse();
  const file = fileOf(root);
  const temp = file + '.' + randomUUID();
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(temp, JSON.stringify(kept, null, 1) + '\n');
    fs.renameSync(temp, file);
    list.splice(0, list.length, ...kept);
    return true;
  } catch (error) {
    try { fs.unlinkSync(temp); } catch (_) { /* 没写出来 */ }
    console.error('保存会话收件箱失败', error);
    return false;
  }
}

function changed(root, recipients) {
  if (root !== state.project) return;
  const queued = new Set(listOf(root).filter((m) => m.state === 'queued').map((m) => m.to));
  for (const id of recipients) send('inbox:changed', { id, queued: queued.has(id) });
}

function wrap(m) {
  return `〔RelayForge 程序 · ${m.label || FROM[m.from] || FROM.app} · ${m.id}〕\n${m.text}`;
}

// 钩子交出去的（<收件箱文件>.delivered，一行「编号<TAB>方式<TAB>时间」）：记成已送出
function absorbDelivered(root) {
  let text = '';
  try { text = fs.readFileSync(fileOf(root) + '.delivered', 'utf8'); } catch (_) { return; }
  const done = new Map(text.split('\n').filter(Boolean).map((line) => { const [id, via, at] = line.split('\t'); return [id, { via, at }]; }));
  const recipients = new Set();
  for (const m of listOf(root)) {
    const d = done.get(m.id);
    // 钩子可能在撤回保存前就取走了消息，回执仍以实际交付为准。
    if (d && (m.state === 'queued' || m.state === 'dropped')) {
      Object.assign(m, { state: 'sent', sent: d.at || new Date().toISOString(), via: d.via || 'hook' });
      delete m.woke;
      recipients.add(m.to);
    }
  }
  if (recipients.size) { save(root); changed(root, recipients); }
}

// 几条排着的话合成一段（按先后），标好各自的来源
function combine(messages) {
  return messages.map(wrap).join('\n\n');
}

// 让界面在后台打开这个会话（和以前一样：修复、排查会话走问题页，需求的开发、测试会话走需求页）；打开时 takeForOpen 把排着的话带进开场白
// 测试会话的再测一轮、问题的修复会话：不用人盯着，在后台无界面运行（headless.js）
function requestOpen(to) {
  if (to.startsWith('test:') || to.startsWith('fix:')) return void require('./sessions').open({ id: to, headless: true });
  const issue = to.startsWith('fix:') || to.startsWith('probe:');
  send(issue ? 'issues:say' : 'tests:say', { id: to, ...(to.startsWith('test:') ? { test: true } : {}) });
}

// 程序要对一个会话说一句话。to：会话在应用里的编号（R-…、test:R-…、fix:F-…、probe:I-…、session:…）；
// from：testing | testsets | issues | splits | merge | app；key：同一件事只发一次用；open：会话没开着时要不要让界面把它打开（默认要）
function post({ to, text, from = 'app', key = null, open = true } = {}) {
  const root = state.project;
  if (!root || !to || !String(text || '').trim()) return null;
  const list = listOf(root);
  if (key) {
    const same = list.find((m) => m.to === to && m.key === key && m.state !== 'dropped');
    if (same) return same;
  }
  const run = state.runs.get(to);
  // 后台 Codex 由 RPC 交付，先不填 session，免得 Stop 钩子同时读到同一条消息。
  const m = { id: 'm-' + randomBytes(3).toString('hex'), to, from, label: FROM[from] || FROM.app, key, text: String(text).trim(), created: new Date().toISOString(),
    state: 'queued', session: run && !run.steer ? run.sessionId : null, sent: null, seen: null };
  list.push(m);
  save(root);
  changed(root, [to]);
  if (run) flush(to);
  else if (open) requestOpen(to);
  return m;
}

// 会话开着、而且现在能收：把排着的话一次送出去
function flush(to) {
  const root = state.project;
  const run = state.runs.get(to);
  if (!root || !run) return 0;
  absorbDelivered(root);
  markSeen(run);
  if (run.steer) {
    if (steering.has(run)) return 0;
    const queued = listOf(root).filter((m) => m.to === to && m.state === 'queued');
    if (!queued.length) return 0;
    const ack = run.steer(combine(queued), true);
    if (!ack) return 0;
    steering.add(run);
    ack.then(() => {
      const now = new Date().toISOString();
      for (const m of queued) Object.assign(m, { state: 'sent', sent: now, session: run.sessionId, via: 'steer' });
      save(root);
      steering.delete(run);
      if (state.project === root && state.runs.get(to) === run) flush(to);
    }, () => steering.delete(run)); // 这一轮没收到，保留在队列里。
    return queued.length;
  }
  if (run.headless) return 0; // Claude Code 的 Stop 钩子先交，剩下的等下一轮（afterHeadless）。
  if (!READY.has(attention.phaseOf(to))) return 0;
  const queued = listOf(root).filter((m) => m.to === to && m.state === 'queued');
  if (!queued.length) return 0;
  const now = new Date().toISOString();
  attention.setPhase(to, 'running'); // 话已经交给它了：这一轮说完之前不再送下一批
  if (run.hooked && !queued.some((m) => m.woke)) {
    // 钩子能用：只输唤醒句，正文由 UserPromptSubmit 钩子交过去（交了会写进 .delivered）
    for (const m of queued) Object.assign(m, { woke: now, session: run.sessionId });
    save(root);
    require('./sessions').type(run, WAKE);
    return queued.length;
  }
  // 钩子不能用，或者唤醒过一次钩子却没交：往终端里粘贴正文
  for (const m of queued) { Object.assign(m, { state: 'sent', sent: now, session: run.sessionId, via: 'type' }); delete m.woke; }
  save(root);
  changed(root, [to]);
  require('./sessions').type(run, combine(queued));
  return queued.length;
}

// 打开一个会话时（sessions.js）：排着的话随开场白一起交给它，记成已送出
function takeForOpen(to, sessionId) {
  const root = state.project;
  if (!root || !to) return '';
  const queued = listOf(root).filter((m) => m.to === to && m.state === 'queued');
  if (!queued.length) return '';
  const now = new Date().toISOString();
  for (const m of queued) Object.assign(m, { state: 'sent', sent: now, session: sessionId || null, via: 'open' });
  save(root);
  changed(root, [to]);
  return combine(queued);
}

// 回执：已经送出去、还没确认的，在会话的聊天记录里找它的编号
function markSeen(run) {
  const root = state.project;
  if (!root || !run || !run.transcript) return;
  const sent = listOf(root).filter((m) => m.to === run.id && m.state === 'sent' && (!m.session || m.session === run.sessionId));
  if (!sent.length) return;
  let text = '';
  try { text = fs.readFileSync(run.transcript, 'utf8'); } catch (_) { return; }
  const now = new Date().toISOString();
  const seen = sent.filter((m) => text.includes(m.id));
  if (!seen.length) return;
  for (const m of seen) Object.assign(m, { state: 'seen', seen: now });
  save(root);
  changed(root, [run.id]);
}

function watchDelivery(findTranscript, now = Date.now()) {
  const root = state.project;
  if (!root) return;
  const pending = new Map();
  for (const m of listOf(root)) {
    if (m.state !== 'sent' || !m.session || m.retryWarned) continue;
    const key = JSON.stringify([m.to, m.session]);
    if (!pending.has(key)) pending.set(key, []);
    pending.get(key).push(m);
  }
  for (const messages of pending.values()) {
    const { to, session } = messages[0];
    const open = state.runs.get(to);
    const run = open?.sessionId === session ? open : null;
    const ready = run && !run.headless && READY.has(attention.phaseOf(to));
    const result = stuck.checkDelivery(messages, ready, () => {
      markSeen({ id: to, sessionId: session, transcript: run?.transcript || findTranscript(session) });
    }, now);
    if (!result || (!result.retry.length && !result.unread.length)) continue;
    save(root);
    changed(root, [to]); // 程序消息面板跟着刷新
    if (result.retry.length) {
      attention.setPhase(to, 'running');
      // 钩子按编号去重，重送保留原编号并沿用直接粘贴的交付方式。
      require('./sessions').type(run, combine(result.retry));
    }
    if (result.unread.length) attention.remind(to, uiText('程序消息还未读到', 'Program messages still unread'),
      uiText('重送后仍未在聊天记录里看到消息 {0}。',
        'The chat record still does not show these messages after retry: {0}.', result.unread.join('、')), 'unread');
  }
}

// 无界面运行的一轮结束了：还有排着的话，就带着它们再在后台跑一轮
function afterHeadless(to) {
  const root = state.project;
  if (!root || state.runs.get(to)) return false;
  absorbDelivered(root);
  if (!listOf(root).some((m) => m.to === to && m.state === 'queued')) return false;
  require('./sessions').open({ id: to, headless: true });
  return true;
}

// 会话状态变了（attention.onPhase）：能收了就送
function phaseChanged(id, phase) {
  if (READY.has(phase)) flush(id);
}

// 界面上看：这个会话的程序消息和送到没有
function messagesFor(to) {
  const root = state.project;
  if (root) absorbDelivered(root);
  return root ? listOf(root).filter((m) => m.to === to).map((m) => ({ ...m })) : [];
}

function queuedForProject() {
  const root = state.project;
  if (!root) return [];
  absorbDelivered(root);
  return [...new Set(listOf(root).filter((m) => m.state === 'queued').map((m) => m.to))];
}

function withdraw(to, id) {
  const root = state.project;
  if (!root) return false;
  // 钩子可能已交出去，界面还显示着上一次取到的「排队中」。
  absorbDelivered(root);
  const m = listOf(root).find((m) => m.to === to && m.id === id && m.state === 'queued');
  if (!m) return false;
  m.state = 'dropped';
  if (!save(root)) {
    m.state = 'queued';
    return false;
  }
  absorbDelivered(root);
  if (m.state !== 'dropped') return false;
  changed(root, [to]);
  return true;
}

// 会话的环境变量里放收件箱文件的位置，钩子据此读（core/inbox.py）
function envFor(root) {
  return root ? { RELAYFORGE_INBOX: fileOf(root) } : {};
}

module.exports = { post, flush, afterHeadless, takeForOpen, markSeen, watchDelivery, phaseChanged, messagesFor, queuedForProject, withdraw,
  wrap, envFor, absorbDelivered, READY, WAKE };
