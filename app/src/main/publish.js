// 发布：把项目目录当前分支推送到远端。只由你在发布对话框里点按钮触发，从来不自动推送。
// 具体的事都由 rf.py publish 做；这里只是在后台运行它（有超时），一次只做一件事，并把状态告诉界面。
// 顶栏通过 rf.py publish status --json 读取发布状态和待验收数量，刷新时不抓取远端。
const fs = require('fs');
const path = require('path');
const { uiText = (zh) => zh, rf, lastLine, parse } = require('./backend');
const { state, send } = require('./state');
const Model = require('../renderer/scripts/publish-model');
const crypto = require('crypto');
const { mergingRemote } = require('./prompts');

const TIMEOUTS = { status: 20000, fetch: 60000, run: 180000, pull: 120000, list: 20000 }; // 毫秒；rf.py 自己对 git 另有更短的超时
const gate = Model.gate();
let latest = null;
let reading = null;
let lastStamp = null;

function busy() { return gate.working(); }
function pushBusy() { send('publish:busy', { working: busy() }); }
// 一次只做一件事；开始和结束都告诉界面，让按钮变灰
async function exclusive(name, fn) {
  if (busy()) return { error: uiText('正在', "Running ") + busy() + uiText('，等它做完再点。', ". Wait for it to finish."), busy: true };
  try {
    return await gate.run(name, async () => { pushBusy(); return fn(); });
  } finally { pushBusy(); }
}

// 这个项目里「让 AI 来合并」开的、最近的那个聊天：它还开着，或者聊天记录还在，才算「知道是哪个聊天」
function mergeChatId() {
  const mine = [...state.mergeChats].filter(([, v]) => v.project === state.project).map(([sid]) => sid).reverse();
  for (const sid of mine) if (state.runs.has('session:' + sid)) return 'session:' + sid;
  for (const sid of mine) if (require('./sessions').findTranscript(sid)) return 'session:' + sid;
  return null;
}

async function readStatus(extra = []) {
  const out = await rf(['publish', 'status', '--json', ...extra], undefined, undefined, extra.includes('--fetch') ? TIMEOUTS.fetch : TIMEOUTS.status);
  const made = parse(out.stdout, null);
  if (!made) return { error: out.timedOut ? uiText('读取发布状态超时了。', "Reading publish status timed out.") : lastLine(out.stderr) };
  const chat = made.busy ? mergeChatId() : null;
  if (chat) made.merge_chat = { id: chat };
  return made;
}

// 顶栏用：不抓取远端。同时只读一次
async function refresh(fresh = false) {
  if (!state.project) return latest;
  if (reading) {
    if (!fresh) return reading;
    await reading;
  }
  reading = readStatus().then((st) => { latest = st; send('publish', st); return st; }).finally(() => { reading = null; });
  return reading;
}

// 对话框打开时用：要抓取就先抓取，抓取失败照样给出状态和失败原因
function status({ fetch = false, remote } = {}) {
  return exclusive(fetch ? uiText('检查远端', "Check remote") : uiText('读取状态', "Read status"), async () => {
    const st = await readStatus([...(fetch ? ['--fetch'] : []), ...(remote ? ['--remote', remote] : [])]);
    if (!st.error) { latest = st; send('publish', st); }
    return st;
  });
}

// 失败时 rf.py 在标准输出给 {ok:false, code, error, detail}
function failure(out, what) {
  const told = parse(out.stdout, null);
  if (told && told.error) return { error: told.error, code: told.code, detail: told.detail || '', record: told.record || null };
  if (out.timedOut) return { error: what + uiText('等了太久，已经停下。检查网络后再试。', "Timed out and stopped. Check your network and retry."), code: 'timeout', detail: '' };
  return { error: lastLine(out.stderr), code: 'failed', detail: '' };
}

function run({ remote } = {}) {
  return exclusive(uiText('发布', "Publish"), async () => {
    const out = await rf(['publish', 'run', '--json', ...(remote ? ['--remote', remote] : [])], undefined, undefined, TIMEOUTS.run);
    const made = out.ok ? parse(out.stdout, null) : null;
    if (!made) { const bad = failure(out, uiText('推送', "Push")); await refresh(); return bad; }
    if (made.status) { latest = made.status; send('publish', made.status); }
    send('notice', Model.doneText(made));
    return { ok: true, ...made, status: undefined };
  });
}

function pull() {
  return exclusive(uiText('拉取', "Pull"), async () => {
    const out = await rf(['publish', 'pull', '--json'], undefined, undefined, TIMEOUTS.pull);
    const made = out.ok ? parse(out.stdout, null) : null;
    if (!made) return failure(out, uiText('拉取', "Pull"));
    await refresh();
    send('notice', made.message);
    return made;
  });
}

// 「让 AI 来合并」：先抓取一次，确认两边现在真的分叉了，再交出新聊天的第一句话（聊天由界面开，会话由 sessions.open 跑在项目目录里）
function aiMerge() {
  return exclusive(uiText('检查远端', "Check remote"), async () => {
    const st = await readStatus(['--fetch']);
    if (st.error) return { error: st.error };
    if (!st.error) { latest = st; send('publish', st); }
    if (st.fetch && !st.fetch.ok) return { error: uiText('先要抓取远端，但没有成功：', "Fetch failed: ") + st.fetch.error + (st.fetch.detail ? uiText('（git 说：', " (Git: ") + st.fetch.detail + '）' : '') };
    if (st.busy) return { error: uiText('项目目录里已经有一次', "The main checkout already has a ") + (st.busy === 'rebase' ? uiText('变基', "rebase") : uiText('合并', "merge")) + uiText('没做完，先把它做完。', " in progress. Finish it first."), merging: true, status: st };
    if (!(st.behind > 0 && st.ahead > 0)) return { error: uiText('现在两边没有分叉了，不用合并。看一眼发布对话框里的状态。', "The branches no longer diverge. Check the Publish dialog for the current status."), status: st };
    const sid = crypto.randomUUID();
    state.mergeChats.set(sid, { project: state.project });
    const dirty = (st.warnings || []).filter((w) => w.code === 'loose' || w.code === 'pending-facts');
    const say = mergingRemote({ remote: st.upstream.remote, branch: st.upstream.branch, current: st.branch, behind: st.behind, ahead: st.ahead,
      both: (st.divergence || {}).both, facts: (st.divergence || {}).facts_both, dirty: dirty.map((w) => w.text) });
    return { ok: true, sessionId: sid, title: uiText('合并 ', "Merge ") + st.upstream.ref, say };
  });
}

async function list() {
  const out = await rf(['publish', 'list', '--json'], undefined, undefined, TIMEOUTS.list);
  return parse(out.stdout, []);
}

// 项目目录的提交有变化（合并、事实提交、切分支、拉取……）、远端配置变了、抓取过：只看几个文件的修改时间，很便宜
function gitStamp(root) {
  let latestMs = 0;
  for (const name of ['HEAD', 'logs/HEAD', 'config', 'FETCH_HEAD', 'packed-refs']) {
    try { latestMs = Math.max(latestMs, fs.statSync(path.join(root, '.git', name)).mtimeMs); } catch (_) { /* 没有这个文件 */ }
  }
  return latestMs;
}
function watch(factsStamp) {
  if (!state.project || busy() || reading) return;
  const now = `${state.project}:${gitStamp(state.project)}:${factsStamp}`;
  if (lastStamp !== null && now === lastStamp) return;
  lastStamp = now;
  refresh();
}

module.exports = { exclusive, refresh, status, run, pull, list, watch, busy, gitStamp, aiMerge, mergeChatId };
