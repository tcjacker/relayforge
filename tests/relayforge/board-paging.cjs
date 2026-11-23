// 测试集接入代码：看板「已完成」「已取消」两列分批显示（需求 R-20261121-c909，功能点 RF-137）。
// 只负责按输入造需求、用真实的界面脚本（board.js 等）在假 DOM 里画看板、按输入依次点按钮，再把两列看到的东西原样交回；
// 不含测试数据，不做比对。
// 输入：{ range: 'all'|'recent', done: [几天前完成…], dropped: [几天前取消…], clicks: [['done'|'dropped', 'more'|'fold'] …],
//   look: 要看的列（默认两列都看） }
// 输出：每列 { count 列头数字, cards 显示的卡片（按显示顺序，写成「几天前」）, more 「显示更多」按钮文字或 null, fold 有没有「收起」, empty 空提示或 null }
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const base = path.join(__dirname, '..', '..', 'app', 'src', 'renderer', 'scripts');
// 和 index.html 里看板相关脚本的先后一致（board.js 依赖的名字都在这些脚本里）
const SCRIPTS = ['html-text', 'core', 'inbox-model', 'inbox-panel', 'deps-model', 'conflicts-model', 'board-page-model', 'reqs',
    'board', 'stage-menu', 'intake', 'designs', 'stage-tabs-model', 'panel', 'compose', 'trace-model', 'trace'];

class Node {
  constructor(tag) {
    this.tag = tag; this.children = []; this.hidden = false; this.style = {}; this.dataset = {}; this.textContent = '';
    this.events = {}; this.classList = { toggle() {}, add() {}, remove() {} };
  }
  appendChild(n) { this.children.push(n); return n; }
  insertAdjacentHTML() {}
  querySelectorAll() { return []; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(event, fn) { this.events[event] = fn; }
  setAttribute(name, value) { this[name] = value; }
  querySelector() { return null; }
  scrollIntoView() {}
}

function day(ago) {
  const d = new Date(); d.setDate(d.getDate() - ago);
  return [d.getFullYear(), d.getMonth() + 1, d.getDate()].map((x) => String(x).padStart(2, '0')).join('-');
}

function make(state, ago, i) {
  const field = state === 'done' ? 'completed_on' : 'dropped_on';
  return { id: `R-${state}-${i}`, title: `${state} ${i}`, state, [field]: day(ago), ago, sessions: [], sections: {} };
}

function run(input) {
  const nodes = {};
  const get = (id) => nodes[id] || (nodes[id] = new Node());
  const context = vm.createContext({
    document: {
      getElementById: get, createElement: (tag) => new Node(tag),
      createTextNode: (text) => Object.assign(new Node(), { textContent: text }),
      querySelector: () => get('main'), querySelectorAll: () => [],
    },
    window: { shell: { viewing() {}, traceGet: async () => null } },
    setTimeout, clearTimeout, requestAnimationFrame() {}, refit() {}, show() {},
  });
  for (const file of SCRIPTS) vm.runInContext(fs.readFileSync(path.join(base, file + '.js'), 'utf8'), context);
  get('board-date-filter').value = input.range || 'all';
  const all = (input.done || []).map((ago, i) => make('done', ago, i)).concat((input.dropped || []).map((ago, i) => make('dropped', ago, i)));
  const byId = Object.fromEntries(all.map((r) => [r.id, r]));
  context.reqs = all;
  context.board();

  const col = (stage) => get('board-columns').children.find((c) => c.className === 'col ' + stage);
  const row = (stage) => col(stage).children.find((c) => c.className === 'more-row');
  const button = (stage, cls) => { const r = row(stage); return r && r.children.find((b) => b.className === cls); };
  for (const [stage, which] of input.clicks || []) {
    const b = button(stage, which);
    if (!b) throw new Error(`${stage} 列没有「${which === 'more' ? '显示更多' : '收起'}」按钮可点`);
    b.events.click();
  }
  const out = {};
  for (const stage of input.look || ['done', 'dropped']) {
    const c = col(stage);
    const none = c.children.find((n) => n.className === 'none');
    const more = button(stage, 'more');
    out[stage] = {
      count: c.children[0].children[2].textContent,
      cards: c.children.filter((n) => /^card/.test(n.className)).map((n) => byId[n.dataset.id].ago),
      more: more ? more.textContent : null,
      fold: !!button(stage, 'fold'),
      empty: none ? none.textContent : null,
    };
  }
  return out;
}

module.exports = { run };
