// 问题：使用中发现的毛病。具体的事都由 rf.py issue 做，这里管三件事：
// 1. 列表推给界面（带上哪些正在初判、哪些在排队）；
// 2. 初判在后台跑：同时最多两个，其余排队；正在初判时你又回复了，这次跑完马上带着回复再跑一次；
//    重启应用、会话里的 Agent 记了新问题，凡是还没判过的都会被找出来判一遍；没判出来的不自动重试，等你点「重试」；
// 3. 修复：开始修复、确认修好了（像需求一样排队合并，有冲突回到修复会话）、还有问题（把你的话交给修复会话）、转为需求；
// 4. 交给 AI 排查：记下这条问题交给了排查会话（会话本身由 sessions.js 开，只读），你在对话里说的话交给开着的排查会话，排查有了结论弹提醒。
const fs = require('fs');
const path = require('path');
const { uiText = (zh) => zh, rf, lastLine, parse } = require('./backend');
const { state, send } = require('./state');
const { pushReqs } = require('./requirements');
const attention = require('./attention');

const MAX_TRIAGE = 2;
const queue = []; // 等着初判的问题编号
const running = new Set(); // 正在初判的
let last = { issues: [], features: [] }; // 上一次读到的列表：用来发现「交回了」「判完了」，好提醒你
let loaded = false; // 这个项目的列表读过没有：第一次读到的不算变化

const runId = (fixId) => 'fix:' + fixId; // 修复会话在界面和提醒里的编号；「交给 AI 排查」的只读会话用 probe:问题编号
const probeId = (issueId) => 'probe:' + issueId;

async function listIssues() {
  const out = await rf(['issue', 'list', '--json']);
  const found = parse(out.stdout, null);
  if (!found || !Array.isArray(found.issues)) return { issues: [], features: [], error: out.ok ? '' : lastLine(out.stderr) };
  return found;
}

function decorate(found) {
  for (const issue of found.issues) {
    issue.triage = running.has(issue.id) ? 'running' : queue.includes(issue.id) ? 'queued' : '';
  }
  return found;
}

// 重读、推给界面、把还没判过的排进队，并看看有没有该提醒你的变化
async function pushIssues() {
  sameProject();
  const found = decorate(await listIssues());
  notice(found);
  last = found;
  send('issues', found);
  for (const issue of found.issues) if (issue.needs_triage && !running.has(issue.id) && !queue.includes(issue.id)) queue.push(issue.id);
  if (queue.length && running.size < MAX_TRIAGE) pump(); // 刚判完一条，空出位子：排着的接着判
  return found;
}

// 交回了、判完了：走现有的提醒（系统通知，点一下跳到这条问题）；你正看着它时不提醒
function notice(found) {
  const before = new Map(last.issues.map((i) => [i.id, i]));
  if (!loaded) { loaded = true; return; } // 刚打开：不把旧事重新提醒一遍
  for (const issue of found.issues) {
    const old = before.get(issue.id);
    if (!old) continue;
    if (old.state === 'fixing' && issue.state === 'review') attention.issue('issue:' + issue.id, uiText("问题 {0} · 修好了，等你确认", "Issue {0} · Fix ready for confirmation", issue.num), issue.title);
    const findings = (i) => i.thread.filter((e) => e.t === 'finding').length;
    if (issue.state === 'todo' && findings(issue) > findings(old)) {
      const f = issue.thread.filter((e) => e.t === 'finding').pop();
      const word = { bug: uiText('是问题', "Issue"), request: uiText('可转需求', "Candidate requirement"), usage: uiText('已有用法', "Existing workflow"), ask: uiText('待补充', "Needs details") }[f.kind] || '';
      attention.issue('issue:' + issue.id, uiText("问题 {0} · AI 排查有了结论：{1}", "Issue {0} · AI investigation: {1}", issue.num, word), f.text || issue.title);
    }
    const judged = (i) => i.thread.filter((e) => e.t === 'judge').length;
    if (issue.state === 'todo' && judged(issue) > judged(old)) {
      const j = issue.thread.filter((e) => e.t === 'judge').pop();
      const word = { bug: uiText('是问题', "Issue"), request: uiText('可转需求', "Candidate requirement"), usage: uiText('已有用法', "Existing workflow"), ask: uiText('待补充', "Needs details") }[j.kind] || '';
      attention.issue('issue:' + issue.id, uiText("问题 {0} · AI 初判：{1}", "Issue {0} · AI triage: {1}", issue.num, word), j.kind === 'ask' ? j.question : issue.title);
    }
  }
}

function enqueue(id, push = true) {
  if (!queue.includes(id) && !running.has(id)) queue.push(id);
  pump();
  if (push) send('issues', decorate(last));
}

function pump() {
  while (running.size < MAX_TRIAGE && queue.length) {
    const id = queue.shift();
    running.add(id);
    const root = state.project;
    rf(['issue', 'triage', id, '--json'], root).then((out) => {
      running.delete(id);
      if (!out.ok && root === state.project) send('notice', uiText("问题 {0} 没判出来：{1}", "Couldn't assess issue {0}: {1}", id.replace('I-', '#'), lastLine(out.stderr)));
      if (root === state.project) pushIssues(); else pump();
    });
  }
  send('issues', decorate(last));
}

// 切换项目以后：上一个项目排着的不再跑，列表和「看到过什么」从头来
let seenRoot = null;
function sameProject() {
  if (seenRoot === state.project) return;
  seenRoot = state.project;
  queue.length = 0; last = { issues: [], features: [] }; loaded = false; stamp = null;
  clearTimeout(reviewTimer);
  review = { root: null, status: { state: 'idle' } };
  if (seenRoot) reviewStatus();
}

async function call(args) {
  const out = await rf(['issue', ...args, '--json']);
  if (!out.ok) return { error: lastLine(out.stderr) };
  const made = parse(out.stdout, null);
  await pushIssues();
  return made || { error: uiText('没有读懂结果。', "Couldn't read the result.") };
}

// AI 复查（问题页的「刷新」）：rf.py issue review 另起一个后台进程跑，进度和结果在它的任务文件里，关掉窗口、关掉应用、切换项目都不打断它。
// 这里只读进度推给界面（issues:review）：在跑的时候隔一会儿读一次；切换项目以后读新项目自己的，旧项目的结果不会显示过来。
let review = { root: null, status: { state: 'idle' } };
let reviewTimer = null;

async function readReview(root) {
  const out = await rf(['issue', 'review-status', '--json'], root);
  return (out.ok && parse(out.stdout, null)) || { state: 'idle' };
}

function pushReview(status, root) {
  if (root !== state.project) return;
  const was = review.root === root ? review.status : null;
  review = { root, status };
  send('issues:review', status);
  clearTimeout(reviewTimer);
  if (status.state === 'running') reviewTimer = setTimeout(pollReview, 1500);
  if (was && was.state === 'running' && status.state !== 'running') pushIssues(); // 查完了：每条问题的对话里多了复查结论
}

async function pollReview() {
  const root = state.project;
  if (root) pushReview(await readReview(root), root);
}

// 界面打开问题页、切换项目以后要知道这个项目现在有没有复查
async function reviewStatus() {
  const root = state.project;
  const status = await readReview(root);
  pushReview(status, root);
  return status;
}

// 点「刷新」：正在复查、或有结果还没处理时，rf.py 只把那一次原样交回来（界面打开进度或结果），不会再开一次
async function reviewIssues() {
  const root = state.project;
  const out = await rf(['issue', 'review', '--json'], root);
  if (root !== state.project) return { error: uiText('项目已切换。', "The project changed.") };
  if (!out.ok) return { error: lastLine(out.stderr) };
  const job = parse(out.stdout, null);
  if (!job) return { error: uiText('没有读懂复查的结果。', "Couldn't read the review results.") };
  if (job.state !== 'empty') pushReview(job, root);
  return job;
}

// 关掉结果、不处理：问题的状态都不变
async function discardReview() {
  const root = state.project;
  const out = await rf(['issue', 'review-discard', '--json'], root);
  if (!out.ok) return { error: lastLine(out.stderr) };
  pushReview({ state: 'idle' }, root);
  return { state: 'idle' };
}

async function applyIssueReview({ review: id, selected }) {
  const root = state.project;
  if (!root || !review.status || review.root !== root || review.status.id !== id) return { error: uiText('这次复查的结果已经不在了，请重新刷新。', "This review has expired. Refresh to start another.") };
  const out = await rf(['issue', 'review-apply', '--json'], root, undefined, 0, JSON.stringify({ review: id, selected }));
  if (root !== state.project) return { error: uiText('项目已切换。', "The project changed.") };
  if (!out.ok) {
    await pushIssues();
    return { error: lastLine(out.stderr) };
  }
  const result = parse(out.stdout, null);
  if (!result || !Array.isArray(result.applied)) return { error: uiText('没有读懂处理结果，请重新刷新。', "Couldn't read the result. Refresh and try again.") };
  for (const issueId of result.applied) require('./sessions').close(probeId(issueId));
  pushReview({ state: 'idle' }, root);
  await pushIssues();
  return result;
}

const newIssue = ({ title, desc, images }) => call(['new', String(title || ''), '--desc', String(desc || ''), '--source', '你', ...imageArgs(images)]);
const imageArgs = (images) => (Array.isArray(images) && images.length ? ['--image', ...images.map(String)] : []);

// 回复：待处理的问题会重新初判（正在判就等这次判完马上再判，见 pushIssues）；处理中的话顺手交给修复会话；
// 排查会话开着的时候，话交给它，不再做只看产品事实的初判（它读过代码，比初判清楚）
async function reply({ id, text, images }) {
  const toProbe = state.runs.has(probeId(String(id)));
  const made = await call(['reply', String(id), String(text || ''), ...(toProbe ? ['--to-probe'] : []), ...imageArgs(images)]);
  if (made.error) return made;
  const said = (images && images.length ? uiText(" 附图：{0}", " Screenshots: {0}", images.join(' ')) : '');
  if (made.state === 'fixing' && made.fix && String(text || '').trim()) {
    tell(made.fix.id, uiText("我在问题 {0} 里补充了一句：{1}", "I added a reply to issue {0}: {1}", made.num, String(text).trim()) + said);
  }
  if (toProbe && made.state === 'todo') {
    require('./inbox').post({ to: probeId(made.id), from: 'issues', open: false,
      text: uiText("用户在问题 {0} 里补充了一句：{1}", "The user added a reply to issue {0}: {1}", made.num, String(text || '').trim() || uiText('（见截图）', " (see screenshot)")) + said });
  }
  return made;
}

// 交给 AI 排查：记下这条问题有了一个排查会话（只有待处理的能交）。界面接着打开 probe:问题编号 这个终端
const probe = (id) => call(['probe', String(id)]);

// 撤回确认：同一次修复还没合并的时候，收回自己的确认
const unconfirm = (id) => call(['unconfirm', String(id)]);
async function setIssue({ id, action, value }) {
  const finishing = action === 'finish-resolved' || action === 'finish-closed';
  const root = state.project;
  // 结束的是这次修复里最后一条：修复跟着结束，rf.py 会收掉它的工作目录，所以先关掉在那里面跑的修复会话
  if (finishing) {
    const issue = (await listIssues()).issues.find((i) => i.id === String(id) || String(i.num) === String(id) || String(i.num) === '#' + String(id).replace(/^#/, ''));
    const fix = issue && issue.fix;
    const all = fix && Array.isArray(fix.issues) ? fix.issues : null; // 不知道这次修复有哪几条时不提前关，照旧等结果
    const left = all ? all.filter((x) => x !== issue.id && !(fix.finished || []).includes(x)) : [null];
    if (fix && fix.state === 'fixing' && !left.length) require('./sessions').close(runId(fix.id));
  }
  const made = await call(['set', String(id), String(action), finishing ? '--reason=' + String(value || '') : String(value || '')]);
  if (finishing && root === state.project && made.fix && made.fix.state === 'ended') {
    require('./sessions').close(runId(made.fix.id));
  }
  return made;
}
const retry = (id) => { enqueue(String(id)); return { ok: true }; };

// 开始修复：开分支和工作目录（rf.py issue fix），界面接着打开修复会话。一条或几条（一起修）共用一个修复
async function startFix(ids, choice) {
  const list = (Array.isArray(ids) ? ids : [ids]).map(String);
  let flags = [];
  if (choice != null) {
    try {
      flags = require('../renderer/scripts/requirement-sessions').fixArgs(choice, require('./settings').catalogs());
    } catch (error) { return { error: error.message }; }
  }
  const out = await rf(['issue', 'fix', ...list, ...flags, '--json']);
  await pushIssues();
  if (!out.ok) return { error: lastLine(out.stderr) };
  for (const id of list) require('./sessions').close(probeId(id)); // 开始修复了：排查会话收起来（它的结论已经在对话里，修复会话的开场白会带上）
  const made = parse(out.stdout, { error: uiText('没有读懂结果。', "Couldn't read the result.") });
  // 修复会话在后台无界面运行（headless.js）：你点开它时先看到它做到哪了，要你批准或在问你时右下角会提醒
  if (made && made.id && !made.error) await require('./sessions').open({ id: runId(made.id), headless: true });
  return made;
}

// 确认修好了：和需求的确认完成一样排进合并队伍（同一时间只合并一个），有冲突回到修复会话里解决
async function confirm(id, closeRun) {
  const issue = last.issues.find((i) => i.id === id) || (await pushIssues()).issues.find((i) => i.id === id);
  if (!issue || !issue.fix) return { error: uiText('没有找到这条问题的修复。', "Couldn't find the fix for this issue.") };
  // 一起修的几条：每条单独确认，同批还有没确认的就只记下这一条的确认，不合并，也不用排队；最后一条确认了才排进合并队伍
  if ((issue.siblings || []).some((s) => !s.confirmed)) {
    const out = await rf(['issue', 'confirm', id, '--json']);
    await pushIssues();
    if (!out.ok) return { error: lastLine(out.stderr) };
    return parse(out.stdout, { error: uiText('没有读懂结果。', "Couldn't read the result.") });
  }
  const result = await require('./merge').enqueue(runId(issue.fix.id), closeRun, { issue: id, kind: 'fix' });
  await pushIssues();
  return result;
}

// 还有问题：退回，把你的话交给同一个修复会话（开着就直接说，没开就打开时作为第一句话）
async function reject({ id, text, images }) {
  const made = await call(['reject', String(id), String(text || ''), ...imageArgs(images)]);
  if (made.error) return made;
  const shots = (made.thread[made.thread.length - 2] || {}).shots || [];
  tell(made.fix.id, uiText("我看过了，问题 {0}「{1}」还有问题：{2}", "Issue {0} “{1}” is still unresolved: {2}", made.num, made.title, String(text).trim()) + (shots.length ? uiText("（截图：{0}）", " (screenshot: {0})", shots.join(' ')) : '') + uiText('\n先按我说的复现，再修，自己验证过、提交以后，重新用 rf.py issue handback 交回。', "\nReproduce the problem as described, fix and verify it, then commit and hand it back with rf.py issue handback."));
  return made;
}

// 往修复会话里说一句：经收件箱（inbox.js），开着的等它能收时再送，没开就让界面打开它、这句随开场白交过去
function tell(fixId, text) {
  require('./inbox').post({ to: runId(fixId), from: 'issues', text });
}

// 转为需求：新建立项中的需求，带上描述、截图和对话
async function toReq(id) {
  const made = await call(['to-req', String(id)]);
  if (!made.error) pushReqs();
  return made;
}

// 需求转问题：立项中的需求记成一条问题（问题在项目主目录里），需求那边记下链接
async function fromReq({ id, feature }) {
  const made = await call(['from-req', String(id), ...(feature ? ['--feature', String(feature)] : [])]);
  if (!made.error) pushReqs();
  return made;
}

// 文件监视（watcher.js）：主目录 .relayforge/issues/ 一变就重读，Agent 记的新问题也就这样被发现、排进初判
let stamp = null;
function watch() {
  if (!state.project) return;
  sameProject();
  let latest = 0;
  const dir = path.join(state.project, '.relayforge', 'issues');
  for (const sub of ['', 'fixes', 'probes']) {
    try { for (const name of fs.readdirSync(path.join(dir, sub))) latest = Math.max(latest, fs.statSync(path.join(dir, sub, name)).mtimeMs); } catch (_) { /* 还没有问题 */ }
  }
  if (stamp !== null && latest !== stamp) pushIssues();
  stamp = latest;
}

module.exports = { fromReq, listIssues: async () => decorate(await listIssues()), pushIssues, reviewIssues, reviewStatus, discardReview, applyIssueReview, newIssue, reply, setIssue, retry, startFix, confirm, unconfirm, reject, toReq, probe, watch, runId, probeId, enqueue };
