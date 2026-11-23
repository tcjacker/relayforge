// 测试集：测试页（测试集管理）、需求面板里的两块（「这个需求改了以前的数据」和「测试集」）、确认完成前的提示、改挂靠和标成过期的小对话框。
// 数据由主进程 testsets.js 调用 rf.py test 给出；怎么分组、排序、筛选、算比例在 tests-model.js。查看器在 test-viewer.js。
var TM = TestsModel, tesc = TM.esc;
var TS = {
  rows: [], loaded: false, error: '', f: 'need', features: [],
  panel: {},     // 需求编号 -> rf.py test list --req 的结果（缓存，文件一变就重读）
  asking: {},    // 需求编号 -> 正在读
  running: {},   // 'all' | 'req:R' | 'set:TS' -> true：正在跑（按钮显示「正在跑…」）
  rejected: [],  // 这次打开应用以来你不同意过的改动（后端已经丢弃了，这里记着好显示结果）
  errors: {},    // 需求编号 -> 上一次操作的错误
  handled: {},   // 测试集编号 -> 「让 AI 来处理」交给了谁 { title, run, at }（只记在这次打开的应用里）
};

// ---------- 一张改动卡片（需求面板和确认完成前的对话框共用） ----------
function tsChangeHtml(c, state) {
  var sides = TM.changeSides(c);
  var h = '<div class="ts-chg' + (state === 'ok' ? ' done' : state === 'no' ? ' no' : '') + '"><b>' + tesc(c.name || '#' + c.case) + '</b> <span class="mono muted">' + tesc(c.set) + ' #' + c.case + ' · ' + TM.opText(c) + '</span>'
    + uiText('<div class="ov"><div><span>原来的期望</span><pre>', "<div class=\"ov\"><div><span>Original expectation</span><pre>") + tesc(sides.old) + uiText('</pre></div><div><span>改成</span><pre>', "</pre></div><div><span>Proposed expectation</span><pre>") + tesc(sides.now) + '</pre></div></div>'
    + (sides.inputChanged ? uiText('<div class="why muted">输入也改了，见查看器。</div>', "<div class=\"why muted\">Input also changed. See the test set viewer.</div>") : '')
    + uiText('<div class="why">AI 的理由：', "<div class=\"why\">AI's reason: ") + tesc(c.why) + '</div><div class="ts-acts">';
  if (state === 'ok') h += uiText('<span class="ts-pill g">你同意了 · 随需求完成落定</span>', "<span class=\"ts-pill g\">Approved · Applied at requirement completion</span>");
  else if (state === 'no') h += uiText('<span class="ts-pill r">你不同意 · 已交回开发会话，期望保持原样</span>', "<span class=\"ts-pill r\">Declined · Sent to development with the original expectation</span>");
  else h += '<button type="button" class="sm primary" data-chg="approve" data-set="' + tesc(c.set) + '" data-k="' + c.id + uiText('">同意改</button><button type="button" class="sm ts-btn" data-chg="reject" data-set="', "\">Approve change</button><button type=\"button\" class=\"sm ts-btn\" data-chg=\"reject\" data-set=\"") + tesc(c.set) + '" data-k="' + c.id + uiText('">不同意，这是改坏了</button>', "\">Reject regression</button>");
  return h + '</div></div>';
}
// 同意或不同意一条改动；不同意时主进程把话交回提出它的开发会话
function tsDecide(btn, done) {
  var set = btn.dataset.set, k = Number(btn.dataset.k), action = btn.dataset.chg;
  var r = byId(current), list = (r && TS.panel[r.id]) || {};
  var change = null;
  (list.sets || []).forEach(function (s) { (s.pending_changes || []).forEach(function (c) { if (s.id === set && c.id === k) change = Object.assign({ set: set }, c); }); });
  btn.disabled = true;
  return window.shell.testsAct({ action: action, id: set, change: change ? (action === 'reject' ? change : change.id) : k }).then(function (res) {
    if (res && res.error) { if (done) done(res.error); else { TS.errors[current] = res.error; panelTests(); } return res; }
    if (action === 'reject' && change) TS.rejected.push(change);
    if (done) done(); else tsReloadPanel(current);
    return res;
  });
}

// ---------- 需求面板 ----------
function tsReloadPanel(id) {
  if (!id || TS.asking[id]) return;
  TS.asking[id] = true;
  window.shell.testsList({ req: id }).then(function (list) {
    TS.asking[id] = false;
    TS.panel[id] = list;
    if (id === current) panel(); // 验收条件下面的用例结果（cases.js）也读这一份
  }, function () { TS.asking[id] = false; });
}
function tsCard(s, ctx) {
  var c = TM.counts(s);
  var h = '<button type="button" class="ts-card" data-open="' + tesc(s.id) + '"><b>' + tesc(s.title) + ' <span class="mono">' + tesc(s.id) + '</span>' + (s.origin === 'existing' && ctx === 'going' ? uiText(' <span class="ts-pill">以前就有的</span>', " <span class=\"ts-pill\">Existing</span>") : '') + '</b>'
    + TM.barHtml(c) + '<span class="ts-pills">' + TM.pillsHtml(c) + uiText('<span class="small muted">共 ', "<span class=\"small muted\">") + s.cases + uiText(' 条</span>', " total</span>")
    + (s.unseen ? uiText('<span class="ts-pill a">AI 新加 ', "<span class=\"ts-pill a\">") + s.unseen + uiText(' 条没看过</span>', " new AI cases to review</span>") : '') + '</span></button>';
  if (c.fail + c.broken) {
    h += uiText('<div class="ts-analysis"><span>分析', "<div class=\"ts-analysis\"><span>Analysis") + (s.analysis ? uiText('（AI 写的）', " (AI)") : '') + '</span><div>' + (s.analysis ? tesc(s.analysis.text) : c.broken ? uiText('测试代码跑不起来，一条都没有执行。打开看错误。', "The test code couldn't run, so all cases were skipped. Open the test set to see the error.") : uiText('还没有写分析。', "No analysis yet.")) + '</div></div>';
  }
  if (TM.isBroken(s)) h += '<div class="ts-acts">' + tsHandleBtn(s.id) + '</div>';
  return h;
}
// 放在「验收条件」那一节后面、「完成记录」前面；没有验收条件时放在需求内容的最后
function tsPlace(box) {
  var body = $('req-body'), heads = Array.prototype.slice.call(body.querySelectorAll('h2'));
  var check = heads.filter(function (h) { return h.dataset.section === '验收条件'; })[0];
  var record = heads.filter(function (h) { return h.dataset.section === '变更记录'; })[0];
  if (check && check.nextSibling) body.insertBefore(box, check.nextSibling.nextSibling);
  else if (record) body.insertBefore(box, record);
  else body.appendChild(box);
}
function panelTests() {
  var r = byId(current);
  var box = $('req-tests');
  if (!box) { box = el('div'); box.id = 'req-tests'; box.addEventListener('click', tsPanelClick); }
  if (!r || r.loose || (r.state !== 'open' && r.state !== 'done')) { box.remove(); return; }
  if (TS.shownFor !== r.id) { TS.shownFor = r.id; tsReloadPanel(r.id); } // 换到这个需求时重读一次，先用缓存画
  if (!TS.panel[r.id]) { box.remove(); return; }
  var m = TM.panel(TS.panel[r.id], r, TS.rejected);
  if (!m.visible && TM.manualOnly(TS.panel[r.id], r)) { // 全靠人工验收、没有测试集：说一句，不空着
    tsPlace(box);
    return tsPaint(box, uiText('<h2>测试集</h2><p class="note">这个需求靠人工验收，没有测试集。', "<h2>Test sets</h2><p class=\"note\">This requirement uses manual acceptance. ") + (r.testing && r.testing.all_ui ? uiText('测试会话把验收条件都归为「看界面」，', "The test session classified every criterion as a visual check. ") : '') + uiText('检查步骤在上面每条验收条件下面。</p>', "See the steps under each criterion above.</p>"));
  }
  if (!m.visible) { box.remove(); return; }
  tsPlace(box);
  var h = '';
  if (m.done) {
    h = uiText('<h2>测试集</h2><details class="ts-calm"><summary>', "<h2>Test sets</h2><details class=\"ts-calm\"><summary>") + tesc(m.doneLine) + '</summary>' + m.hot.concat(m.calm).map(function (s) { return tsCard(s, 'done'); }).join('') + '</details>';
    return tsPaint(box, h);
  }
  if (m.pending.length || m.approved.length || m.rejected.length) {
    h += uiText('<h2>这个需求改了以前的数据 <span class="n">', "<h2>Changes to existing test data <span class=\"n\">") + (m.pending.length ? m.pending.length + uiText(' 条等你确认', " awaiting approval") : uiText('都确认过了', "All reviewed")) + '</span></h2>'
      + uiText('<p class="note">新加的数据不用确认。AI 改动已经存在的数据时，程序会单独列在这里，防止测试被悄悄改成永远通过。</p>', "<p class=\"note\">Changes to existing data appear here for review. New cases can be added without approval.</p>")
      + m.pending.map(function (c) { return tsChangeHtml(c, ''); }).join('') + m.approved.map(function (c) { return tsChangeHtml(c, 'ok'); }).join('')
      + m.rejected.map(function (c) { return tsChangeHtml(c, 'no'); }).join('');
  }
  if (m.totals.sets) {
    var running = !!TS.running['req:' + r.id];
    h += uiText('<h2>测试集 <span class="n">', "<h2>Test sets <span class=\"n\">") + tesc(m.head) + '</span></h2>' + m.hot.map(function (s) { return tsCard(s, 'going'); }).join('');
    if (m.calm.length) h += '<details class="ts-calm"><summary>' + (m.hot.length ? uiText('另有 ', "") : '') + m.calm.length + uiText(' 个测试集全部通过（共 ', " other test sets passed (") + m.calmCases + uiText(' 条）</summary>', " cases)</summary>") + m.calm.map(function (s) { return tsCard(s, 'going'); }).join('') + '</details>';
    h += '<div class="ts-acts ts-envrow">' + envSelect() + '</div><div class="ts-acts"><button type="button" class="ts-btn" data-ts="runreq"' + (running ? ' disabled' : '') + '>' + (running ? uiText('正在跑…', "Running…") : uiText('全部重跑', "Run all again")) + '</button>'
      + uiText('<span class="small muted">合计 ', "<span class=\"small muted\">Total: ") + TM.resultText(m.totals) + '</span></div>'
      + uiText('<p class="note">分析是 AI 写的；通过或失败是程序比对出来的。</p>', "<p class=\"note\">AI writes the analysis. RelayForge compares the results with the expected output.</p>");
  }
  if (TS.errors[r.id]) h += '<p class="ts-err">' + tesc(TS.errors[r.id]) + '</p>';
  tsPaint(box, h);
}
// 面板经常重画（会话进展一变就画）：内容没变就不动它，展开着的「全部通过」也就不会收起来
function tsPaint(box, h) {
  if (box.dataset.for === current && box.tsHtml === h) return;
  var open = box.dataset.for === current && !!box.querySelector('details.ts-calm[open]');
  box.innerHTML = h; box.tsHtml = h; box.dataset.for = current;
  var d = box.querySelector('details.ts-calm');
  if (d && open) d.open = true;
}
function tsPanelClick(e) {
  var b = e.target.closest('button');
  if (!b) return;
  var r = byId(current);
  if (b.dataset.open) return openTestViewer(b.dataset.open, r && r.state === 'open' ? { req: r.id } : {});
  if (b.dataset.tshandle) return tsAiHandle(b.dataset.tshandle, r && r.state === 'open' ? { req: r.id } : {}, function (err) { if (err) TS.errors[r.id] = err; else delete TS.errors[r.id]; panelTests(); });
  if (b.dataset.chg) { delete TS.errors[current]; return tsDecide(b); }
  if (b.dataset.ts === 'runreq' && r) tsRun('req:' + r.id, { req: r.id }, function (res) { if (res && res.error) TS.errors[r.id] = res.error; else delete TS.errors[r.id]; tsReloadPanel(r.id); });
}
// 运行：主进程调用 rf.py test run（不经过模型）；同一样东西跑着的时候不再点。
// 测试环境里有第一次要执行（或命令改了）的准备步骤时，主进程不跑，先交回来让你看命令、点同意；同意了再跑，不同意就不跑
function tsRun(key, request, then) {
  if (TS.running[key]) return;
  TS.running[key] = true; tsRefreshViews();
  window.shell.testsRun(request).then(function (res) {
    delete TS.running[key];
    if (res && res.needApproval) {
      tsRefreshViews();
      return envApproveDialog(res.env, res.needApproval, function () { tsRun(key, request, then); },
        function () { if (then) then({ error: uiText('没有运行：测试环境里的准备步骤还没同意执行。', "Tests did not run because test environment preparation still needs approval."), declined: true }); tsRefreshViews(); });
    }
    if (then) then(res);
    tsRefreshViews();
  });
}
// 「让 AI 来处理」按钮和它交给了谁
function tsHandleBtn(id) {
  var hd = TS.handled[id];
  return '<button type="button" class="sm ts-btn" data-tshandle="' + tesc(id) + uiText('">让 AI 来处理</button>', "\">Ask AI to investigate</button>")
    + (hd ? uiText('<span class="small muted">已交给', "<span class=\"small muted\">Sent to ") + tesc(hd.title) + '（' + tesc(new Date(hd.at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })) + uiText('），处理完点「再跑一次」</span>', "). Run again when ready</span>") : '');
}
// 测试集跑不起来：把程序写好的情况交给一个会话（进行中的需求交给它的开发会话，否则新开一个聊天，不切过去）
function tsAiHandle(id, ctx, done) {
  window.shell.envsHandle(Object.assign({ kind: 'set', ref: id }, ctx || {})).then(function (res) {
    if (!res || res.error) return done && done((res && res.error) || uiText('没能交出去。', "Couldn't send."));
    if (res.kind === 'chat') tsChatInBackground(res.sessionId, res.title, res.say);
    TS.handled[id] = { title: res.kind === 'chat' ? uiText('新开的聊天「', "New chat “") + res.title + '」' : res.title, run: res.run, at: new Date().toISOString() };
    if (done) done(null, res);
  });
}
function tsRefreshViews() { if (!$('tests').hidden) renderTests(); panelTests(); if (typeof renderViewer === 'function') renderViewer(); }

// ---------- 测试页：测试集管理 ----------
function loadTests() {
  return window.shell.testsList({}).then(function (list) {
    TS.loaded = true;
    if (list && list.error) { TS.error = list.error; } else { TS.error = ''; TS.rows = list.sets || []; TS.full = list.full || null; }
    renderTests();
  });
}
function showTests() {
  showTestsTab(EV.tab);
  loadTests();
}
function renderTests() {
  var counts = TM.filterCounts(TS.rows), at = TM.lastRunAt(TS.rows);
  var where = uiText('对着主线（项目目录现在的代码）', "Running against the main checkout") + (at ? uiText(' · 上次运行 ', " · Last run ") + at.slice(5, 16) : '');
  $('ts-where').dataset.sets = where;
  if (EV.tab === 'sets') $('ts-where').textContent = where;
  var running = !!TS.running.all;
  $('ts-runall').disabled = running; $('ts-runall').textContent = running ? uiText('正在跑…', "Running…") : uiText('整套跑一遍', "Run all");
  $('ts-filters').innerHTML = TM.FILTERS.map(function (x) { return '<button type="button" class="sm" data-f="' + x[0] + '" aria-pressed="' + (TS.f === x[0]) + '">' + x[1] + ' ' + counts[x[0]] + '</button>'; }).join('');
  $('ts-error').hidden = !TS.error; $('ts-error').textContent = TS.error;
  var full = TM.fullLine(TS.full); // 上次全量：合并前的检查只跑一部分，剩下的靠它
  $('ts-full').textContent = full; $('ts-full').hidden = !full; $('ts-full').classList.toggle('warn', !!(TS.full && (TS.full.stale || (TS.full.last && (TS.full.last.failing || []).length))));
  var gs = TM.groups(TS.rows, TS.f);
  var h = uiText('<thead><tr><th>测试集</th><th>挂在哪个需求</th><th>功能点</th><th>数据</th><th>上次结果</th><th>提示</th><th></th></tr></thead><tbody>', "<thead><tr><th>Test set</th><th>Requirement</th><th>Feature</th><th>Cases</th><th>Last result</th><th>Notices</th><th></th></tr></thead><tbody>");
  gs.forEach(function (g) {
    h += '<tr class="grp"><td colspan="7"><b>' + tesc(g.feature + ' ' + g.title) + '</b> <span class="small muted">' + g.rows.length + uiText(' 个测试集 · ', " test sets · ") + g.cases + uiText(' 条数据</span></td></tr>', " cases</span></td></tr>");
    g.rows.forEach(function (s) {
      var c = TM.counts(s), lab = TM.reqLabel(s, reqs);
      var hints = (s.hints || []).map(function (x) { return tesc(x.text); });
      if (s.pending_changes && s.pending_changes.length) hints.push(s.pending_changes.length + uiText(' 处改动等你确认', " changes awaiting approval"));
      if (s.stale) hints.unshift(uiText('过期：', "Obsolete: ") + tesc(s.stale_reason || ''));
      h += '<tr class="row" data-open="' + tesc(s.id) + '"><td><b>' + tesc(s.title) + '</b><span class="sub mono">' + tesc(s.id) + '</span></td>'
        + '<td>' + tesc(lab.title) + '<span class="sub">' + (lab.going ? '<span class="going">' + tesc(lab.sub) + '</span>' : tesc(lab.sub)) + '</span></td>'
        + '<td>' + tesc(s.feature) + '<span class="sub">' + tesc(s.feature_title) + '</span></td>'
        + '<td class="n">' + s.cases + uiText(' 条</td>', "</td>")
        + '<td class="res"><span class="ts-pills">' + TM.pillsHtml(c) + '</span>' + TM.barHtml(c) + '</td>'
        + '<td class="hint">' + (hints.length ? hints.join('<br>') : '<span class="none">—</span>') + '</td>'
        + '<td class="ops">' + (s.stale ? uiText('<span class="ts-pill">过期 · 不再运行</span> <button type="button" class="sm quiet" data-act="revive" data-id="', "<span class=\"ts-pill\">Obsolete · Skipped</span> <button type=\"button\" class=\"sm quiet\" data-act=\"revive\" data-id=\"") + tesc(s.id) + uiText('">恢复</button>', "\">Restore</button>")
          : (TM.isBroken(s) ? '<button type="button" class="sm ts-btn" data-act="handle" data-id="' + tesc(s.id) + uiText('">让 AI 来处理</button> ', "\">Ask AI to investigate</button> ") : '')
            + '<button type="button" class="sm" data-act="move" data-id="' + tesc(s.id) + uiText('">改挂靠</button> <button type="button" class="sm quiet" data-act="stale" data-id="', "\">Change links</button> <button type=\"button\" class=\"sm quiet\" data-act=\"stale\" data-id=\"") + tesc(s.id) + uiText('">标成过期</button>', "\">Mark obsolete</button>"))
        + (TS.handled[s.id] ? uiText('<span class="sub" title="已交给', "<span class=\"sub\" title=\"Sent to ") + tesc(TS.handled[s.id].title) + uiText('">已交给 AI 处理，处理完再跑一次</span>', "\">Sent to AI. Run again when ready</span>") : '') + '</td></tr>';
    });
  });
  if (!gs.length) h += '<tr><td colspan="7" class="ts-empty">' + (!TS.loaded ? uiText('正在读…', "Loading…") : !TS.rows.length ? uiText('还没有测试集。需求进入验证时，开发会话会按验收条件生成测试集。', "No test sets yet. Test sets are added when a requirement enters testing.") : TS.f === 'need' ? uiText('没有需要看的：都通过了，也没有提示和等你确认的改动。', "All tests passed. There are no notices or pending changes.") : uiText('没有符合的测试集。', "No matching test sets.")) + '</td></tr>';
  $('ts-table').innerHTML = h + '</tbody>';
}
function tsRowOf(id) { return TS.rows.filter(function (s) { return s.id === id; })[0]; }
function testsClick(e) {
  var b = e.target.closest('button');
  if (b && b.dataset.f) { TS.f = b.dataset.f; return renderTests(); }
  if (b && b.id === 'ts-runall') return tsRun('all', { all: true }, function (res) { TS.error = res && res.error ? res.error : ''; if (res && res.declined) renderTests(); else loadTests(); });
  if (b && b.dataset.act) {
    var s = tsRowOf(b.dataset.id);
    if (!s) return;
    if (b.dataset.act === 'revive') return tsAct({ action: 'revive', id: s.id });
    if (b.dataset.act === 'stale') return tsStaleDialog(s);
    if (b.dataset.act === 'move') return tsMoveDialog(s);
    if (b.dataset.act === 'handle') return tsAiHandle(s.id, {}, function (err) { TS.error = err || ''; renderTests(); });
    return;
  }
  var row = e.target.closest('tr.row');
  if (row) openTestViewer(row.dataset.open, {});
}
function tsAct(request) {
  return window.shell.testsAct(request).then(function (res) {
    if (res && res.error) { TS.error = res.error; renderTests(); return res; }
    TS.error = ''; loadTests(); return res;
  });
}

// 标成过期：写一句原因（可以不写）
function tsStaleDialog(s) {
  var d = $('ts-dlg');
  d.innerHTML = uiText('<h2>把「', "<h2>Mark “") + tesc(s.title) + uiText('」标成过期？</h2><p>过期的测试集不再运行、不删除，在「过期」里能找到，随时可以恢复。</p>', "” obsolete?</h2><p>Obsolete test sets stop running. Find and restore them in the Obsolete filter.</p>")
    + uiText('<label>为什么（可以不写）<input id="ts-dlg-reason" type="text" placeholder="比如：登录改成了短信验证码"></label><p class="ts-err" id="ts-dlg-err" hidden></p>', "<label>Reason (optional)<input id=\"ts-dlg-reason\" type=\"text\" placeholder=\"Example: sign-in now uses a verification code\"></label><p class=\"ts-err\" id=\"ts-dlg-err\" hidden></p>")
    + uiText('<div class="ts-row"><button type="button" class="quiet" data-x="no">取消</button><button type="button" class="primary" data-x="yes">标成过期</button></div>', "<div class=\"ts-row\"><button type=\"button\" class=\"quiet\" data-x=\"no\">Cancel</button><button type=\"button\" class=\"primary\" data-x=\"yes\">Mark obsolete</button></div>");
  d.onclick = function (e) {
    var b = e.target.closest('button'); if (!b) return;
    if (b.dataset.x === 'no') return d.close();
    if (b.dataset.x === 'yes') { b.disabled = true; window.shell.testsAct({ action: 'stale', id: s.id, reason: $('ts-dlg-reason').value.trim() }).then(function (res) { b.disabled = false; if (res && res.error) { $('ts-dlg-err').textContent = res.error; $('ts-dlg-err').hidden = false; return; } d.close(); loadTests(); }); }
  };
  d.showModal(); $('ts-dlg-reason').focus();
}
// 改挂靠：换功能点、换需求（进行中的需求新建的测试集跟着它走，不能改需求）
function tsMoveDialog(s) {
  var d = $('ts-dlg');
  var draw = function () {
    var feats = TS.features.length ? TS.features : [{ id: s.feature, title: s.feature_title }];
    var opts = reqs.filter(function (r) { return r.state !== 'dropped' && r.state !== 'draft'; });
    var now = s.requirement || '';
    d.innerHTML = uiText('<h2>改挂靠：', "<h2>Change links: ") + tesc(s.title) + uiText('</h2><p>测试集同时挂在一个功能点和一个需求下面；需求完成以后它留在功能点下面。</p>', "</h2><p>Link a test set to a feature and a requirement. It stays with the feature after the requirement is complete.</p>")
      + uiText('<label>功能点<select id="ts-dlg-feat">', "<label>Feature<select id=\"ts-dlg-feat\">") + feats.map(function (f) { return '<option value="' + tesc(f.id) + '"' + (f.id === s.feature ? ' selected' : '') + '>' + tesc(f.id + ' ' + (f.title || '')) + '</option>'; }).join('') + '</select></label>'
      + uiText('<label>需求<select id="ts-dlg-req"', "<label>Requirement<select id=\"ts-dlg-req\"") + (s.in_progress ? ' disabled' : '') + '><option value="none"' + (!now ? ' selected' : '') + uiText('>不挂需求</option>', ">No requirement</option>")
      + opts.map(function (r) { return '<option value="' + tesc(r.id) + '"' + (r.id === now ? ' selected' : '') + '>' + tesc(r.title + '（' + r.id + '）') + '</option>'; }).join('')
      + (now && !opts.some(function (r) { return r.id === now; }) ? '<option value="' + tesc(now) + '" selected>' + tesc(now) + '</option>' : '') + '</select></label>'
      + (s.in_progress ? uiText('<p>它是 ', "<p>This set was added by ") + tesc(s.in_progress) + uiText(' 进行中新建的，跟着这个需求走；需求完成以后才能改挂到别的需求。</p>', ". Its requirement link can change after that requirement completes.</p>") : '')
      + uiText('<p class="ts-err" id="ts-dlg-err" hidden></p><div class="ts-row"><button type="button" class="quiet" data-x="no">取消</button><button type="button" class="primary" data-x="yes">保存</button></div>', "<p class=\"ts-err\" id=\"ts-dlg-err\" hidden></p><div class=\"ts-row\"><button type=\"button\" class=\"quiet\" data-x=\"no\">Cancel</button><button type=\"button\" class=\"primary\" data-x=\"yes\">Save</button></div>");
  };
  draw();
  if (!TS.features.length) window.shell.listIssues().then(function (found) { if (found && found.features && found.features.length) { TS.features = found.features; if (d.open) draw(); } });
  d.onclick = function (e) {
    var b = e.target.closest('button'); if (!b) return;
    if (b.dataset.x === 'no') return d.close();
    if (b.dataset.x !== 'yes') return;
    var feature = $('ts-dlg-feat').value, req = $('ts-dlg-req').disabled ? '' : $('ts-dlg-req').value;
    var request = { action: 'move', id: s.id };
    if (feature && feature !== s.feature) request.feature = feature;
    if (req && req !== (s.requirement || 'none')) request.req = req;
    if (!request.feature && !request.req) return d.close();
    b.disabled = true;
    window.shell.testsAct(request).then(function (res) { b.disabled = false; if (res && res.error) { $('ts-dlg-err').textContent = res.error; $('ts-dlg-err').hidden = false; return; } d.close(); loadTests(); });
  };
  d.showModal();
}

// ---------- 确认完成前 ----------
// 有测试集的需求：先跑一遍。全部通过、也没有等你确认的改动，就照旧走 proceed；有失败或跑不起来只提示；
// 还有没确认的改动要你在这里逐条处理完，才能接着确认完成
function testsBeforeDone(r, proceed, busy) {
  if (!r || TS.running['done:' + r.id]) return;
  TS.running['done:' + r.id] = true;
  if (busy) busy(true);
  window.shell.testsBeforeDone(r.id).then(function (res) {
    delete TS.running['done:' + r.id];
    if (busy) busy(false);
    TS.panel[r.id] = null; tsReloadPanel(r.id);
    if (res && res.needApproval) return envApproveDialog(res.env, res.needApproval, function () { testsBeforeDone(r, proceed, busy); });
    var cases = CasesModel.pending(byId(r.id) || r); // 待确认的用例改动：和没确认的数据改动一样，要在这里处理完
    if ((!res || res.ok) && !cases.length) return proceed();
    tsDoneDialog(r, Object.assign({ failing: [], pending: [], totals: null }, res && !res.ok ? res : {}), proceed, cases);
  });
}
function tsDoneDialog(r, res, proceed, cases) {
  var d = $('ts-done'), decided = {}, err = '';
  cases = cases || [];
  var draw = function () {
    var left = res.pending.filter(function (c) { return !decided[c.set + '#' + c.id]; }).concat(cases.filter(function (c) { return !decided['uc#' + c.k]; }));
    var h = uiText('<h2>确认「', "<h2>Before marking “") + tesc(r.title) + uiText('」完成之前</h2>', "” complete</h2>");
    if (res.error) h += '<p class="ts-err">' + tesc(res.error) + '</p>';
    if (res.failing.length) {
      h += uiText('<h3>刚跑了一遍测试集，有 ', "<h3>The test run found ") + res.failing.length + uiText(' 个没通过</h3><ul>', " unsuccessful test sets</h3><ul>") + res.failing.map(function (f) {
        return '<li><button type="button" class="link" data-open="' + tesc(f.id) + '">' + tesc(f.id + ' ' + f.title) + '</button>：' + tesc(TM.failingText(f)) + (f.origin === 'existing' ? uiText('（以前就有的）', " (existing)") : '') + '</li>';
      }).join('') + uiText('</ul><p>有失败不拦着确认完成，完成记录里会写上这次的结果。</p>', "</ul><p>You can still mark the requirement complete. The completion record will include these results.</p>");
    } else if (res.totals && res.totals.sets) {
      h += uiText('<p>刚跑了一遍测试集：', "<p>All tests passed: ") + res.totals.sets + uiText(' 个、', " test sets and ") + res.totals.total + uiText(' 条，全部通过。</p>', " cases.</p>");
    }
    if (res.pending.length) {
      h += '<h3>' + (left.length ? uiText('还有 ', "Review ") + left.length + uiText(' 处改动没确认，处理完才能确认完成', " pending changes before marking complete") : res.pending.length + uiText(' 处改动都处理好了', " changes reviewed")) + uiText('</h3><p>需求完成时不能留下没确认的改动：AI 改了以前就有的数据的期望，同意就随需求落定，不同意就丢弃并交回开发会话。</p>', "</h3><p>Review changes to existing data before marking complete. Approved changes apply at completion. Declined changes go back to the development session.</p>")
        + res.pending.map(function (c) { return tsChangeHtml(c, decided[c.set + '#' + c.id] || ''); }).join('');
    }
    if (cases.length) {
      var ucLeft = cases.filter(function (c) { return !decided['uc#' + c.k]; }).length;
      h += '<h3>' + (ucLeft ? uiText('还有 ', "Review ") + ucLeft + uiText(' 条用例改动没确认，处理完才能确认完成', " pending case changes before marking complete") : cases.length + uiText(' 条用例改动都处理好了', " case changes reviewed")) + uiText('</h3><p>会话在立项以后提的改、删、加用例和改归类，同意就生效，不同意就丢弃并交回提出它的会话。</p>', "</h3><p>Review the additions, edits, removals, and classifications proposed after planning. Declined changes go back to the session that proposed them.</p>")
        + cases.map(function (c) { var st = decided['uc#' + c.k]; return ucChangeHtml(Object.assign({}, c, { state: st === 'ok' ? 'approved' : st === 'no' ? 'rejected' : 'pending' }), r); }).join('');
    }
    if (err) h += '<p class="ts-err">' + tesc(err) + '</p>';
    h += '<div class="ts-row">' + (left.length ? uiText('<span class="wait">还有 ', "<span class=\"wait\">") + left.length + uiText(' 处改动要你处理</span>', " changes still need your review</span>") : '')
      + uiText('<button type="button" class="quiet" data-x="no">取消</button><button type="button" class="primary" data-x="yes"', "<button type=\"button\" class=\"quiet\" data-x=\"no\">Cancel</button><button type=\"button\" class=\"primary\" data-x=\"yes\"") + (left.length ? ' disabled' : '') + '>' + (res.failing.length || res.error ? uiText('仍然确认完成', "Mark complete anyway") : uiText('确认完成', "Mark complete")) + '</button></div>';
    d.innerHTML = h;
  };
  d.onclick = function (e) {
    var b = e.target.closest('button'); if (!b) return;
    if (b.dataset.open) return openTestViewer(b.dataset.open, { req: r.id });
    if (b.dataset.x === 'no') return d.close();
    if (b.dataset.x === 'yes') { if (res.pending.some(function (c) { return !decided[c.set + '#' + c.id]; }) || cases.some(function (c) { return !decided['uc#' + c.k]; })) return; d.close(); return proceed(); }
    if (b.dataset.ucd) {
      var k = Number(b.dataset.k), ok = b.dataset.ucd === 'ok';
      b.disabled = true; err = '';
      return ucDecide(r.id, k, ok, function (e2) { if (e2) err = e2; else decided['uc#' + k] = ok ? 'ok' : 'no'; draw(); });
    }
    if (b.dataset.chg) {
      var key = b.dataset.set + '#' + b.dataset.k, c = res.pending.filter(function (x) { return x.set + '#' + x.id === key; })[0];
      b.disabled = true; err = '';
      window.shell.testsAct({ action: b.dataset.chg, id: b.dataset.set, change: b.dataset.chg === 'reject' ? c : Number(b.dataset.k) }).then(function (out) {
        if (out && out.error) { err = out.error; draw(); return; }
        decided[key] = b.dataset.chg === 'approve' ? 'ok' : 'no';
        if (b.dataset.chg === 'reject' && c) TS.rejected.push(c);
        tsReloadPanel(r.id); draw();
      });
    }
  };
  draw();
  d.showModal();
}

// ---------- 交给会话的话 ----------
// 新开一个日常聊天，不切过去：话交给它，查看器里显示「已交给…」和「去那个会话看」
function tsChatInBackground(sid, title, say) {
  var key = 'session:' + sid, now = new Date(), pad = function (n) { return (n < 10 ? '0' : '') + n; };
  past.unshift({ session_id: sid, blank: true, title: title || uiText('新聊天', "New chat"), first: '', updated: now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate()) + ' ' + pad(now.getHours()) + ':' + pad(now.getMinutes()) });
  terminal(key, say); show(current); list();
}
function tsGoTo(run) {
  if (!run) return;
  if (run.indexOf('session:') === 0) { view('chats'); select(run); return; }
  if (run.indexOf('fix:') === 0) {
    var issue = (IS.issues || []).filter(function (i) { return i.fix && 'fix:' + i.fix.id === run; })[0];
    view('issues', issue ? issue.id : undefined); return;
  }
  view('reqs'); select(run);
}

// 绑定事件、开始加载：由 main.js 按顺序调用
function wireTests() {
  $('view-tests').addEventListener('click', function () { view('tests'); });
  $('tests').addEventListener('click', testsClick);
  window.shell.onTestsSay(function (p) { sayTo(p.id, p.say); });
  // 需求进了测试中、应用开出了测试会话：你正看着这个需求，就切到「测试」标签
  if (window.shell.onTestingKicked) window.shell.onTestingKicked(function (p) { var r = byId(p.id); if (r && current === p.id && tab === 'reqs') setTimeout(function () { openTab(byId(p.id) || r, 'test'); }, 300); });
  // .relayforge/tests/ 变了：测试页、需求面板、开着的查看器都重读
  window.shell.onTestsChanged(function () {
    if (!$('tests').hidden) loadTests();
    Object.keys(TS.panel).forEach(function (id) { if (id !== current) delete TS.panel[id]; });
    if (current && byId(current)) tsReloadPanel(current);
    if (typeof reloadViewer === 'function') reloadViewer();
  });
}
