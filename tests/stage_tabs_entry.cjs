// 需求详情阶段标签页的接入代码：只读输入、调用被测代码、返回结果，不含数据、不做比对。
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const base = path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts');
const M = require(path.join(base, 'stage-tabs-model'));
const plain = (x) => JSON.parse(JSON.stringify(x));

// 真实的 openTab（中间切会话）和 viewTab（右边点标签）：加载页面脚本，用最小的假 DOM
function page() {
  class Node {
    constructor() { this.children = []; this.hidden = false; this.style = {}; this.dataset = {}; this.textContent = ''; this.className = ''; this.classList = { toggle() {}, add() {}, remove() {} }; }
    appendChild(n) { this.children.push(n); return n; }
    insertAdjacentHTML() {}
    querySelectorAll() { return []; }
    replaceChildren(...n) { this.children = n; }
    addEventListener() {}
    setAttribute() {}
    remove() {}
  }
  const nodes = {};
  const get = (id) => nodes[id] || (nodes[id] = new Node());
  const ctx = vm.createContext({
    document: { getElementById: get, createElement: () => new Node(), createTextNode: (t) => Object.assign(new Node(), { textContent: t }), querySelector: () => get('main'), querySelectorAll: () => [] },
    window: { shell: { viewing() {}, traceGet: async () => null, openTesting: async () => ({}), predictConflicts: async () => null } },
    preview() {}, intakeAssessment() {}, ladder() {}, impact() {}, levelRow: () => new Node(), setTimeout, clearTimeout, levelName: () => '方向', terminal() {}, show() {}, refit() {}, requestAnimationFrame() {},
  });
  for (const f of ['html-text', 'core', 'inbox-model', 'inbox-panel', 'deps-model', 'conflicts-model', 'board-page-model', 'reqs',
    'board', 'stage-menu', 'designs', 'stage-tabs-model', 'panel', 'compose', 'trace-model', 'trace']) vm.runInContext(fs.readFileSync(path.join(base, f + '.js'), 'utf8'), ctx);
  get('board-date-filter').value = 'all';
  return ctx;
}

function run(input) {
  const { op, stage, tab } = input;
  if (op === 'open') { // 打开需求：停在哪、标签栏分不分
    return { tabs: M.hasTabs(input.req || {}, stage), tab: M.defaultTab(stage), tip_test: M.tip(stage, 'test', !!input.skipped) };
  }
  if (op === 'view') { // 给定阶段和标签：显示哪些块、哪些按钮、验收条件的样式
    const later = M.isLater(stage, tab);
    return { blocks: plain(M.blocks(stage, tab)), buttons: plain(M.buttons(stage, tab)), criteria: M.criteriaMode(tab), later_text: later ? M.LATER[tab].join('') : null };
  }
  if (op === 'tabs') { // 依次点各个标签：每个标签里有哪些按钮
    return Object.fromEntries(M.ORDER.map((t) => [t, plain(M.buttons(stage, t))]));
  }
  if (op === 'follow') { // 阶段变化：右边停在哪
    const st = M.sync({ viewing: input.viewing, stage: input.from }, stage);
    return { viewing: st.viewing, stage: st.stage, highlight: st.stage };
  }
  if (op === 'ui') { // 中间 / 右边的真实操作，依次执行，返回两边各停在哪
    const c = page();
    const r = { id: 'R-1', title: 't', state: 'open', stage: stage, worktree: '/wt', branch: 'b', sessions: ['dev'], dev_sessions: ['dev'], test_sessions: ['t'], designs: [], sections: {}, moves: {}, testing: { flow: true, checks: [], rounds: [], sessions: ['t'], kicked: 1 } };
    c.reqs = [r]; c.current = r.id;
    vm.runInContext('stageView = {}; shown = {};', c);
    vm.runInContext(`stageView['R-1'] = ${JSON.stringify({ viewing: input.right, stage: stage })}; shown['R-1'] = ${JSON.stringify(input.middle === 'chat' ? null : input.middle)};`, c);
    for (const a of input.actions) {
      if (a.at === 'middle') vm.runInContext(`openTab(reqs[0], ${JSON.stringify(a.to === 'chat' ? null : a.to)})`, c);
      else vm.runInContext(`viewTab('R-1', ${JSON.stringify(a.to)})`, c);
    }
    const out = vm.runInContext(`({ right: stageView['R-1'].viewing, middle: shown['R-1'] })`, c);
    return { right: out.right, middle: out.middle || 'chat' };
  }
  throw new Error('未知操作 ' + op);
}
module.exports = { run };
