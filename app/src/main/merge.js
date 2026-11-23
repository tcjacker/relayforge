// 确认完成以后的合并：排队，同一时间只有一个需求（或问题的修复）在合并进项目目录签出的分支。
// 分支还没包含目标分支的最新内容时，没有代码冲突的由 rf.py req done（修复是 rf.py issue confirm）自己在工作目录里合进来；
// 有冲突、或者还有没提交的改动时它什么都不落，程序回到这个需求（或修复）的会话，让 AI 提交改动、把目标分支合并进来、
// 解决冲突，只测冲突可能弄坏的地方；它这一轮说完了，程序再试。
// 分支包含了目标分支的最新内容以后，合并回去不会再有冲突，这时才写「已完成」（修复是「已解决」）。
// 修复排在同一个队伍里，编号是 fix:修复编号，结果推给问题页。
// 真正的 git 合并由 rf.py req done 做，它自己也在 .git 里加锁，防止别的进程同时合并。
const { uiText = (zh) => zh, exec, rf, lastLine, parse } = require('./backend');
const { send, state } = require('./state');
const { pushReqs } = require('./requirements');

// 等开发会话同步和跑测试时不占合并队列。只恢复用户已经确认过的合入。
const fs = require('fs');
const path = require('path');
const { createHash, randomUUID } = require('crypto');
const { BUNDLED, RF_HOME } = require('./config');
const queues = new Map();
const MAX_HANDOFFS = 3;
let closeRun = () => false;

function queueFile(root) {
  const key = createHash('sha256').update(root).digest('hex');
  return path.join(RF_HOME, 'merge-queue', key + '.json');
}

function queueFor(root) {
  if (!queues.has(root)) {
    let saved = [];
    try { saved = JSON.parse(fs.readFileSync(queueFile(root), 'utf8')); }
    catch (error) { if (error.code !== 'ENOENT') console.error(uiText('读取合并队列失败', "Couldn't read the merge queue"), error); }
    if (!Array.isArray(saved)) saved = [];
    const pending = [...saved.filter((item) => item.phase !== 'syncing'), ...saved.filter((item) => item.phase === 'syncing')];
    queues.set(root, pending.map((item) => ({ ...item, phase: item.phase === 'stuck' ? 'stuck' : 'waiting' })));
  }
  return queues.get(root);
}

function save(root) {
  const file = queueFile(root);
  const temp = file + '.' + randomUUID();
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(temp, JSON.stringify(queueFor(root)), { mode: 0o600 });
    fs.renameSync(temp, file);
  } catch (error) {
    console.error(uiText('保存合并队列失败', "Couldn't save the merge queue"), error);
    if (root === state.project) send('notice', uiText('无法保存合并队列，重启后需要重新发起合并。', "Couldn't save the merge queue. Start the merge again after restarting."));
    try { fs.unlinkSync(temp); } catch (_) { /* 临时文件可能还没写出来 */ }
  }
}

const ready = (item) => item.phase === 'waiting' || item.phase === 'merging';
const head = (root) => queueFor(root).find(ready);
const find = (root, id) => queueFor(root).find((x) => x.id === id);
function push(root) {
  if (root !== state.project) return;
  let ahead = 0;
  send('merge', queueFor(root).map((x) => ({ id: x.id, phase: x.phase, ahead: ready(x) ? ahead++ : 0, base: x.base || '', files: x.files || [], note: x.note || '', busy: x.phase === 'syncing' && !!x.busy,
    gate: x.gate || null })));
}

function update(root) {
  save(root);
  push(root);
}

// 合并完测什么：只测冲突可能弄坏的地方，不跑整个项目的测试（大项目一跑十几分钟，合并一直卡着）。
// 没有冲突时不用测：两边改的是不同的地方，git 自己合得上；需求和修复各自的测试在验收前已经跑过。
function checks(told, item) {
  const conflicted = told.merging || (told.files || []).length;
  if (!conflicted) return '这次合并没有冲突，合完提交就行，不用跑测试。';
  const scope = item.kind === 'fix' ? `--fix ${String(item.id).replace(/^fix:/, '')}` : `--req ${item.id}`;
  return '解决完冲突、提交合并以后，只测冲突可能弄坏的地方，不要跑整个项目的测试：'
    + `运行 python3 ${BUNDLED} test run ${scope}（这个${item.kind === 'fix' ? '修复' : '需求'}的测试集和它动到的功能点的旧测试集），`
    + '再找出直接测冲突文件的那几个项目测试（按测试文件名、导入或引用了冲突文件来找）只跑它们；找不到就说一声。没通过就修到通过，修不了说明原因。';
}

// 合并闸门没过（backend/relayforge/testsets/gate.py）：合并后的代码上，这几个测试集没过。交回会话修，修好它这一轮说完，程序再检查
function gatePrompt(told, item) {
  const scope = item.kind === 'fix' ? `--fix ${String(item.id).replace(/^fix:/, '')}` : `--req ${item.id}`;
  const rows = (told.gate.failing || []).map((f) => `- ${f.id}「${f.title}」` + (f.result === 'broken' ? `跑不起来：${f.error || ''}`
    : `没过的第 ${f.cases.join('、')} 条` + (f.names && f.names.length ? `（${f.names.join('、')}）` : '')) + `；单独重跑：python3 ${BUNDLED} test run ${f.id}` + (f.cases.length === 1 && f.cases[0] !== '*' ? ` --case ${f.cases[0]}` : ''));
  return [
    item.handoffs > 0 ? '上轮结束后，程序又检查了一遍，还是没过：\n' : (item.kind === 'fix' ? '我确认修好了。' : '我点了「确认完成」。')
      + '程序在合并后的代码（这个工作目录现在的样子，已经包含目标分支的最新提交）上跑了合并前的检查，这几个测试集没过：\n',
    rows.join('\n'),
    `\n先运行 git status 看清工作目录，再用 python3 ${BUNDLED} test show 测试集编号 看没过的那几条的输入、期望和实际输出，找出原因修好代码并提交。`,
    '这些测试集有的不是这个' + (item.kind === 'fix' ? '修复' : '需求') + '自己的（是依赖它的功能点、或者主干流程上的），它们没过多半说明这次的改动影响到了别处，按它们的期望修代码；',
    '如果确实是测试数据本身过时了，用 rf.py test edit 写清理由（改落定的数据要等用户同意），不要为了通过去改期望。',
    `修完运行 python3 ${BUNDLED} test run ${scope} 确认。不要动项目目录，不要运行 ${item.kind === 'fix' ? 'rf.py issue confirm' : 'rf.py req done'}，程序会再检查并合并回去。`,
  ].join('');
}

const prompt = (told, item) => [
  item.handoffs > 0 ? '上轮结束后，程序检查发现同步还没完成。' + told.error :
    item.kind === 'fix'
      ? `我确认修好了。合并回项目目录的分支 ${told.base} 之前，要先在这个修复自己的工作目录同步 ${told.base}。`
      : `我点了「确认完成」。合并回项目目录的分支 ${told.base} 之前，要先在这个需求自己的工作目录同步 ${told.base}。`,
  '先运行 git status，按当前工作目录的状态接着做。',
  told.merging
    ? 'Git 已经在合并中。核对这笔合并是否来自目标分支，再解决尚未解决的冲突并完成这次合并提交。完成前不要再运行 git merge，也不要中止已有合并。'
    : [
      (told.loose || []).length ? `先把还没提交的改动提交（${told.loose.join('、')}）。` : '',
      `然后运行 git merge ${told.base}` + ((told.files || []).length ? `，会有冲突（${told.files.join('、')}），请理解并保留两边的改动。` : '。'),
    ].join(''),
  checks(told, item),
  '结束前确认没有未解决的冲突或未完成的合并，并且目标分支的代码已同步。确实需要我决定、批准或无法验证时，说明具体原因。',
  `不要动项目目录，不要运行 ${item.kind === 'fix' ? 'rf.py issue confirm' : 'rf.py req done'}。程序会检查并把它合并回去。`,
].join('');

async function enqueue(id, close, spec) {
  const root = state.project;
  if (!root) return { error: uiText('没有打开项目。', "No project is open.") };
  if (close) closeRun = close;
  const item = find(root, id);
  if (item) return queueStatus(root, item);
  return join(root, { id, phase: 'waiting', handoffs: 0, ...(spec || {}) });
}

function queueStatus(root, item) {
  const ahead = ready(item) ? queueFor(root).filter(ready).indexOf(item) : 0;
  return { merging: { id: item.id, phase: item.phase, ahead, ...(ahead ? { holder: head(root).id } : {}) } };
}

function join(root, item) {
  queueFor(root).push(item);
  update(root);
  return head(root) === item ? attempt(root, item) : queueStatus(root, item);
}

async function attempt(root, item) {
  if (root !== state.project || head(root) !== item || item.phase === 'merging') return queueStatus(root, item);
  item.phase = 'merging'; item.note = ''; update(root);
  const ref = await exec('git', ['symbolic-ref', '--quiet', '--short', 'HEAD'], root);
  if (head(root) !== item) return { error: uiText('合并已中止。', "Merge aborted.") };
  if (root !== state.project) {
    item.phase = 'waiting';
    update(root);
    return queueStatus(root, item);
  }
  const target = ref.stdout.trim();
  if (item.target && item.target !== target) {
    return blocked(root, item, uiText("目标分支是 {0}。请切回该分支，再重新检查并合并。", "The target branch is {0}. Switch back to it, then check and merge again.", item.target));
  }
  item.target = target;
  save(root);
  if (item.kind !== 'fix') await require('./services').stopScope(String(item.id), root);
  if (head(root) !== item) return { error: uiText('合并已中止。', "Merge aborted.") };
  if (root !== state.project) {
    item.phase = 'waiting';
    update(root);
    return queueStatus(root, item);
  }
  const skip = item.skipGate ? ['--skip-gate'] : []; // 你在合并卡住时点了「跳过检查，仍然合并」
  delete item.skipGate;
  const out = item.kind === 'fix'
    ? await rf(['issue', 'confirm', item.issue, '--json', ...skip], root)
    : await rf(['req', 'done', item.id, '--json', ...skip], root);
  if (head(root) !== item) return { error: uiText('合并已中止。', "Merge aborted.") };
  const { id } = item;
  const active = root === state.project;
  if (out.ok) {
    const made = parse(out.stdout, null);
    if (active && (item.kind !== 'fix' || (made && made.state === 'resolved'))) closeRun(id);
    finish(root, item);
    if (active) {
      if (item.kind === 'fix') { require('./issues').pushIssues(); send('issues:merged', { id, issue: made }); }
      else {
        pushReqs(); send('merge:finished', made || { id });
        if (made && made.state === 'done') require('./summary').start(made.id);
        // 前置需求完成了：在等它、现在前置需求都齐了的需求，提醒「可以开始了」（只是提醒，立项还是你点）
        for (const next of (made && made.state === 'done' && made.dependents) || []) {
          if (next.ready) require('./attention').issue(next.id, uiText('可以开始了：', "Ready to start: ") + next.title, uiText("它的前置需求《{0}》已经确认完成。", "Prerequisite “{0}” is complete.", made.title));
        }
      }
    }
    return made || { error: uiText('没有读懂确认的结果。', "Couldn't read the completion result.") };
  }
  const told = parse(out.stdout, null) || {};
  if (told.gate) {
    // 合并闸门：在合并后的代码上跑的回归没过（或者准备步骤、构建命令还没经你同意），什么都没合进去
    item.gate = { failing: (told.gate.failing || []).map((f) => ({ id: f.id, title: f.title, kind: f.kind })), blocked: told.gate.blocked || [] };
    if (item.gate.blocked.length) return blocked(root, item, told.error);
    if (item.handoffs >= MAX_HANDOFFS) return blocked(root, item, told.error + uiText(' 自动交回几次后还是没过，请查看会话；也可以跳过检查仍然合并。', " Still failing after handing it back several times. Check the session, or skip the check and merge anyway."));
    if (!active) {
      item.phase = 'waiting';
      update(root);
      return queueStatus(root, item);
    }
    item.phase = 'syncing';
    item.busy = true;
    const message = gatePrompt(told, item);
    item.handoffs++;
    resolveIn(id, message);
    send(item.kind === 'fix' ? 'issues:resolve' : 'merge:resolve', { id, prompt: message, gate: true, headless: !state.runs.get(id) || !!state.runs.get(id).headless });
    advance(root);
    return { merging: { id, phase: 'syncing', gate: item.gate } };
  }
  delete item.gate;
  if (told.busy) {
    item.phase = 'waiting'; item.note = told.error || '';
    update(root);
    setTimeout(() => attempt(root, item), 3000);
    return { merging: { id, phase: 'waiting', holder: told.holder } };
  }
  if (told.sync) {
    item.base = told.base; item.files = told.files || [];
    const stopped = item.stopped;
    delete item.stopped;
    if (item.handoffs >= MAX_HANDOFFS) {
      return blocked(root, item, [stopped?.text, told.error, uiText('自动处理后仍未同步，请查看会话。', "The branch still needs syncing. Check the session.")].filter(Boolean).join(' '));
    }
    if (!active) {
      item.phase = 'waiting';
      update(root);
      return queueStatus(root, item);
    }
    item.phase = 'syncing';
    item.busy = true; // 话交给 AI 了：在它这一轮说完之前，都算它在干活（合并、跑测试）
    const message = prompt(told, item);
    item.handoffs++;
    resolveIn(id, message);
    send(item.kind === 'fix' ? 'issues:resolve' : 'merge:resolve', { id, prompt: message, headless: !state.runs.get(id) || !!state.runs.get(id).headless });
    advance(root);
    return { merging: { id, phase: 'syncing', base: told.base, files: item.files } };
  }
  const error = told.error || lastLine(out.stderr);
  if (item.handoffs > 0 || !active) return blocked(root, item, error);
  finish(root, item);
  return { error };
}

// 合并前的同步交给 AI：会话开着就经收件箱（等它这一轮说完再送）；没开着就在后台无界面运行一轮（headless.js），不用你盯着
function resolveIn(id, message) {
  const sessions = require('./sessions');
  if (state.runs.get(id)) require('./inbox').post({ to: id, from: 'merge', text: message, open: false });
  else sessions.open({ id, say: message, headless: true });
}

function blocked(root, item, note) {
  item.phase = 'stuck'; item.note = note;
  advance(root);
  return { merging: { id: item.id, phase: 'stuck', note } };
}

function finish(root, item) {
  const queue = queueFor(root);
  const at = queue.indexOf(item);
  if (at < 0) return;
  const wasHead = item === head(root);
  queue.splice(at, 1);
  if (wasHead) advance(root);
  else update(root);
}

function advance(root) {
  update(root);
  const next = head(root);
  if (next) attempt(root, next);
}

function abort(id) {
  const root = state.project;
  const item = root && find(root, id);
  if (item) finish(root, item);
  return { ok: true };
}

function requeue(root, item) {
  const queue = queueFor(root);
  queue.splice(queue.indexOf(item), 1);
  item.phase = 'waiting';
  item.note = '';
  return join(root, item);
}

// force：AI 还在会话里干活（多半在跑测试）时，你在确认框里点了「仍然合并」
// skipGate：合并闸门没过、合并停住时，你点了「跳过检查，仍然合并」（rf.py req done --skip-gate，完成记录里会写明）
function retry(id, force, skipGate) {
  const root = state.project;
  const item = root && find(root, id);
  if (!item) return { merging: null };
  if (skipGate && item.gate && item.phase === 'stuck') item.skipGate = true;
  if (ready(item)) return queueStatus(root, item);
  if (item.phase === 'syncing' && item.busy && !force) return { confirm: true, id };
  delete item.busy;
  if (item.phase === 'stuck') item.handoffs = 0;
  delete item.stopped;
  delete item.awaitingAnswer;
  return requeue(root, item);
}

function settled(id, kind = 'reply', text = '') {
  const root = state.project;
  if (!root) return;
  const item = find(root, id);
  if (!item) return;
  if (kind === 'running' && item.awaitingAnswer) {
    delete item.awaitingAnswer;
    item.phase = 'syncing'; item.note = ''; item.busy = true;
    update(root);
    return;
  }
  if (item.phase !== 'syncing') return;
  item.busy = kind === 'running' || kind === 'approve'; // 等你批准时这一轮也还没说完
  if (kind === 'answer') {
    item.awaitingAnswer = true;
    return blocked(root, item, uiText('AI 在会话里需要你的答复。答复后会继续合并。', "The Agent is waiting for your reply. Merging will continue after you reply."));
  }
  if (kind === 'approve' || kind === 'running') {
    item.note = kind === 'approve' ? uiText('AI 正在等你批准会话里的操作。', "The Agent is waiting for your approval.") : '';
    push(root);
    return;
  }
  item.stopped = { text };
  return requeue(root, item);
}

// 切换项目会关闭旧项目的会话。切回来时先核对 Git，再决定是否需要恢复 AI。
function pause(root) {
  const queue = queues.get(root);
  if (!queue) return;
  for (const item of queue) {
    if (item.phase === 'syncing') {
      item.phase = 'waiting';
      item.note = '';
    }
  }
  save(root);
}

function list(close) {
  if (close) closeRun = close;
  const root = state.project;
  if (!root) return 0;
  push(root);
  const item = head(root);
  if (item) attempt(root, item);
  return queueFor(root).length;
}

module.exports = { enqueue, abort, retry, settled, pause, list };
