// 需求：列表、新建、确认立项、确认完成、放弃。具体的事都由 rf.py req 做，这里只转一道并通知界面。
const fs = require('fs');
const path = require('path');
const { uiText = (zh) => zh, rf, lastLine, parse } = require('./backend');
const { state, send } = require('./state');
const { createArgs } = require('../renderer/scripts/requirement-sessions');

async function listReqs(root) {
  const out = await rf(['req', 'list', '--json'], root);
  const found = parse(out.stdout, []);
  return Array.isArray(found) ? found : [];
}
// 每次推给界面时也看一眼测试阶段：刚进入测试中的开出测试会话，刚交回的结论交给开发会话或提醒你（testing.js）
function withSubagent(project, items) {
  if (!project) return items;
  return items.map((req) => ['draft', 'open'].includes(req.state) ? {
    ...req, subagent: require('./subagents').view(project, req, state.runs?.get(req.id)?.agent),
  } : req);
}

async function pushReqs() {
  const project = state.project;
  const items = await listReqs(project);
  if (state.project !== project) return;
  const found = withSubagent(project, items);
  send('reqs', found);
  try { require('./conflicts').schedule(); } catch (_) { /* 冲突过一会儿再算 */ } // 需求有变化（包括合并以后主分支前进了）：重算需求之间的冲突
  try { Promise.resolve(require('./testing').follow(found)).catch(() => {}); } catch (_) { /* 只是没跟上测试阶段，下次再看 */ }
}

async function listSessions() {
  const previous = parse((await rf(['sessions', '--json'])).stdout, []);
  const reqs = await listReqs();
  const issues = (await require('./issues').listIssues()).issues || [];
  const taken = new Set([...reqs.flatMap((r) => r.sessions), ...issues.flatMap((i) => (i.fix && i.fix.sessions) || [])]); // 修复会话在问题页里
  const chats = require('./codex-sessions').chats(state.project).filter((s) => !taken.has(s.session_id));
  const examples = require('./demo').chats(state.project);
  const ids = new Set([...previous, ...chats].map((s) => s.session_id));
  return [...previous, ...chats, ...examples.filter((s) => !ids.has(s.session_id))].filter((s) => !taken.has(s.session_id)).sort((a, b) => b.updated.localeCompare(a.updated));
}

async function newReq(title, choices, catalogs) {
  let flags;
  try { flags = choices == null ? [] : createArgs(choices, catalogs); } catch (error) { return { error: error.message }; }
  const out = await rf(['req', 'new', String(title || ''), '--json', ...flags]);
  if (!out.ok) return { error: lastLine(out.stderr) };
  pushReqs();
  return parse(out.stdout, { error: uiText('没有读懂新建的结果。', "Couldn't read the new requirement.") });
}

// 测试用例：你在面板上直接改（加、改、删、归类，rf.py req case-save，不受限），同意或不同意会话提的用例改动（case-ok / case-no）。
// 都只由你在应用里点（rf.py 校验应用的标记）。不同意时把这句交回提出它的会话：开发会话的发进开发会话，测试会话的发进测试会话。
const REQ_ID = /^R-[\w-]+$/;
async function saveCase(id, payload) {
  if (!REQ_ID.test(String(id || ''))) return { error: uiText('要给需求编号。', "Provide a requirement ID.") };
  const out = await rf(['req', 'case-save', String(id), '--file', '-', '--json'], undefined, undefined, 60000, JSON.stringify(payload || {}));
  if (!out.ok) return { error: lastLine(out.stderr) };
  pushReqs();
  return parse(out.stdout, { error: uiText('没有读懂保存的结果。', "Couldn't read the saved requirement.") });
}
async function decideCase(id, k, ok, change) {
  if (!REQ_ID.test(String(id || '')) || !/^\d+$/.test(String(k))) return { error: uiText('要给需求编号和改动编号。', "Provide the requirement and change IDs.") };
  const out = await rf(['req', ok ? 'case-ok' : 'case-no', String(id), String(k), '--json']);
  if (!out.ok) return { error: lastLine(out.stderr) };
  pushReqs();
  const made = parse(out.stdout, { error: uiText('没有读懂结果。', "Couldn't read the result.") });
  const c = (made && made.change) || change;
  if (!ok && c && (c.actor === 'develop' || c.actor === 'test')) {
    const text = uiText("【用例改动 {0} 我没有同意】你提的「{1}」{2}（理由：{3}）我没有同意，用例保持原样。", "[Case change {0} declined] Your proposal “{1}” {2} (reason: {3}) was declined. Keep the original case.", c.k, c.target, ({ edit: uiText('修改', "edit"), delete: uiText('删除', "delete"), add: uiText('新增', "add"), classify: uiText('归类', "classify") })[c.op] || '', c.why)
      + (c.actor === 'develop' ? uiText('请按现在的用例来做，让它们通过。', "Use the current cases and make them pass.") : uiText('请按现在的用例对接和执行。', "Use the current cases to set up and run the tests."));
    try { require('./testsets').tell(c.actor === 'test' ? 'test:' + id : id, text); } catch (_) { /* 会话没开着：下次打开时它会从需求文件里看到 */ }
  }
  return made;
}

// closeRun(id)：关掉这个需求开着的会话，关了返回 true。会话归 sessions.js 管，由 ipc.js 传进来。
async function startReq(id, closeRun) {
  // 确认立项：程序过门（至少一条验收条件），然后关掉立项会话；界面重新打开这个需求时会开出开发会话。
  const project = state.project;
  try { await captureSubagentDecision(project, id); } catch (error) { return { error: error.message }; }
  if (project !== state.project) return { error: '项目已切换，请重新打开这个需求。' };
  const out = await rf(['req', 'start', String(id), '--json']);
  if (!out.ok) return { error: lastLine(out.stderr) };
  closeRun(id);
  pushReqs();
  return parse(out.stdout, { error: uiText('没有读懂立项的结果。', "Couldn't read the planning approval result.") });
}
async function captureSubagentDecision(project, id) {
  const req = (await listReqs(project)).find((item) => item.id === id);
  if (req?.state === 'draft' && req.intake?.length) {
    const { findTranscript } = require('./sessions');
    return require('./subagents').readIntake(project, req, findTranscript);
  }
}

async function doneReq(id, closeRun) {
  await require('./services').stopScope(String(id)); // 工作目录要收掉了：先停掉从它启动的验收服务
  const out = await rf(['req', 'done', String(id), '--json']);
  if (!out.ok) return { error: lastLine(out.stderr) };
  closeRun(id); // 工作目录已经收掉，会话也跟着结束
  pushReqs();
  return parse(out.stdout, { error: uiText('没有读懂确认的结果。', "Couldn't read the completion result.") });
}
// 取消立项或开发中的需求：关闭会话，保存需求记录并收起工作目录，分支保留供恢复。
async function dropReq(id, closeRun) {
  return setReq(id, 'dropped', closeRun);
}
// 手动改状态（右击菜单、拖动看板卡片）：规则由 rf.py req set 把关。
// 离开立项中要关掉立项会话（重新打开时开出开发会话）；改到已完成、已取消，工作目录会收掉，会话也跟着关。
// to 是 'skip'：面板上的「跳过测试，直接验收」（rf.py req skip-test，进行中或测试中都能点；程序校验是应用点的）
async function setReq(id, to, closeRun) {
  const req = (await listReqs()).find((r) => r.id === id);
  if (!req) return { error: uiText('没有找到这个需求。', "Requirement not found.") };
  if (to === 'skip') {
    if (req.moves && req.moves.check) return { error: req.moves.check };
    const out = await rf(['req', 'skip-test', String(id), '--json']);
    pushReqs();
    if (!out.ok) return { error: lastLine(out.stderr) };
    return parse(out.stdout, { error: uiText('没有读懂跳过测试的结果。', "Couldn't verify that testing was skipped.") });
  }
  if (req.moves && req.moves[to]) return { error: req.moves[to] };
  if (to === 'dropped') {
    await require('./services').stopScope(String(id), undefined, uiText('放弃需求前停掉', "Stopped before cancelling the requirement")); // 放弃：工作目录要收掉，先停掉从它启动的验收服务
    for (const sid of req.sessions) closeRun('session:' + sid);
    if (closeRun(id)) await new Promise((done) => setTimeout(done, 800)); // 等会话退出，再收它的工作目录
  }
  if (to === 'done') { // 改到已完成就是确认完成：一样排队合并，有冲突回到会话里解决
    if (req.state === 'draft') {
      const started = await startReq(id, closeRun);
      if (started.error) return started;
    }
    return require('./merge').enqueue(id, closeRun);
  }
  if (req.state === 'draft' && to !== 'talk' && to !== 'dropped') {
    const project = state.project;
    try { await captureSubagentDecision(project, id); } catch (error) { return { error: error.message }; }
    if (state.project !== project) return { error: '项目已切换，请重新打开这个需求。' };
  }
  const out = await rf(['req', 'set', String(id), String(to), '--json']);
  // 立项中直接改到已完成：可能立项成功、合并失败，所以按改完以后真实的状态决定关不关会话
  const after = out.ok ? null : (await listReqs()).find((r) => r.id === id);
  if (out.ok ? to === 'done' || req.state === 'draft' : after && after.state !== req.state) closeRun(id);
  pushReqs();
  if (!out.ok) return { error: lastLine(out.stderr) };
  return parse(out.stdout, { error: uiText('没有读懂改状态的结果。', "Couldn't read the status change result.") });
}

async function restoreReq(id) {
  const out = await rf(['req', 'restore', String(id), '--json']);
  if (!out.ok) return { error: lastLine(out.stderr) };
  pushReqs();
  return parse(out.stdout, { error: uiText('没有读懂恢复的结果。', "Couldn't read the restore result.") });
}

// 前置需求：你在需求面板里加上或去掉（只由你触发，rf.py 自己校验）。不能依赖自己、已完成或已取消的需求，不能形成环：rf.py 说原因
async function dependReq(id, other, remove) {
  const out = await rf(['req', 'depend', String(id), String(other), ...(remove ? ['--remove'] : []), '--json']);
  if (!out.ok) return { error: lastLine(out.stderr) };
  pushReqs();
  return parse(out.stdout, { error: uiText('没有读懂设置前置需求的结果。', "Couldn't read the dependency update.") });
}

// 已取消的需求：保留它的分支不被清理，或者取消保留（rf.py req keep，只由用户触发）
async function keepReq(id, keep) {
  const out = await rf(['req', 'keep', String(id), ...(keep ? [] : ['--off']), '--json']);
  if (!out.ok) return { error: lastLine(out.stderr) };
  pushReqs();
  return parse(out.stdout, { error: uiText('没有读懂保留分支的结果。', "Couldn't verify the branch retention change.") });
}

// 大图：rf.py render 写出页面，界面载入这个文件
async function renderMap() {
  const out = await rf(['render']);
  const file = path.join(state.project, '.relayforge', 'site', 'index.html');
  return out.ok && fs.existsSync(file) ? { file } : { error: lastLine(out.stderr) };
}

// 用户旅程设为主干（合并前必跑它用到的功能点的测试集，见 backend/relayforge/testsets/gate.py）：只由你在大图里点
async function setTrunk(id, on) {
  if (!/^J-\d+$/i.test(id)) return { error: uiText('没有这条用户旅程。', "No such user journey.") };
  const out = await rf(['map', 'trunk', id, ...(on ? [] : ['--off']), '--json']);
  return out.ok ? parse(out.stdout, {}) : { error: lastLine(out.stderr) };
}

module.exports = { withSubagent, captureSubagentDecision, listReqs, pushReqs, listSessions, newReq, saveCase, decideCase, startReq, doneReq, dropReq, setReq, restoreReq, dependReq, keepReq, renderMap, setTrunk };
