// 测试集：需求面板的两块、测试页的测试集管理、测试集查看器、确认完成前的检查。具体的事都由 rf.py test 做（只读的 list、show，
// 运行 run，以及只由你点了按钮才调用的 approve、reject、stale、revive、move、save、seen），这里只是调用它、把结果交给界面。
// 运行由程序在本机跑测试代码，不经过模型。「让 AI 改」把你的话交给一个会话：进行中的需求（或修复）发进它的开发会话，
// 没有需求时新开一个日常聊天，前面加上程序写好的说明（见 prompts.js 的 testEditing）。
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { uiText = (zh) => zh, rf, lastLine, parse } = require('./backend');
const { state, send } = require('./state');
const { testEditing } = require('./prompts');
const Model = require('../renderer/scripts/tests-model');

const TIMEOUTS = { read: 30000, run: 15 * 60 * 1000, act: 60000 };
const handed = new Map(); // 项目|测试集编号 -> { kind: 'req'|'fix'|'chat', run, title, at }：「让 AI 改」交给了哪个会话（只记在内存里）

const ID = /^TS-\d+$/i;
const OWNER = /^(R-[\w-]+|F-\d+)$/;
const scopeArgs = ({ req, fix }) => (req && OWNER.test(req) ? ['--req', req] : fix && OWNER.test(fix) ? ['--fix', fix] : []);
const failed = (out, what) => ({ error: out.timedOut ? what + uiText('等了太久，已经停下。', "Timed out and stopped.") : lastLine(out.stderr) });

async function call(args, timeout = TIMEOUTS.read, input) {
  const out = await rf(['test', ...args, '--json'], undefined, undefined, timeout, input);
  const made = out.ok ? parse(out.stdout, null) : null;
  return made || failed(out, uiText('测试集', "Test set"));
}

// 列表：测试页（全部，按主线）或者一个需求（它眼里的数据：它新建的、挂在它名下的、它动到的功能点下已有的）
function list({ req, fix } = {}) { return call(['list', ...scopeArgs({ req, fix })]); }

// 查看器：一个测试集的全部数据和上次的结果；带上「让 AI 改」交给了哪个会话
async function show({ id, req, fix } = {}) {
  if (!ID.test(String(id || ''))) return { error: uiText('要给测试集编号。', "Provide a test set ID.") };
  const made = await call(['show', String(id), ...scopeArgs({ req, fix })]);
  if (!made.error) made.handed = handed.get(state.project + '|' + made.id) || null;
  return made;
}

// 运行：一个或几个测试集、一个需求的全部、或整套（主线）；caseN 只跑一条。
// 先问一下 rf.py：测试环境里有没有这次要执行、但你还没同意过的准备步骤（第一次执行、或命令改了）；有就不跑，把它们交给界面，
// 界面给你看实际的命令和执行目录，你点同意（envs:act approve）以后再跑。不同意就不跑
async function run({ ids, req, fix, all, caseN } = {}) {
  const sets = (Array.isArray(ids) ? ids : ids ? [ids] : []).map(String).filter((x) => ID.test(x));
  const args = ['run', ...sets, ...(all ? ['--all'] : scopeArgs({ req, fix })), ...(caseN != null && sets.length === 1 ? ['--case', String(caseN)] : [])];
  if (!sets.length && !all && !scopeArgs({ req, fix }).length) return { error: uiText('要说跑哪些测试集。', "Select which test sets to run.") };
  const pre = await call([...args, '--prep-check'], TIMEOUTS.read);
  if (!pre.error && pre.blocked && pre.blocked.length) return { needApproval: pre.blocked, env: pre.env, place: pre.place };
  return call(args, TIMEOUTS.run);
}

// 你点的按钮：同意、不同意、标成过期、恢复、改挂靠、AI 新加的标为看过了、同意执行构建命令
async function act({ action, id, change, reason, feature, req, caseN, print, dir } = {}) {
  if (!ID.test(String(id || ''))) return { error: uiText('要给测试集编号。', "Provide a test set ID.") };
  let args;
  const k = change && typeof change === 'object' ? change.id : change; // 不同意时界面给的是整条改动（要交回提出它的会话）
  if (action === 'approve' || action === 'reject') args = [action, String(id), String(k)];
  else if (action === 'stale') args = ['stale', String(id), ...(reason ? ['--reason', String(reason)] : [])];
  else if (action === 'revive') args = ['revive', String(id)];
  else if (action === 'move') args = ['move', String(id), ...(feature ? ['--feature', String(feature)] : []), ...(req ? ['--req', String(req)] : [])];
  else if (action === 'seen') args = ['seen', String(id), ...(caseN != null ? ['--case', String(caseN)] : [])];
  // 同意执行测试集的构建命令：确认框里你看到的那一条（指纹）；rf.py 只认应用的标记，命令或目录在你看过以后变了就不算同意
  else if (action === 'approve-build') {
    if (!/^build:[0-9a-f]{24}$/.test(String(print || ''))) return { error: uiText('没有给出你看过的是哪一条构建命令。', "Specify the build command you reviewed.") };
    args = ['approve-build', String(id), '--print', String(print), ...(dir ? ['--dir', String(dir)] : [])];
  } else return { error: uiText('不认识的操作。', "Unknown operation.") };
  const made = await call(args, TIMEOUTS.act);
  if (!made.error && action === 'reject' && change && typeof change === 'object' && change.by) tellReject(id, change); // 不同意：这是改坏了，告诉提出它的需求的开发会话
  return made;
}

// 以你的身份保存：整份（payload 是 {cases:[…]}），或者一条（caseN 是序号或 'new'）
function save({ id, req, fix, caseN, payload } = {}) {
  if (!ID.test(String(id || ''))) return Promise.resolve({ error: uiText('要给测试集编号。', "Provide a test set ID.") });
  return call(['save', String(id), ...(caseN != null ? ['--case', String(caseN)] : []), ...scopeArgs({ req, fix }), '--file', '-'], TIMEOUTS.act, JSON.stringify(payload));
}

// 以你的身份改测试代码的设置：语言、接入代码、classpath（Java）、build 命令（Java）
function config({ id, payload } = {}) {
  if (!ID.test(String(id || ''))) return Promise.resolve({ error: uiText('要给测试集编号。', "Provide a test set ID.") });
  return call(['config', String(id), '--file', '-'], TIMEOUTS.act, JSON.stringify(payload || {}));
}

// 往一个需求或修复的会话里说一句：经收件箱（inbox.js），开着的等它能收时再送，没开就让界面打开它、这句随开场白交过去
function tell(runId, text, { from = 'testsets', key = null } = {}) {
  require('./inbox').post({ to: runId, text, from, key });
}
function tellReject(id, change) {
  const by = String(change.by);
  if (!OWNER.test(by)) return;
  tell(by.startsWith('F-') ? 'fix:' + by : by, Model.rejectText(id, change)); // 每次不同意都是你点的一次，不去重
}

// 「让 AI 改」：交给谁（见 Model.aiTarget），说什么（prompts.testEditing）。新开的日常聊天由界面开（像发布里的「让 AI 来合并」），
// 这里只给出会话编号和第一句话，并记下这个聊天可以用 rf.py test 的写命令（claude-settings.js 的 TEST_AI_ALLOW）
async function aiEdit({ id, req, fix, text } = {}) {
  const words = String(text || '').trim();
  if (!words) return { error: uiText('先写想怎么改。', "Describe the change first.") };
  const set = await show({ id, req, fix });
  if (set.error) return set;
  const target = Model.aiTarget(set, { req, fix }, await require('./requirements').listReqs());
  const say = testEditing({ set, scope: target.scope, text: words });
  let made;
  if (target.kind === 'chat') {
    const sid = crypto.randomUUID();
    state.testChats.set(sid, { project: state.project, set: set.id });
    made = { kind: 'chat', run: 'session:' + sid, sessionId: sid, title: uiText('改测试集 ', "Edit test set ") + set.id + ' ' + (set.title || ''), say };
  } else {
    tell(target.run, say);
    made = { kind: target.kind, run: target.run, title: target.title };
  }
  const record = { kind: made.kind, run: made.run, title: made.title, at: new Date().toISOString() };
  handed.set(state.project + '|' + set.id, record);
  return Object.assign({ ok: true }, made, { handed: record });
}

// 确认完成前：这个需求有测试集就先跑一遍，有失败或跑不起来只提示；还有没确认的改动要你当场处理完
async function beforeDone(req) {
  if (!OWNER.test(String(req || ''))) return { ok: true };
  const listed = await list({ req });
  if (listed.error) return { ok: true, note: uiText('没能读到测试集：', "Couldn't load test set: ") + listed.error };
  if (!listed.sets.length && !listed.totals.pending_changes) return { ok: true, none: true };
  const ran = listed.sets.length ? await run({ req }) : { sets: [], totals: { sets: 0 } };
  if (ran.needApproval) return { ok: false, needApproval: ran.needApproval, env: ran.env, place: ran.place }; // 先问你同不同意执行准备步骤
  const again = await list({ req }); // 运行以后再读一次：待确认的改动以这时为准
  return Model.doneCheck(ran, again.error ? listed : again);
}

// 文件监视（watcher.js）：.relayforge/tests/ 下的数据、计数和结果一变，就告诉界面重读
let stamp = null;
function testsStamp(root) {
  let latest = 0;
  const dir = path.join(root, '.relayforge', 'tests');
  const look = (folder, depth) => {
    let names = [];
    try { names = fs.readdirSync(folder); } catch (_) { return; }
    for (const name of names) {
      const file = path.join(folder, name);
      let st;
      try { st = fs.statSync(file); } catch (_) { continue; }
      latest = Math.max(latest, st.mtimeMs);
      if (st.isDirectory() && depth) look(file, depth - 1);
    }
  };
  look(dir, 2); // tests/*.json、tests/stats/*.json、tests/results/TS-…/*.json
  return latest;
}
function watch() {
  if (!state.project) return;
  const now = state.project + ':' + testsStamp(state.project);
  if (stamp !== null && now !== stamp) send('tests:changed', {});
  stamp = now;
}

module.exports = { list, show, run, act, save, config, aiEdit, beforeDone, watch, testsStamp, handed, tell };
