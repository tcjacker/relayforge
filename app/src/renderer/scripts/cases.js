// 测试用例：需求面板里每条验收条件下面的用例（名称、类别、前提 / 操作 / 预期，可展开）、归类、结果（都通过 / 有失败 / 跑不起来 / 还没对接）、
// 对接的数据（点了跳到测试集查看器里那几条）；「待确认的用例改动」一块（样式和「这个需求改了以前的数据」一样）；你自己加、改、删用例和归类的对话框。
// 写都由 rf.py req case-save / case-ok / case-no 做（只由你在应用里点，程序校验应用的标记）；怎么分组、怎么说在 cases-model.js。
var CM = CasesModel;
var UC = { open: {}, err: {}, dlg: null, form: null };

function ucEditable(r) { return !!r && !r.loose && (r.state === 'draft' || r.state === 'open') && !((states[r.id] || {}).demo); }
function ucReport(r) { return r && r.state !== 'draft' && typeof TS !== 'undefined' && TS.panel[r.id] && TS.panel[r.id].usecases || null; }

// 一条验收条件下面：用例一条一行（可展开），结果和对接的数据；能改的时候带「加用例」「归类」「改」「删」
function caseBlock(li, r, check) {
  var report = ucReport(r), results = !!report;
  var cond = CM.conditions(r, report).filter(function (c) { return c.n === check.n; })[0];
  if (!cond) return;
  var edit = ucEditable(r);
  if (!cond.cases.length && !edit && !cond.uncovered) return;
  if (!cond.cases.length && !edit && r.state === 'done') return;
  var t = r.testing || {};
  var old = r.state !== 'draft' && !t.case_flow && !t.has_cases; // 老需求：立项时还没有用例这一步，不催
  var box = li.appendChild(el('div', 'uc'));
  var head = box.appendChild(el('div', 'uc-head' + (cond.uncovered && !old ? ' warn' : '')));
  head.appendChild(el('span', '', old && !cond.cases.length ? (cond.kind === 'ui' ? CM.condLine(cond, results) : uiText('立项时没有写用例（老需求，测试会话按验收条件测）', "This older requirement has no planning cases. The test session uses the acceptance criteria.")) : CM.condLine(cond, results)));
  if (edit) {
    var add = head.appendChild(el('button', 'link', uiText('加用例', "Add case"))); add.type = 'button'; add.dataset.uc = 'add'; add.dataset.check = cond.n;
    var kind = head.appendChild(el('button', 'link', cond.kind ? uiText('改归类', "Change classification") : uiText('归类', "Classify"))); kind.type = 'button'; kind.dataset.uc = 'kind'; kind.dataset.check = cond.n;
  }
  cond.cases.forEach(function (c) {
    var key = r.id + ':' + c.n;
    var d = box.appendChild(el('details', 'uc-case' + (c.source === 'test' ? ' sup' : '')));
    d.dataset.key = key; if (UC.open[key]) d.open = true;
    d.addEventListener('toggle', function () { if (d.open) UC.open[key] = true; else delete UC.open[key]; });
    var s = d.appendChild(el('summary'));
    if (results) s.appendChild(el('span', 'uc-s ' + (c.status || 'unlinked'), CM.MARK[c.status || 'unlinked']));
    s.appendChild(el('b', '', uiText('用例 ', "Case ") + c.n));
    s.appendChild(el('span', 'uc-name', c.name));
    s.appendChild(el('span', 'ts-pill', TestsModel.categoryName(c.cat)));
    if (c.source === 'test') s.appendChild(el('span', 'ts-pill b', uiText('测试补充的', "Added during testing")));
    if (c.source === 'user') s.appendChild(el('span', 'ts-pill', uiText('你加的', "Added by you")));
    if (results) s.appendChild(el('span', 'ts-pill ' + CM.PILL[c.status || 'unlinked'], CM.STATUS[c.status || 'unlinked']));
    var dl = d.appendChild(el('dl', 'uc-body'));
    [[uiText('前提', "Given"), c.given], [uiText('操作', "When"), c.when], [uiText('预期', "Then"), c.then]].forEach(function (x) { dl.appendChild(el('dt', '', x[0])); dl.appendChild(el('dd', '', x[1])); });
    if (results) {
      var links = d.appendChild(el('div', 'uc-links'));
      if (!c.links.length) links.appendChild(el('span', 'muted small', uiText('还没有对接的测试数据。', "No linked test data yet.")));
      else {
        links.appendChild(el('span', 'muted small', uiText('对接的数据：', "Linked test data: ")));
        c.links.forEach(function (x) {
          var b = links.appendChild(el('button', 'uc-link ' + x.status, CM.MARK[x.status] + ' ' + x.set + ' #' + x.case + ' ' + x.name));
          b.type = 'button'; b.dataset.ucset = x.set; b.dataset.ucref = CM.ref(r, c.n); b.title = uiText('在测试集查看器里看这条用例对接的数据', "Open this case's data in the test set viewer");
        });
      }
    }
    if (edit) {
      var acts = d.appendChild(el('div', 'uc-acts'));
      var e1 = acts.appendChild(el('button', 'sm', uiText('改', "Edit"))); e1.type = 'button'; e1.dataset.uc = 'edit'; e1.dataset.case = c.n;
      var e2 = acts.appendChild(el('button', 'sm quiet', uiText('删', "Delete"))); e2.type = 'button'; e2.dataset.uc = 'del'; e2.dataset.case = c.n;
    }
  });
  if (UC.err[r.id + ':' + cond.n]) box.appendChild(el('p', 'ts-err', UC.err[r.id + ':' + cond.n]));
}

// 「待确认的用例改动」：立项以后会话提的改、删、加用例和改归类，你同意或不同意；样式和「这个需求改了以前的数据」一样
function ucChangeHtml(c, r) {
  var h = '<div class="ts-chg' + (c.state === 'approved' ? ' done' : c.state === 'rejected' ? ' no' : '') + '"><b>' + tesc(c.target) + uiText('</b> <span class="mono muted">改动 ', "</b> <span class=\"mono muted\">Change ") + c.k + ' · ' + tesc(c.opText) + ' · ' + tesc(c.by) + '</span>'
    + uiText('<div class="ov"><div><span>原来</span><pre>', "<div class=\"ov\"><div><span>Original</span><pre>") + tesc(c.old) + uiText('</pre></div><div><span>改成</span><pre>', "</pre></div><div><span>Proposed</span><pre>") + tesc(c.now) + '</pre></div></div>'
    + uiText('<div class="why">理由：', "<div class=\"why\">Reason: ") + tesc(c.why) + '</div><div class="ts-acts">';
  if (c.state === 'approved') h += uiText('<span class="ts-pill g">你同意了 · 已生效</span>', "<span class=\"ts-pill g\">Approved · Applied</span>");
  else if (c.state === 'rejected') h += uiText('<span class="ts-pill r">你不同意 · 用例保持原样</span>', "<span class=\"ts-pill r\">Declined · Original kept</span>");
  else h += '<button type="button" class="sm primary" data-ucd="ok" data-req="' + tesc(r.id) + '" data-k="' + c.k + uiText('">同意</button><button type="button" class="sm ts-btn" data-ucd="no" data-req="', "\">Approve</button><button type=\"button\" class=\"sm ts-btn\" data-ucd=\"no\" data-req=\"") + tesc(r.id) + '" data-k="' + c.k + uiText('">不同意</button>', "\">Decline</button>");
  return h + '</div></div>';
}
function panelCaseChanges() {
  var r = byId(current), box = $('req-case-changes');
  if (!box) { box = el('div'); box.id = 'req-case-changes'; box.addEventListener('click', ucDecideClick); }
  var all = r && (r.state === 'open' || r.state === 'draft') ? CM.changes(r) : [];
  if (!all.length) { box.remove(); return; }
  var pend = all.filter(function (c) { return c.state === 'pending'; }), done = all.filter(function (c) { return c.state !== 'pending'; }).slice(-3);
  var h = uiText('<h2>待确认的用例改动 <span class="n">', "<h2>Case changes awaiting review <span class=\"n\">") + (pend.length ? pend.length + uiText(' 条等你确认', " awaiting review") : uiText('都处理过了', "All reviewed")) + '</span></h2>'
    + uiText('<p class="note">立项以后，会话要改、删用例，加用例或改归类，都要你同意才生效（测试会话补充的用例除外）。同意马上生效；不同意时这句话会交回提出它的会话。</p>', "<p class=\"note\">After planning, Agent changes to cases and classifications need approval, except new cases added by the test session. Approvals apply immediately. Declines go back to the proposing session.</p>")
    + pend.map(function (c) { return ucChangeHtml(c, r); }).join('') + done.map(function (c) { return ucChangeHtml(c, r); }).join('')
    + (UC.err[r.id] ? '<p class="ts-err">' + tesc(UC.err[r.id]) + '</p>' : '');
  if (box.ucHtml !== h || box.dataset.for !== r.id) { box.innerHTML = h; box.ucHtml = h; box.dataset.for = r.id; }
  var body = $('req-body'), tests = $('req-tests');
  var check = Array.prototype.slice.call(body.querySelectorAll('h2')).filter(function (x) { return x.textContent === '验收条件'; })[0];
  if (tests && tests.parentNode === body) body.insertBefore(box, tests);
  else if (check && check.nextSibling) body.insertBefore(box, check.nextSibling.nextSibling);
  else body.appendChild(box);
}
function ucDecide(id, k, ok, done) {
  var r = byId(id), change = r && (r.testing.case_changes || []).filter(function (c) { return c.k === k; })[0];
  return window.shell.decideCase(id, k, ok, change).then(function (res) {
    if (res && res.error) { if (done) done(res.error); else { UC.err[id] = res.error; panel(); } return res; }
    delete UC.err[id];
    if (res && res.id) reqs = reqs.map(function (x) { return x.id === id ? Object.assign({}, x, { testing: Object.assign({}, x.testing, { cases: res.cases, case_changes: res.case_changes, checks: res.checks, uncovered: res.uncovered,
      pending_case_changes: (res.case_changes || []).filter(function (c) { return c.state === 'pending'; }).length }) }) : x; });
    if (done) done(); else panel();
    return res;
  });
}
function ucDecideClick(e) {
  var b = e.target.closest('button[data-ucd]');
  if (!b) return;
  b.disabled = true;
  ucDecide(b.dataset.req, Number(b.dataset.k), b.dataset.ucd === 'ok');
}

// ---------- 你自己加、改、删用例和归类 ----------
function ucDialog() {
  if (UC.dlg) return UC.dlg;
  var d = document.createElement('dialog');
  d.id = 'uc-dlg'; d.className = 'ts-dlg uc-dlg';
  document.body.appendChild(d);
  d.addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    if (b.dataset.x === 'no') return d.close();
    if (b.dataset.x === 'yes') return ucSubmit();
  });
  UC.dlg = d;
  return d;
}
function ucOpen(form) {
  var r = byId(current); if (!r) return;
  UC.form = Object.assign({ req: r.id }, form);
  var d = ucDialog(), f = UC.form, checks = (r.testing && r.testing.checks) || [];
  var h = '<h2>' + (f.op === 'kind' ? uiText('第 ', "Classify criterion ") + f.check + uiText(' 条验收条件怎么验', "") : f.op === 'edit' ? uiText('改用例 ', "Edit case ") + f.case : uiText('加一条用例', "Add a case")) + '</h2>';
  if (f.op === 'kind') {
    var cond = checks.filter(function (c) { return c.n === Number(f.check); })[0] || {};
    h += '<p class="muted">' + tesc(cond.text || '') + '</p><div class="uc-kinds">'
      + [['data', uiText('能用数据验证', "Runnable test"), uiText('给定一些数据、做一个操作、看结果就能判断：写用例，测试会话对接成测试数据', "Define the input, action, and expected result. The test session converts the case into test data.")], ['ui', uiText('只能人工看', "Manual check"), uiText('要人看着界面、操作了才能判断：写清楚应该看到什么，测试会话补成检查步骤', "Describe what someone should see or do. The test session will write the steps.")]].map(function (x) {
        return '<label><input type="radio" name="uc-kind" value="' + x[0] + '"' + ((f.kind || cond.kind) === x[0] ? ' checked' : '') + '> <b>' + x[1] + '</b><small>' + x[2] + '</small></label>';
      }).join('') + uiText('</div><label class="uc-f">理由（只能人工看的，写清楚应该看到什么）<input id="uc-why" value="', "</div><label class=\"uc-f\">Reason (for a manual check, describe what to expect)<input id=\"uc-why\" value=\"") + tesc(f.why || cond.why || '') + '"></label>';
  } else {
    h += uiText('<label class="uc-f">挂在哪条验收条件下<select id="uc-check">', "<label class=\"uc-f\">Acceptance criterion<select id=\"uc-check\">") + checks.map(function (c) { return '<option value="' + c.n + '"' + (Number(f.check) === c.n ? ' selected' : '') + uiText('>第 ', ">Criterion ") + c.n + uiText(' 条：', ": ") + tesc(c.text) + '</option>'; }).join('') + '</select></label>'
      + uiText('<div class="uc-row"><label class="uc-f grow">名称（覆盖哪种情况）<input id="uc-name" value="', "<div class=\"uc-row\"><label class=\"uc-f grow\">Name (what the case covers)<input id=\"uc-name\" value=\"") + tesc(f.name || '') + '"></label>'
      + uiText('<label class="uc-f">类别<select id="uc-cat">', "<label class=\"uc-f\">Category<select id=\"uc-cat\">") + CM.CATS.map(function (k) { return '<option value="' + k + '"' + ((f.cat || '正常') === k ? ' selected' : '') + '>' + (typeof TestsModel === 'undefined' ? k : TestsModel.categoryName(k)) + '</option>'; }).join('') + '</select></label></div>'
      + [['given', uiText('前提', "Given")], ['when', uiText('操作', "When")], ['then', uiText('预期', "Then")]].map(function (x) { return '<label class="uc-f">' + x[1] + '<textarea id="uc-' + x[0] + '" rows="2">' + tesc(f[x[0]] || '') + '</textarea></label>'; }).join('')
      + uiText('<p class="muted small">用业务的话写：说用户看得到、说得出的东西，不提函数名、文件、接口字段。', "<p class=\"muted small\">Describe what the user sees or does in business terms.") + (r.state === 'open' ? uiText('你在这里改的直接生效，不用确认。', "Your edits take effect immediately.") : '') + '</p>';
  }
  h += uiText('<p class="ts-err" id="uc-err" hidden></p><div class="ts-row"><button type="button" class="quiet" data-x="no">取消</button><button type="button" class="primary" data-x="yes">保存</button></div>', "<p class=\"ts-err\" id=\"uc-err\" hidden></p><div class=\"ts-row\"><button type=\"button\" class=\"quiet\" data-x=\"no\">Cancel</button><button type=\"button\" class=\"primary\" data-x=\"yes\">Save</button></div>");
  d.innerHTML = h;
  if (!d.open) d.showModal();
}
function ucSubmit() {
  var f = UC.form, err = $('uc-err');
  var form, op;
  if (f.op === 'kind') {
    var pick = UC.dlg.querySelector('input[name="uc-kind"]:checked');
    form = { check: f.check, kind: pick ? pick.value : '', why: $('uc-why').value };
    if (!form.kind) { err.textContent = uiText('选一种。', "Select a classification."); err.hidden = false; return; }
    op = 'classify';
  } else {
    form = { check: $('uc-check').value, name: $('uc-name').value, cat: $('uc-cat').value, given: $('uc-given').value, when: $('uc-when').value, then: $('uc-then').value, case: f.case };
    var miss = CM.checkForm(form);
    if (miss) { err.textContent = miss; err.hidden = false; return; }
    op = f.op === 'edit' ? 'edit' : 'add';
  }
  UC.dlg.querySelector('[data-x="yes"]').disabled = true;
  window.shell.saveCase(f.req, CM.savePayload(op, form)).then(function (res) {
    UC.dlg.querySelector('[data-x="yes"]').disabled = false;
    if (res && res.error) { err.textContent = res.error; err.hidden = false; return; }
    ucMerge(f.req, res); UC.dlg.close(); panel();
  });
}
// 保存的结果马上画上（之后 rf.py req list 推过来的会再覆盖一次）
function ucMerge(id, res) {
  if (!res || !res.id) return;
  reqs = reqs.map(function (x) {
    if (x.id !== id) return x;
    var t = Object.assign({}, x.testing, { cases: res.cases, case_changes: res.case_changes, checks: res.checks, uncovered: res.uncovered });
    var gate = Object.assign({}, x.gate, { uncovered: (res.uncovered || []).map(function (n) { var c = res.checks.filter(function (k) { return k.n === n; })[0]; return { n: n, text: c ? c.text : '' }; }) });
    return Object.assign({}, x, { testing: t, gate: gate });
  });
}
function ucClick(e) {
  var b = e.target.closest('button');
  if (!b) return;
  var r = byId(current); if (!r) return;
  if (b.dataset.ucset) { e.preventDefault(); return openTestViewer(b.dataset.ucset, Object.assign(r.state === 'open' ? { req: r.id } : {}, { usecase: b.dataset.ucref })); }
  var act = b.dataset.uc; if (!act) return;
  e.preventDefault();
  if (act === 'add') return ucOpen({ op: 'add', check: Number(b.dataset.check) });
  if (act === 'kind') return ucOpen({ op: 'kind', check: Number(b.dataset.check) });
  var c = ((r.testing && r.testing.cases) || []).filter(function (x) { return x.n === Number(b.dataset.case); })[0];
  if (!c) return;
  var cond = ((r.testing && r.testing.checks) || []).filter(function (k) { return k.key === c.check; })[0];
  if (act === 'edit') return ucOpen({ op: 'edit', case: c.n, check: cond ? cond.n : 1, name: c.name, cat: c.cat, given: c.given, when: c.when, then: c.then });
  if (act === 'del') {
    if (!window.confirm(uiText('删掉用例 ', "Delete case ") + c.n + '「' + c.name + uiText('」？\n\n删了的编号不再用', "”?\n\nThe case number will not be reused") + (c.source === 'test' ? uiText('；这是测试会话补充的。', ". The test session added this case.") : '。') + (r.state === 'open' ? uiText('你在这里删的直接生效。', "Deleting here takes effect immediately.") : ''))) return;
    window.shell.saveCase(r.id, { op: 'delete', case: c.n }).then(function (res) {
      if (res && res.error) { UC.err[r.id + ':' + (cond ? cond.n : 0)] = res.error; return panel(); }
      ucMerge(r.id, res); panel();
    });
  }
}

// 绑定事件：由 main.js 按顺序调用
function wireCases() {
  $('req-body').addEventListener('click', ucClick);
}
