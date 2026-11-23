// 测试阶段（见 FORMAT.md 的「需求」）：需求进入「测试中」时开出它的测试会话并发开场白；测试会话交回结论以后，「有问题」把问题清单
// 作为一段话发进开发会话，「可以验收了」提醒你。做没做过记在需求文件头（rf.py req test-kicked / test-told，要应用的标记），重启应用不会重发。
// 应用开着时看到需求进了测试中就开；应用没开着时不在启动时一下子开一堆，等你下次点开这个需求再开（kick）。
const { uiText = (zh) => zh, rf } = require('./backend');
const { state, send } = require('./state');
const { retesting, testProblems } = require('./prompts');

const seen = new Map(); // 项目 -> 上一次看到的 { 需求编号: 阶段|轮次|结论轮次 }：第一次看到的不算「刚进入」
const busy = new Set(); // 项目|需求编号|kick|轮次：正在做，不重复

// 让测试会话开始这一轮：第一次是开场白（sessions.js 打开 test:需求编号 时给），以后是「再测一轮」。会话开着就直接输进去，没开就让界面在后台打开
async function kick(r) {
  const t = r.testing;
  if (!t || !t.needs_kick || r.state !== 'open' || !r.worktree) return false;
  if (require('./demo').info(state.project)) return false; // 演示项目不调用 AI
  const key = [state.project, r.id, 'kick', t.round].join('|');
  if (busy.has(key)) return false;
  busy.add(key);
  const marked = await rf(['req', 'test-kicked', r.id, String(t.round)]);
  if (!marked.ok) { busy.delete(key); return false; }
  const runId = 'test:' + r.id;
  const say = t.sessions.length ? retesting(r) : undefined; // 第一次：开场白里已经讲清楚了
  // 再测一轮：经收件箱，等测试会话能收时再送（没开着就让界面打开它）；第一次的话在开场白里，只要把它打开
  if (say) require('./inbox').post({ to: runId, from: 'testing', key: `kick:${r.id}:${t.round}`, text: say });
  else if (!state.runs.get(runId)) send('tests:say', { id: runId, test: true });
  send('testing:kicked', { id: r.id, round: t.round });
  return true;
}

// 测试会话交回的结论：有问题交给开发会话（它开着就输进去，没开就让界面打开它、这段话作为第一句）；可以验收了提醒你
async function tell(r) {
  const t = r.testing;
  if (!t || !t.to_tell) return false;
  const key = [state.project, r.id, 'tell', t.to_tell].join('|');
  if (busy.has(key)) return false;
  busy.add(key);
  const marked = await rf(['req', 'test-told', r.id, String(t.to_tell)]);
  if (!marked.ok) { busy.delete(key); return false; }
  const last = t.last || {};
  if (last.verdict === 'problems') {
    if (r.state === 'open' && r.worktree) require('./testsets').tell(r.id, testProblems(r), { from: 'testing', key: `tell:${r.id}:${t.to_tell}` });
    require('./attention').remind(r.id, uiText("{0} · 测试有问题，退回进行中", "{0} · Testing found issues. Returned to development.", r.title), uiText("第 {0} 轮测试交回 {1} 条问题，已经交给开发会话。", "Round {0} found {1} issues and sent them to the development session.", last.n, last.problems.length));
  } else if (last.verdict === 'ok') {
    require('./attention').remind(r.id, uiText("{0} · 可以验收了", "{0} · Ready for acceptance", r.title), last.summary || uiText('测试会话交回：可以验收了。', "The test session marked this ready for acceptance."));
  }
  return true;
}

// 每次读到需求列表时调用（requirements.js 的 pushReqs）：这一次才进入测试中、或才交回结论的，马上处理
async function follow(reqs) {
  if (!state.project || !Array.isArray(reqs)) return;
  const before = seen.get(state.project);
  const now = {};
  for (const r of reqs) now[r.id] = r.testing ? [r.stage, r.testing.round, r.testing.to_tell].join('|') : r.stage;
  seen.set(state.project, now);
  if (!before) return; // 刚打开应用（或刚切到这个项目）：不一下子开一堆会话，等你点开需求时再说（open）
  for (const r of reqs) {
    if (before[r.id] === now[r.id]) continue;
    if (r.testing && r.testing.needs_kick) await kick(r);
    if (r.testing && r.testing.to_tell) await tell(r);
  }
}

// 你点开一个需求时（界面调用）：该开的测试会话、该交的结论，这时候补上
async function open(id) {
  const r = (await require('./requirements').listReqs()).find((x) => x.id === id);
  if (!r || !r.testing) return { ok: false };
  const kicked = await kick(r);
  const told = await tell(r);
  return { ok: true, kicked, told };
}

module.exports = { follow, kick, tell, open };
