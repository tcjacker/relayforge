// 每 1.5 秒看一眼：钩子的新事件、事实文件的变化、单独打开的设计稿、会话是不是停下来了。
const { uiText } = require('./locale');
const fs = require('fs');
const path = require('path');
const { AUTOSYNC, IDLE_MS } = require('./config');
const { state } = require('./state');
const { pushReqs } = require('./requirements');
const attention = require('./attention');
const designs = require('./designs');
const { findTranscript, pushState, sync } = require('./sessions');
const splits = require('./splits');
const { readTurn } = require('./codex-turns');
const stuck = require('./stuck');
const inbox = require('./inbox');

// 事实文件一变就重读：会话记了进展、改了需求，界面马上跟上。事实都在项目主目录里
function stamp(root) {
  let latest = 0;
  for (const dir of ['sessions', 'requirements', 'features', 'modules', 'journeys', 'decisions', 'product', 'designs', 'history']) {
    const folder = path.join(root, '.relayforge', dir);
    try { for (const name of fs.readdirSync(folder)) latest = Math.max(latest, fs.statSync(path.join(folder, name)).mtimeMs); } catch (_) { /* 这一类还没有文件 */ }
  }
  return latest;
}

let lastStamp = null;
function tick() {
  attention.readEvents();
  if (state.project) inbox.absorbDelivered(state.project);
  attention.sweepIfDue();
  // 事实只在项目主目录里有一份（会话跑在哪个工作目录都一样），所以只看主目录这一处：变了就重读所有开着的会话和需求列表
  const now = state.project ? stamp(state.project) : 0;
  if (lastStamp !== null && now !== lastStamp) { for (const run of state.runs.values()) pushState(run); pushReqs(); }
  lastStamp = now;
  designs.reloadChanged();
  require('./issues').watch();
  require('./testsets').watch(); // .relayforge/tests/ 变了：测试页、需求面板、开着的查看器重读
  require('./envs').watch(); // 测试环境的设置或本机的保密值、建议变了：测试环境页和各处的选择框重读
  require('./services').watch(); // 人工验收的服务：启动后代码改过的标出来（只标记，不重启）
  const approvals = require('./hitl').tick(); // 同一批批准请求用来画卡片和暂停后台会话计时
  require('./publish').watch(now); // 顶栏计数跟随 Git 状态和需求进度刷新，不抓取远端
  for (const run of state.runs.values()) {
    if (run.headless) {
      stuck.watchHeadless(run, approvals.some((a) => a.run === run.id));
      if (state.runs.get(run.id) !== run) continue;
    }
    run.transcript = run.transcript || findTranscript(run.sessionId);
    let size = null;
    try { if (run.transcript) size = fs.statSync(run.transcript).size; } catch (_) { /* 聊天记录可能还没落盘 */ }
    if (size !== null && run.splitEnabled && size !== run.splitSeen && !run.splitBusy) {
      run.splitSeen = size;
      splits.review(run);
    }
    if (size !== null && run.kind === 'intake' && size !== run.subagentSeen && !run.subagentBusy) {
      const project = state.project;
      run.subagentSeen = size;
      run.subagentBusy = true;
      require('./requirements').captureSubagentDecision(project, run.owner)
        .then((changed) => { if (changed && state.project === project) pushReqs(); }).catch(() => { run.subagentSeen = null; })
        .finally(() => { run.subagentBusy = false; });
    }
    const changed = size !== null && size !== run.seen;
    if (changed && run.agent === 'codex' && !run.hooked && !run.steer) { // 钩子没在报状态（还没信任，或者旧版 Codex）：从聊天记录读
      const progress = readTurn(run, size);
      if (progress) attention.mergeProgress(run.id, progress);
    }
    if (!run.headless) stuck.watchInteractive(run, attention.phaseStateOf(run.id), size, attention.remind);
    if (!changed) continue;
    inbox.markSeen(run);
    run.seen = size;
    clearTimeout(run.idleTimer);
    if (AUTOSYNC || run.agent === 'codex') run.idleTimer = setTimeout(() => sync(run, uiText('会话停下来了，正在整理进展…', "The session paused. Syncing progress…")), IDLE_MS);
  }
  inbox.watchDelivery(findTranscript);
}

function start() { return setInterval(tick, 1500); }

module.exports = { start };
