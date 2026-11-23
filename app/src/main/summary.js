// 确认完成以后的两件后台的事，都不挡完成：
// 1. 总结：rf.py summarize R-… 调一次无界面模型，写概括标签、几句话的总结，聊到了但没记下的决定交给你确认（待确认）。
//    一次只跑一个，其余排队；没装模型、失败了，需求照常是已完成，面板上显示「还没总结」和「重新总结」。
// 2. 会话记录补存：打开项目时 rf.py archive sweep 一次，把已经完成、还没存到本机的会话记录补存（存档只在这台电脑上，不进仓库）。
const { rf, lastLine, parse, uiText = (zh) => zh } = require('./backend');
const { state, send } = require('./state');

const TIMEOUT = 15 * 60 * 1000;
const queue = []; // { id, root }
let current = null; // 正在总结的 { id, root }
const failed = new Map(); // 需求编号 -> 没总结出来的原因（只记这次打开应用以来的）

function view() {
  const here = (x) => x.root === state.project;
  return { running: current && here(current) ? [current.id] : [], queued: queue.filter(here).map((x) => x.id), failed: Object.fromEntries(failed) };
}
function push() { send('summary', view()); }

// 确认完成以后自动调用；面板上点「重新总结」也走这里
function start(id) {
  const text = String(id || '').trim();
  if (!/^R-[\w-]+$/.test(text)) return { error: uiText('要给需求编号。', "Enter a requirement ID.") };
  const root = state.project;
  if ((current && current.id === text && current.root === root) || queue.some((x) => x.id === text && x.root === root)) return view();
  failed.delete(text);
  queue.push({ id: text, root });
  pump();
  push();
  return view();
}

async function pump() {
  if (current || !queue.length) return;
  current = queue.shift();
  push();
  const { id, root } = current;
  const out = await rf(['summarize', id, '--json'], root, undefined, TIMEOUT);
  current = null;
  if (!out.ok) failed.set(id, out.timedOut ? uiText('总结超时了。', "The summary timed out.") : lastLine(out.stderr));
  if (root === state.project) {
    require('./requirements').pushReqs();
    const made = out.ok ? parse(out.stdout, null) : null;
    if (made && made.proposed && made.proposed.length) send('notice', uiText("需求 {0} 写好了总结：有 {1} 条聊到了但没记下的决定，等你确认（{2}）。", "Requirement {0} has a summary and {1} decisions awaiting confirmation ({2}).", id, made.proposed.length, made.proposed.join('、')));
  }
  push();
  pump();
}

// 打开项目时补存一次会话记录；同一个项目这次打开应用只做一次
const swept = new Set();
function sweep(root = state.project) {
  if (!root || swept.has(root)) return Promise.resolve(null);
  swept.add(root);
  return rf(['archive', 'sweep', '--json'], root, undefined, TIMEOUT).then((out) => {
    const made = out.ok ? parse(out.stdout, null) : null;
    if (made && made.changed && root === state.project) require('./requirements').pushReqs();
    return made;
  });
}

module.exports = { start, sweep, view, push };
