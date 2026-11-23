// 知识保鲜（backend/relayforge/facts/fresh.py，设计见 docs/design/知识保鲜-设计.md）：大图上哪些功能点可能过时、点「核对」开一个
// 后台的只读核对会话（check:功能点编号，sessions.js），它交回的建议由你在对话框里逐条采纳或忽略，处理完点「核对完」。
// 只在你点的时候核对（不在后台自动跑：要花模型的钱）。
const { uiText = (zh) => zh, rf, parse, lastLine } = require('./backend');
const { state, send } = require('./state');

const ID = /^RF-\d+$/;
const ok = (out) => (out.ok ? parse(out.stdout, null) : { error: lastLine(out.stderr) });

// 大图要的：{ 功能点: { stale, unmapped, commits: [{short, subject, owner}], checking, pending, verdict } }
async function overview() {
  if (!state.project) return {};
  const [drift, shown] = await Promise.all([rf(['fresh', 'drift', '--json']), rf(['fresh', 'show', '--json'])]);
  const rows = drift.ok ? parse(drift.stdout, []) : [];
  const waiting = shown.ok ? parse(shown.stdout, []) : [];
  const out = {};
  for (const r of rows || []) {
    out[r.id] = { stale: !!r.stale, unmapped: !!r.unmapped, checking: state.runs.has('check:' + r.id),
      commits: (r.commits || []).slice(0, 8).map((c) => ({ short: c.short, subject: c.subject, owner: c.owner })), more: Math.max(0, (r.commits || []).length - 8) };
  }
  for (const p of waiting || []) {
    out[p.feature] = Object.assign(out[p.feature] || {}, { pending: p.items.filter((i) => i.state === 'pending').length, verdict: p.verdict, proposed: true });
  }
  return out;
}

async function check(id) {
  if (!ID.test(String(id || ''))) return { error: uiText('没有这个功能点。', 'No such feature.') };
  if (state.runs.has('check:' + id)) return { running: true };
  await require('./sessions').open({ id: 'check:' + id, headless: true });
  send('fresh:changed', { id });
  return { ok: true };
}

const show = (id) => (ID.test(String(id || '')) ? rf(['fresh', 'show', id, '--json']).then(ok) : Promise.resolve(null));
const act = (action) => async (id, ks) => {
  if (!ID.test(String(id || ''))) return { error: uiText('没有这个功能点。', 'No such feature.') };
  const keys = (Array.isArray(ks) ? ks : []).map(Number).filter((k) => Number.isInteger(k) && k > 0).map(String);
  const out = ok(await rf(['fresh', action, id, ...keys, '--json']));
  send('fresh:changed', { id });
  return out;
};

// 核对会话这一轮说完（或退出）：大图和对话框重读
function settled(id, kind) { if (String(id).startsWith('check:') && kind !== 'running') send('fresh:changed', { id: String(id).slice(6) }); }

module.exports = { overview, check, show, adopt: act('adopt'), ignore: act('ignore'), done: act('done'), settled };
