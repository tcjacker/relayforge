// 看板：需求按所处的阶段分列。阶段由程序从需求文件和会话进展里算出（r.stage）；也可以右击或把卡片拖到别的栏手动改，见 stage-menu.js
var STAGES = [['talk', uiText('立项中', "Planning"), uiText('还在把需求聊清楚，等你确认立项', "Clarify the requirement and approve development")], ['build', uiText('进行中', "In development"), uiText('已经立项，正在做', "Approved and in development")], ['test', uiText('测试中', "Testing"), uiText('开发做完了，测试会话按验收条件在测', "Development is complete. The testing session is checking the acceptance criteria.")], ['check', uiText('待验收', "Ready for acceptance"), uiText('测试会话说可以验收了（或跳过了测试），等你试', "Test results are in or testing was skipped. Try the changes yourself.")], ['done', uiText('已完成', "Completed"), uiText('你确认过，已合并进主线', "Accepted and merged into the main checkout")], ['dropped', uiText('已取消', "Cancelled"), uiText('已取消，可查看内容并恢复原状态', "View the requirement or restore its previous status")]];
var reqFilter = { talk: true, build: true, test: true, check: true, done: false, dropped: false };
// 需求面板顶上的阶段进度条：立项 › 开发 › 测试 › 验收 › 完成
var STEP_NAMES = [['talk', uiText('立项', "Planning")], ['build', uiText('开发', "Development")], ['test', uiText('测试', "Testing")], ['check', uiText('验收', "Acceptance")], ['done', uiText('完成', "Complete")]];
var boardDateFields = { talk: 'created', done: 'completed_on', dropped: 'dropped_on' };
// 已完成、已取消两列现在各显示几张（board-page-model.js）。不存盘：重开应用、切换日期范围、离开需求页都回到 10 张；
// 点卡片看详情再回看板保持不变。freshFrom 记下刚点「显示更多」时从第几张起是新出来的，那几张闪一下
var boardShown = BoardPage.fresh(), boardFreshFrom = {};
function resetBoardPages() { boardShown = BoardPage.fresh(); boardFreshFrom = {}; }
function visibleReq(r) { return !!reqFilter[stageOf(r)]; }
function stageOf(r) {
  if (r.stage) return r.stage;
  if (r.state === 'dropped') return 'dropped';
  if (r.state === 'done') return 'done';
  if (r.state === 'draft' || !(r.sections['验收条件'] || []).length) return 'talk'; // 以前没走立项、也没写验收条件的需求也放在这里
  return r.steps && r.done === r.steps ? 'check' : 'build';
}

function localDate(date) {
  return [date.getFullYear(), date.getMonth() + 1, date.getDate()].map(function (part) { return String(part).padStart(2, '0'); }).join('-');
}

function board() {
  var on = tab === 'reqs' && !current;
  document.querySelector('main').classList.toggle('boarding', on);
  $('board').hidden = !on; $('to-board').classList.toggle('on', on);
  if (!on) return;
  var recent = $('board-date-filter').value === 'recent';
  var start = new Date(), today = localDate(start);
  start.setDate(start.getDate() - 6);
  var earliest = localDate(start);
  $('board-columns').replaceChildren.apply($('board-columns'), STAGES.map(function (stage) {
    var mine = reqs.filter(function (r) { return stageOf(r) === stage[0]; });
    var total = mine.length, dateField = boardDateFields[stage[0]];
    var isDone = stage[0] === 'done';
    if (recent && dateField) mine = mine.filter(function (r) {
      var date = (r[dateField] || '').slice(0, 10);
      return date >= earliest && date <= today;
    });
    // 已完成、已取消按日期从新到旧排，只取这一批；列头数字仍用 mine：日期范围筛出来的数量/总数，不因为收起而变小
    var pg = BoardPage.page(stage[0], mine, boardShown[stage[0]]), fresh = boardFreshFrom[stage[0]];
    var col = el('div', 'col ' + stage[0]), head = el('div', 'col-head');
    dropCol(col, stage[0]);
    head.appendChild(el('i', 'dot')); head.appendChild(el('b', '', stage[1]));
    var count = head.appendChild(el('span', 'muted col-count', mine.length < total ? mine.length + '/' + total : String(total)));
    if (dateField) {
      count.title = uiText('显示 ', "Showing ") + mine.length + uiText(' 条，共 ', " of ") + total + uiText(' 条', "");
      count.setAttribute('aria-live', 'polite');
    }
    head.title = stage[2];
    col.appendChild(head);
    pg.cards.forEach(function (r, i) {
      var card = el('div', 'card' + (r.id === menuFor ? ' ctx' : '') + (lockedReq(r) ? ' locked' : '') + (fresh != null && i >= fresh ? ' fresh' : ''));
      card.tabIndex = 0; card.setAttribute('role', 'button');
      card.appendChild(el('b', '', r.title));
      if (r.state === 'draft') card.appendChild(levelRow(r));
      // 前置需求（deps-model.js）：在等谁、前置已取消、可以开始了、有几个需求在等它；鼠标停上去串起前后
      DepsModel.cardNotes(r).forEach(function (n) { card.appendChild(el('small', 'dep ' + n.cls, n.text)).title = n.title; });
      if (DepsModel.waitingOn(r).length) card.classList.add('blocked');
      // 需求之间的冲突（conflicts-model.js）：和主分支或别的需求会冲突、和别的需求改到同一处
      ConflictsModel.cardNotes(conflictsData, r.id).forEach(function (n) { card.appendChild(el('small', 'overlap ' + n.cls, n.text)).title = n.title; });
      card.dataset.id = r.id;
      card.addEventListener('mouseenter', function () { highlightDeps(r); });
      card.addEventListener('mouseleave', clearDepsHighlight);
      var w = waiting[r.id] || waiting['test:' + r.id];
      if (r.state !== 'dropped' && w) card.appendChild(el('small', 'wait ' + w.kind, '● ' + (waiting[r.id] ? '' : uiText('测试会话', "Test session")) + WAIT[w.kind] + '：' + w.text));
      else if (r.state !== 'dropped' && (running[r.id] || running['test:' + r.id])) card.appendChild(el('small', 'run', running[r.id] ? uiText('正在干活', "Working") : uiText('测试会话正在测', "Tests in progress")));
      if (stage[0] === 'build' && r.testing && r.testing.last && r.testing.last.verdict === 'problems' && !r.testing.last.fixed) card.appendChild(el('small', 'wait answer', uiText('测试交回 ', "Testing reported ") + r.testing.last.problems.length + uiText(' 条问题', " issues")));
      if (stage[0] === 'check' && r.testing && r.testing.last) card.appendChild(el('small', r.testing.last.verdict === 'ok' ? 'run' : 'muted', r.testing.last.verdict === 'ok' ? uiText('✓ 测试：可以验收了', "✓ Tests passed. Ready for acceptance.") : uiText('跳过了测试', "Testing skipped")));
      if (merging[r.id]) card.appendChild(el('small', 'merge', '⇄ ' + mergeText(merging[r.id])));
      var doing = stage[0] === 'test' ? (r.testing && r.testing.doing) || '' : r.doing;
      if (doing && stage[0] !== 'done' && stage[0] !== 'dropped') card.appendChild(el('p', '', doing));
      if (r.steps && stage[0] !== 'done' && stage[0] !== 'dropped') { var bar = el('div', 'bar'), fill = el('i'); fill.style.width = Math.round(r.done / r.steps * 100) + '%'; bar.appendChild(fill); card.appendChild(bar); }
      var live = terms[r.id] && !terms[r.id].dead;
      var date = (dateField ? r[dateField] || '' : r.updated || r.created || '').slice(0, 10);
      card.appendChild(el('small', '', [r.steps && !isDone && stage[0] !== 'dropped' ? uiText('计划 ', "Plan ") + r.done + ' / ' + r.steps : '', r.sessions.length ? '' : uiText('还没开始', "Ready to start"), live ? uiText('会话开着', "Session open") : '', date].filter(Boolean).join(' · ')));
      card.addEventListener('click', function () { select(r.id); });
      card.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(r.id); } });
      onContext(card, r); dragCard(card, r);
      col.appendChild(card);
    });
    if (pg.more || pg.fold) col.appendChild(moreRow(stage[0], pg));
    delete boardFreshFrom[stage[0]];
    if (!mine.length) {
      var empty = stage[0] === 'talk' ? uiText('点左边的「＋ 新建需求」，写一句话就能开始', "Select New requirement on the left to get started.") : uiText('没有', "No ");
      if (recent && dateField && (total || isDone)) empty = uiText('最近 7 天没有', "No recent ") + (isDone ? uiText('完成的需求', "completed requirements") : '「' + stage[1] + uiText('」需求', " requirements"));
      col.appendChild(el('p', 'none', empty));
    }
    return col;
  }));
}

// 已完成、已取消列底部：「显示更多（还有 N 个）」每次再多 10 张；展开过以后旁边有「收起」，回到 10 张
function moreRow(stage, pg) {
  var row = el('div', 'more-row');
  if (pg.more) {
    var more = row.appendChild(el('button', 'more', BoardPage.moreText(pg.left)));
    more.type = 'button';
    more.addEventListener('click', function () {
      boardFreshFrom[stage] = pg.cards.length; boardShown = BoardPage.more(boardShown, stage); board();
    });
  }
  if (pg.fold) {
    var fold = row.appendChild(el('button', 'fold', uiText('收起', "Show less")));
    fold.type = 'button'; fold.title = uiText('回到只显示 ', "Show only ") + BoardPage.PAGE + uiText(' 个', "");
    fold.addEventListener('click', function () {
      boardShown = BoardPage.fold(boardShown, stage); board();
      var again = document.querySelector('#board-columns .col.' + stage);
      if (again) again.scrollIntoView({ block: 'nearest' });
    });
  }
  return row;
}

// 鼠标停在一张卡片上：它的前置需求标黄框、在等它的需求标紫框，其余变淡；没有依赖关系的卡片不动
function highlightDeps(r) {
  var rel = DepsModel.related(r);
  if (!rel.pre.length && !rel.post.length) return;
  document.querySelectorAll('#board-columns .card').forEach(function (c) {
    var id = c.dataset.id, pre = rel.pre.indexOf(id) >= 0, post = rel.post.indexOf(id) >= 0;
    c.classList.toggle('dep-focus', id === r.id); c.classList.toggle('dep-pre', pre); c.classList.toggle('dep-post', post);
    c.classList.toggle('dep-fade', id !== r.id && !pre && !post);
  });
}
function clearDepsHighlight() {
  document.querySelectorAll('#board-columns .card').forEach(function (c) { c.classList.remove('dep-focus', 'dep-pre', 'dep-post', 'dep-fade'); });
}

// 绑定事件、开始加载：由 main.js 按顺序调用
function wireBoard() {
  $('board-date-filter').addEventListener('change', function () { resetBoardPages(); board(); });
  $('req-filter').addEventListener('change', function (e) {
    var input = e.target; if (!(input.value in reqFilter)) return;
    reqFilter[input.value] = input.checked;
    if (current && byId(current) && !visibleReq(byId(current))) select(null);
    list(); panel();
  });
  $('to-board').addEventListener('click', function () { if (tab !== 'reqs') view('reqs'); select(null); });
}
