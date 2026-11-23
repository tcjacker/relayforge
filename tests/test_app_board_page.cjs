// 看板「已完成」「已取消」两列分批显示（app/src/renderer/scripts/board-page-model.js 和 board.js 的 moreRow）：
// 先显示最新的 10 张，「显示更多（还有 N 个）」每次再多 10 张，展开过以后有「收起」；切换日期范围、离开需求页回到 10 张。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const base = path.join(__dirname, '..', 'app', 'src', 'renderer');
const P = require(path.join(base, 'scripts', 'board-page-model.js'));

const day = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return [d.getFullYear(), d.getMonth() + 1, d.getDate()].map((x) => String(x).padStart(2, '0')).join('-'); };
// n 个已完成（或已取消）的需求，第 i 个在 i 天前完成；打乱顺序，看排序是不是程序做的
function reqs(n, state = 'done', from = 0) {
  const field = state === 'done' ? 'completed_on' : 'dropped_on';
  const out = Array.from({ length: n }, (_, i) => ({ id: `R-${state}-${i + from}`, title: `${state} ${i + from}`, state, [field]: day(i + from), sessions: [], sections: {} }));
  return out.reverse();
}
const ids = (cards) => cards.map((r) => r.id);

test('model: 超过 10 张只给最新的 10 张，按日期从新到旧，算出剩余数', () => {
  const pg = P.page('done', reqs(23), P.fresh().done);
  assert.deepEqual(ids(pg.cards), Array.from({ length: 10 }, (_, i) => `R-done-${i}`));
  assert.equal(pg.left, 13); assert.equal(pg.more, true); assert.equal(pg.fold, false);
  assert.equal(P.moreText(pg.left), '显示更多（还有 13 个）');
});

test('model: 点显示更多再多 10 张，最后不足 10 张全部显示，按钮消失', () => {
  let s = P.fresh();
  s = P.more(s, 'done');
  let pg = P.page('done', reqs(33), s.done);
  assert.equal(pg.cards.length, 20); assert.equal(pg.left, 13); assert.equal(pg.fold, true);
  pg = P.page('done', reqs(23), s.done);
  assert.equal(pg.cards.length, 20); assert.equal(pg.left, 3);
  s = P.more(s, 'done'); pg = P.page('done', reqs(23), s.done);
  assert.equal(pg.cards.length, 23); assert.equal(pg.more, false); assert.equal(pg.fold, true);
  // 恰好 20 张
  pg = P.page('done', reqs(20), P.fresh().done);
  assert.equal(pg.left, 10);
  pg = P.page('done', reqs(20), P.more(P.fresh(), 'done').done);
  assert.equal(pg.cards.length, 20); assert.equal(pg.more, false);
});

test('model: 收起回到 10 张；两列各自展开互不影响；不超过 10 张没有按钮；别的列不分批', () => {
  let s = P.more(P.more(P.fresh(), 'done'), 'done');
  assert.equal(P.page('done', reqs(33), s.done).cards.length, 30);
  s = P.fold(s, 'done');
  const pg = P.page('done', reqs(33), s.done);
  assert.equal(pg.cards.length, 10); assert.equal(pg.left, 23); assert.equal(pg.fold, false);
  s = P.more(P.fresh(), 'done');
  assert.equal(P.page('done', reqs(23), s.done).cards.length, 20);
  assert.equal(P.page('dropped', reqs(14, 'dropped'), s.dropped).cards.length, 10);
  for (const [stage, n] of [['done', 10], ['dropped', 3], ['done', 0]]) {
    const p = P.page(stage, reqs(n, stage), P.fresh()[stage]);
    assert.equal(p.cards.length, n); assert.equal(p.more, false); assert.equal(p.fold, false);
  }
  const talk = P.page('talk', reqs(25), undefined);
  assert.equal(talk.cards.length, 25); assert.equal(talk.more, false);
});

// 用真的 board.js 画看板，看列里的卡片、按钮和列头数字
function renderer() {
  class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.hidden = false; this.style = {}; this.dataset = {}; this.textContent = ''; this.events = {}; this.classList = { toggle() {}, add() {}, remove() {} }; }
    appendChild(n) { this.children.push(n); return n; }
    insertAdjacentHTML() {}
    querySelectorAll() { return []; }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(event, fn) { this.events[event] = fn; }
    setAttribute(name, value) { this[name] = value; }
    querySelector() { return null; }
    scrollIntoView() {}
  }
  const nodes = {};
  const get = (id) => nodes[id] || (nodes[id] = new Node());
  const context = vm.createContext({
    document: { getElementById: get, createElement: (tag) => new Node(tag), createTextNode: (text) => Object.assign(new Node(), { textContent: text }),
      querySelector: () => get('main'), querySelectorAll: () => [] },
    window: { shell: { viewing() {}, traceGet: async () => null } },
    setTimeout, clearTimeout, requestAnimationFrame() {}, refit() {}, show() {},
  });
  for (const file of ['html-text', 'core', 'inbox-model', 'inbox-panel', 'deps-model', 'conflicts-model', 'board-page-model', 'reqs',
    'board', 'stage-menu', 'intake', 'designs', 'stage-tabs-model', 'panel', 'compose', 'trace-model', 'trace']) vm.runInContext(fs.readFileSync(path.join(base, 'scripts', file + '.js'), 'utf8'), context);
  get('board-date-filter').value = 'all';
  const col = (stage) => get('board-columns').children.find((c) => c.className === 'col ' + stage);
  const cards = (stage) => col(stage).children.filter((c) => /^card/.test(c.className));
  const row = (stage) => col(stage).children.find((c) => c.className === 'more-row');
  const button = (stage, cls) => row(stage) && row(stage).children.find((b) => b.className === cls);
  const count = (stage) => col(stage).children[0].children[2].textContent;
  return { c: context, get, col, cards, row, button, count };
}

test('看板：已完成 23 张显示最新 10 张和「显示更多（还有 13 个）」，没有收起，列头 23', () => {
  const { c, cards, button, count } = renderer();
  c.reqs = reqs(23); c.board();
  assert.deepEqual(cards('done').map((n) => n.dataset.id), Array.from({ length: 10 }, (_, i) => `R-done-${i}`));
  assert.equal(button('done', 'more').textContent, '显示更多（还有 13 个）');
  assert.equal(button('done', 'fold'), undefined);
  assert.equal(count('done'), '23');
});

test('看板：最近 7 天只和筛出来的比，列头 12/30', () => {
  const { c, get, cards, button, count } = renderer();
  c.reqs = reqs(12).map((r, i) => ({ ...r, completed_on: day(i % 7) })).concat(reqs(18, 'done', 20)); get('board-date-filter').value = 'recent'; c.board();
  assert.equal(cards('done').length, 10);
  assert.equal(button('done', 'more').textContent, '显示更多（还有 2 个）');
  assert.equal(count('done'), '12/30');
});

test('看板：点显示更多、再点、收起；切换日期范围和离开需求页回到 10 张', () => {
  const { c, get, cards, button, count } = renderer();
  c.reqs = reqs(33).concat(reqs(14, 'dropped')); c.board();
  button('done', 'more').events.click();
  assert.equal(cards('done').length, 20);
  assert.equal(button('done', 'more').textContent, '显示更多（还有 13 个）');
  assert.ok(button('done', 'fold'));
  assert.equal(cards('dropped').length, 10); // 两列互不影响
  assert.equal(cards('done').filter((n) => / fresh/.test(n.className)).length, 10); // 新出来的 10 张闪一下
  button('done', 'more').events.click();
  assert.equal(cards('done').length, 30);
  button('done', 'fold').events.click();
  assert.equal(cards('done').length, 10);
  assert.equal(button('done', 'more').textContent, '显示更多（还有 23 个）');
  assert.equal(button('done', 'fold'), undefined);
  assert.equal(count('done'), '33');
  // 已取消：14 张，点一次全部显示，按钮消失，出现收起
  assert.equal(button('dropped', 'more').textContent, '显示更多（还有 4 个）');
  button('dropped', 'more').events.click();
  assert.equal(cards('dropped').length, 14); assert.equal(button('dropped', 'more'), undefined); assert.ok(button('dropped', 'fold'));
  // 切换日期范围
  button('done', 'more').events.click();
  get('board-date-filter').events = {}; c.wireBoard(); get('board-date-filter').events.change();
  assert.equal(cards('done').length, 10); assert.equal(cards('dropped').length, 10);
  // 离开需求页（map.js 的 view 调这个）
  button('done', 'more').events.click(); c.resetBoardPages(); c.board();
  assert.equal(cards('done').length, 10);
});

test('看板：不超过 10 张没有按钮；最近 7 天没有完成的照旧显示空提示', () => {
  const { c, get, cards, row, col } = renderer();
  c.reqs = reqs(10).concat(reqs(3, 'dropped')); c.board();
  assert.equal(cards('done').length, 10); assert.equal(cards('dropped').length, 3);
  assert.equal(row('done'), undefined); assert.equal(row('dropped'), undefined);
  c.reqs = reqs(5, 'done', 10); get('board-date-filter').value = 'recent'; c.board();
  assert.equal(cards('done').length, 0); assert.equal(row('done'), undefined);
  assert.equal(col('done').children.at(-1).textContent, '最近 7 天没有完成的需求');
});

test('map.js 离开需求页时让看板回到 10 张', () => {
  const src = fs.readFileSync(path.join(base, 'scripts', 'map.js'), 'utf8');
  assert.match(src, /if \(name !== 'reqs'\) resetBoardPages\(\);/);
});
