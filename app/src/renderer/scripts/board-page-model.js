// 看板「已完成」「已取消」两列分批显示的 model 层：不碰 DOM，测试（tests/test_app_board_page.cjs）直接在 Node 里调用。
// 卡片多了一列拉得太长，所以先显示 PAGE 张，点「显示更多」每次再多 PAGE 张，展开过以后可以「收起」回到 PAGE 张。
// 列头数字不归这里管：它仍是日期范围筛出来的数量/总数，不因为收起而变小。
var BoardPage = (function () {
  var PAGE = 10;
  var PAGED = { done: 'completed_on', dropped: 'dropped_on' }; // 分批的列，和各自按哪个日期从新到旧排

  // 每列现在要显示几张；不在这里的列按 PAGE 算。切换日期范围、离开需求页、重开应用都会回到这个初始值
  function fresh() { return { done: PAGE, dropped: PAGE }; }

  function paged(stage) { return Object.prototype.hasOwnProperty.call(PAGED, stage); }

  // 按完成（或取消）日期从新到旧排，不改原数组；同一天的保持原来的先后
  function sort(stage, cards) {
    var field = PAGED[stage];
    if (!field) return cards;
    return cards.map(function (r, i) { return { r: r, i: i }; }).sort(function (a, b) {
      return (b.r[field] || '').localeCompare(a.r[field] || '') || a.i - b.i;
    }).map(function (x) { return x.r; });
  }

  // 给定日期筛过的卡片和这列现在要显示几张，算出 { cards 要画的卡片, left 还剩几个（显示更多上的 N）, more 有没有显示更多, fold 有没有收起 }
  function page(stage, cards, shown) {
    var all = sort(stage, cards || []);
    if (!paged(stage)) return { cards: all, left: 0, more: false, fold: false };
    var limit = Math.min(Math.max(shown || PAGE, PAGE), all.length);
    var left = all.length - limit;
    return { cards: all.slice(0, limit), left: left, more: left > 0, fold: limit > PAGE };
  }

  // 点「显示更多」：再多 PAGE 张（不足就全部，由 page 截住）
  function more(state, stage) { var next = Object.assign(fresh(), state); next[stage] = (next[stage] || PAGE) + PAGE; return next; }
  // 点「收起」：回到 PAGE 张，另一列不动
  function fold(state, stage) { var next = Object.assign(fresh(), state); next[stage] = PAGE; return next; }

  function moreText(left) {
    var text = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
    return text('显示更多（还有 {0} 个）', 'Show more ({0} remaining)', left);
  }

  return { PAGE: PAGE, fresh: fresh, paged: paged, sort: sort, page: page, more: more, fold: fold, moreText: moreText };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = BoardPage;
