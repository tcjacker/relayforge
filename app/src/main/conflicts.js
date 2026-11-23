// 需求之间的冲突（rf.py conflicts，backend/relayforge/requirements/conflicts.py）：进行中的需求和修复各改了哪些文件、
// 谁和谁改到同一处、和合并目标或彼此合并会不会冲突。算出来推给界面（看板卡片、需求面板），不自动改依赖、不自动改代码。
// 什么时候重算：会话说完一轮（可能刚改了文件、刚提交）、需求列表刷新（包括有需求或修复合并了、主分支前进了）；去抖，在后台跑。
const { rf, parse } = require('./backend');
const { state, send } = require('./state');

const DEBOUNCE_MS = 10000;
const TIMEOUT = 60000;
let timer = null, urgent = false, busy = false, again = false, last = null;

async function refresh() {
  timer = null; urgent = false;
  if (busy) { again = true; return; }
  const root = state.project;
  if (!root) return;
  busy = true;
  try {
    const out = await rf(['conflicts', '--json'], root, undefined, TIMEOUT);
    if (root !== state.project) return;
    const found = out.ok ? parse(out.stdout, null) : null;
    if (found && Array.isArray(found.items)) {
      last = { ...found, at: new Date().toISOString() };
      send('conflicts', last);
      for (const n of notices(last)) require('./inbox').post(n);
    }
  } catch (_) {
    /* 算不出来就保留上一次的结果，下次再算 */
  } finally {
    busy = false;
    if (again) { again = false; schedule(); }
  }
}

// 要告诉开发会话的（经收件箱，inbox.js）：和合并目标会冲突的，让它现在就同步；和别的需求会冲突的，让它知道、别扩大改动。
// 同一组文件只说一次（key 里带文件）；会话没开着就排着，等你打开它时随开场白交过去，不替你打开。只是改到同一处、还不冲突的只在界面上标
function runOf(item) { return item.kind === 'fix' ? 'fix:' + item.id : item.id; }
const working = (item) => !!item && item.state !== 'draft'; // 立项会话只读，不让它去同步代码
function notices(data) {
  const out = [];
  const byId = new Map((data.items || []).map((i) => [i.id, i]));
  const what = (i) => (i.kind === 'fix' ? '修复' : '需求');
  for (const i of data.items || []) {
    if (!working(i) || !i.base_conflicts || !i.base_conflicts.length || !data.base) continue;
    const files = i.base_conflicts.join('、');
    out.push({ to: runOf(i), from: 'conflicts', open: false, key: `conflict:base:${i.id}:${i.base_conflicts.join(',')}`,
      text: `${data.base} 上新合进的改动和这个${what(i)}在 ${files} 上会冲突。趁冲突还小，现在就运行 git merge ${data.base}，解决冲突、跑一遍相关的测试，再接着做；` +
        '冲突处拿不准两边该怎么保留，就停下来问用户。不要运行 rf.py req done 或 issue confirm。' });
  }
  for (const i of data.items || []) { // 改到了要改的功能点以外的代码：告诉开发会话一次（同一组文件不重复说），只提示
    if (!working(i) || i.kind !== 'req') continue;
    for (const o of i.outside || []) {
      out.push({ to: runOf(i), from: 'conflicts', open: false, key: `scope:${i.id}:${o.feature}:${o.files.join(',')}`,
        text: `这个需求改到了 ${o.feature} 的代码（${o.files.join('、')}），但「要改的功能点」里没有它。确实要改的话，用 rf.py note 记下为什么要改它，`
          + '并告诉用户把它加进要改的功能点（完成时它的描述才会一起更新）；只是顺手改的，改回去。不用停下来等。' });
    }
  }
  for (const p of data.pairs || []) {
    if (!p.conflicts || !p.conflicts.length) continue;
    for (const [me, other] of [[p.a, p.b], [p.b, p.a]]) {
      const mine = byId.get(me), theirs = byId.get(other);
      if (!working(mine) || !theirs) continue;
      out.push({ to: runOf(mine), from: 'conflicts', open: false, key: `conflict:pair:${other}:${p.conflicts.join(',')}`,
        text: `${theirs.kind === 'fix' ? '修复 ' + theirs.id : '需求《' + theirs.title + '》'}也改了 ${p.conflicts.join('、')}，两个分支合并时会冲突。` +
          '这些文件尽量只改这个需求必须改的地方，不要顺手重排或重写；谁等谁由用户在应用里决定，不用你处理。' });
    }
  }
  return out;
}

// 过一会儿再算（这段时间里又有动静就往后推）；delay 为 0 是界面要马上看（还没算过），之后的动静不再把它往后推
function schedule(delay = DEBOUNCE_MS) {
  if (timer && urgent) return;
  if (timer) clearTimeout(timer);
  urgent = delay === 0;
  timer = setTimeout(refresh, delay);
}

// attention.onSettled 的听众：这一轮说完了才算，干活中途不算
function settled(_id, kind) {
  if (kind === 'reply' || kind === 'answer' || kind === 'exit') schedule();
}

// 换项目时清掉上一个项目的结果
function reset() {
  last = null;
  if (timer) { clearTimeout(timer); timer = null; urgent = false; }
}

function current() { return last; }

// 立项时按历史预测（rf.py conflicts --predict）：这个需求大概会动到哪些文件、可能撞上哪些进行中的需求。界面点开立项中的需求、
// 确认立项时要；一分钟内同一个需求不重算
const predicted = new Map(); // 需求编号 -> { at, data }
const PREDICT_TTL = 60000;
async function predict(id) {
  if (!state.project || !/^R-[\w-]+$/.test(String(id || ''))) return null;
  const hit = predicted.get(id);
  if (hit && Date.now() - hit.at < PREDICT_TTL) return hit.data;
  const out = await rf(['conflicts', '--predict', String(id), '--json'], state.project, undefined, TIMEOUT);
  const data = out.ok ? parse(out.stdout, null) : null;
  if (data) predicted.set(id, { at: Date.now(), data });
  return data;
}

module.exports = { schedule, settled, refresh, reset: () => { reset(); predicted.clear(); }, current, notices, predict };
