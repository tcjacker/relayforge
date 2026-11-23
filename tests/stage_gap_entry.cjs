// 开发中点「交给测试」/「跳过测试直接验收」前的确认：接入代码只加载真实的页面脚本、模拟点按钮和点弹窗，
// 返回「弹没弹、弹窗写了什么、发出了哪些阶段变更」，不含数据、不做比对。
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const base = path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts');

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
    window: { shell: { viewing() {}, traceGet: async () => null, sessionUsage: async () => ({ sessions: [], groups: [] }), openTesting: async () => ({}), predictConflicts: async () => null } },
    preview() {}, intakeAssessment() {}, ladder() {}, impact() {}, levelRow: () => new Node(), setTimeout, clearTimeout, levelName: () => '方向', terminal() {}, show() {}, refit() {}, requestAnimationFrame() {},
  });
  for (const f of ['html-text', 'core', 'inbox-model', 'inbox-panel', 'deps-model', 'conflicts-model', 'board-page-model', 'reqs',
    'board', 'stage-menu', 'designs', 'stage-tabs-model', 'panel', 'compose', 'trace-model', 'trace', 'session-history-model', 'session-history']) vm.runInContext(fs.readFileSync(path.join(base, f + '.js'), 'utf8'), ctx);
  get('board-date-filter').value = 'all';
  return { ctx, get };
}

// input: { to: 'test'|'skip', plan: [{status}], answer: 'confirm'|'cancel' }
async function run(input) {
  const { ctx, get } = page();
  ctx.HTMLDialogElement = undefined;
  const calls = [];
  let shown = 0;
  ctx.window.shell.setReq = async (id, t) => { calls.push(t); return { ...req(), stage: t === 'test' ? 'test' : 'check' }; };
  get('stage-dlg').showModal = () => { shown++; };
  get('stage-dlg').close = () => {};
  get('stage-dlg-no').focus = () => {};
  vm.runInContext(`states['R-1'] = { state: { plan: ${JSON.stringify(input.plan)} } }; changeStage(${JSON.stringify(req())}, ${JSON.stringify(input.to)});`, ctx);
  await new Promise((r) => setTimeout(r, 5));
  const asked = shown > 0;
  const text = asked ? get('stage-dlg-body').children.map((c) => c.textContent).join('\n') : '';
  const title = asked ? get('stage-dlg-title').textContent : '';
  const beforeAnswer = calls.slice();
  if (asked && input.answer === 'confirm') { get('stage-dlg-yes').onclick(); await new Promise((r) => setTimeout(r, 5)); }
  // 点取消：弹窗只是关掉，不调用确认的回调
  return { asked, title, text, calls_before_answer: beforeAnswer, calls_final: calls, shown };
}

function req() {
  return { id: 'R-1', title: '导出加合计行', file: 'x.md', state: 'open', stage: 'build', worktree: '/wt', branch: 'req/1', sessions: ['dev'], dev_sessions: ['dev'], test_sessions: [], designs: [],
    sections: { '需求说明': [], '要改的功能点': [], '验收条件': ['a'] }, moves: {}, testing: { flow: true, checks: [], classified: false, all_ui: false, rounds: [], last: null, round: 1, sessions: [], kicked: 0, told: 0, needs_kick: false, to_tell: 0, skipped: false, ever: false, doing: '', line: '' } };
}
module.exports = { run };
