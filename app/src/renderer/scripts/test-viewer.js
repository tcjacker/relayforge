// 测试集查看器：需求面板和测试页共用的弹层。四个标签：用例（像刷题网站跑代码：输入、期望输出、实际输出）、测试数据（JSON 全文，可改）、
// 测试代码（代码只读；语言、接入代码路径、Java 的 classpath 和 build 命令可以改）、让 AI 改（把你的话交给一个会话去改，不在这里聊）。
// 数据由 rf.py test show 给出，保存、运行、看过了都由主进程调用 rf.py test。AI 新加的数据只有点「看过了」才算看过，点开不算。
var TV = {
  id: null, ctx: {}, data: null, error: '', info: '', tab: 'cases', f: 'need', q: '', n: null,
  edits: {},     // 序号（新加的是 'new'）-> { name, cat, input, expect, raises }：改了还没保存的文字
  json: null,    // JSON 标签里改了还没保存的全文
  conf: null,    // 测试代码标签里改了还没保存的设置 { lang, entry, classpath（一行一个）, build }
  say: '', busy: '', // busy：'save' | 'ai' 等着主进程回话
  fresh: false,  // 数据文件变了，查看器自己重读过
};

function tvScope() {
  if (TV.ctx.req) return { req: TV.ctx.req };
  if (TV.ctx.fix) return { fix: TV.ctx.fix };
  var own = TV.data && TV.data.in_progress;
  return own ? (/^F-/.test(own) ? { fix: own } : { req: own }) : {};
}
var tvKey = function () { return 'set:' + TV.id; };

// ctx：{ req: 需求编号 }（从需求面板打开，按那个需求眼里的数据）或 {}（从测试页打开，按主线）
function openTestViewer(id, ctx) {
  var d = $('ts-viewer');
  if (TV.id !== id) { TV.edits = {}; TV.json = null; TV.conf = null; TV.say = ''; TV.n = null; TV.q = ''; TV.f = 'need'; TV.tab = 'cases'; }
  TV.id = id; TV.ctx = Object.assign({}, ctx || {}); TV.data = null; TV.error = ''; TV.info = '';
  TV.uc = TV.ctx.usecase || null; delete TV.ctx.usecase; // 从需求面板的一条用例点进来：只看它对接的那几条数据（「全部」能看别的）
  if (TV.uc) TV.f = 'uc';
  renderViewer();
  if (!d.open) d.showModal();
  reloadViewer(true);
}
function reloadViewer(first) {
  if (!TV.id || !$('ts-viewer').open) return;
  var want = TV.id;
  window.shell.testsShow(Object.assign({ id: TV.id }, TV.ctx)).then(function (data) {
    if (want !== TV.id) return;
    if (data && data.error) { TV.error = data.error; renderViewer(); return; }
    var was = TV.data;
    TV.data = data;
    if (first || TV.n == null || !data.cases_list.some(function (c) { return c.n === TV.n; })) {
      var fv = TM.firstView(data.cases_list);
      var mine = TV.uc ? data.cases_list.filter(function (c) { return c.usecase === TV.uc; }) : [];
      if (first) TV.f = mine.length ? 'uc' : fv.filter;
      if (TV.n !== 'new') TV.n = mine.length && first ? mine[0].n : fv.n;
    }
    TV.fresh = !!was && !first;
    renderViewer();
  });
}
function closeViewer() {
  $('ts-viewer').close();
  TV.id = null; TV.data = null;
}

function tvCase() { return TV.data && TV.data.cases_list.filter(function (c) { return c.n === TV.n; })[0]; }
function tvStatusPill(c) {
  var s = TM.caseStatus(c);
  return '<span class="ts-pill ' + ({ pass: 'g', fail: 'r', broken: 'r', unrun: 'a' })[s] + '">' + TM.STATUS[s] + '</span>';
}
function renderViewer() {
  var d = $('ts-viewer');
  if (!d.open && !TV.id) return;
  var s = TV.data, running = !!TS.running[tvKey()];
  if (!s) { d.innerHTML = '<div class="tv-head"><h2>' + tesc(TV.id || '') + uiText('</h2><span class="sp"></span><button type="button" class="sm quiet" data-v="close">关闭</button></div><div class="tv-main">', "</h2><span class=\"sp\"></span><button type=\"button\" class=\"sm quiet\" data-v=\"close\">Close</button></div><div class=\"tv-main\">") + (TV.error ? '<p class="ts-err">' + tesc(TV.error) + '</p>' : uiText('<p class="muted">正在读…</p>', "<p class=\"muted\">Loading…</p>")) + '</div>'; return; }
  var t = TM.viewerCounts(s.cases_list), scope = tvScope(), owner = scope.req || scope.fix;
  var r = owner && byId(owner);
  var h = '<div class="tv-head"><h2>' + tesc(s.title) + '</h2><span class="mono muted">' + tesc(s.id) + '</span><span class="ts-pills">' + TM.pillsHtml(t) + '</span>'
    + (s.stale ? uiText('<span class="ts-pill">过期 · 不再运行</span>', "<span class=\"ts-pill\">Obsolete · Skipped</span>") : '')
    + '<span class="sp"></span>' + (t.unseen ? uiText('<button type="button" class="sm" data-v="seenall" title="AI 新加的 ', "<button type=\"button\" class=\"sm\" data-v=\"seenall\" title=\"Mark all ") + t.unseen + uiText(' 条都标成看过了">这些我都看过了（', " new AI cases as reviewed\">Mark all reviewed (") + t.unseen + '）</button>' : '')
    + envSelect() + uiText('<button type="button" class="sm" data-v="add">加一条</button>', "<button type=\"button\" class=\"sm\" data-v=\"add\">Add case</button>")
    + '<button type="button" class="sm primary" data-v="runall"' + (running || s.stale ? ' disabled' : '') + '>' + (running ? uiText('正在跑…', "Running…") : uiText('运行全部', "Run all")) + '</button>'
    + uiText('<button type="button" class="sm quiet" data-v="close">关闭</button></div>', "<button type=\"button\" class=\"sm quiet\" data-v=\"close\">Close</button></div>");
  h += '<div class="tv-tabs" role="tablist">' + [['cases', uiText('逐条数据（', "Cases (") + s.cases_list.length + '）'], ['json', uiText('测试数据（JSON 全文）', "Test data (JSON)")], ['code', uiText('测试代码', "Test code")], ['ai', uiText('让 AI 改', "Ask AI to edit")]].map(function (x) {
    return '<button type="button" role="tab" class="tv-tab" data-tab="' + x[0] + '" aria-selected="' + (TV.tab === x[0]) + '">' + x[1] + '</button>';
  }).join('') + uiText('<span class="tv-where">挂在：', "<span class=\"tv-where\">Linked to: ") + tesc((s.requirement ? ((byId(s.requirement) || {}).title || s.requirement) : uiText('没挂需求', "No requirement")) + ' · ' + s.feature + ' ' + (s.feature_title || ''))
    + (owner ? uiText(' · 按「', " · Data for “") + tesc(r ? r.title : owner) + uiText('」眼里的数据', "”") : uiText(' · 按主线的数据', " · Main checkout data")) + '</span></div>';
  if (TV.error) h += '<div class="tv-msg err" role="alert">' + tesc(TV.error) + '</div>';
  else if (TV.info) h += '<div class="tv-msg ok" role="status">' + tesc(TV.info) + '</div>';
  if (TV.tab === 'json') h += tvJson(s);
  else if (TV.tab === 'code') h += '<div class="tv-main">' + tvConfHtml(s) + uiText('<p class="lead">AI 写的测试代码（接入代码）。它不含任何测试数据，只负责：读数据 → 逐条调用被测的代码 → 把结果交给程序比对。一般不需要看。</p><p class="small mono muted">', "<p class=\"lead\">The test entry point reads each case, calls the code under test, and returns the result for RelayForge to compare.</p><p class=\"small mono muted\">") + tesc(s.entry_path || s.entry) + '（' + tesc(s.lang) + '）</p>'
    + (s.entry_code == null ? uiText('<p class="muted">这个文件还不存在。</p>', "<p class=\"muted\">This file does not exist yet.</p>") : '<pre>' + tesc(s.entry_code) + '</pre>') + '</div>';
  else if (TV.tab === 'ai') h += tvAi(s);
  else h += tvCases(s, t);
  d.innerHTML = h;
}

// ---------- 用例 ----------
function tvCases(s, t) {
  var h = '';
  if (s.analysis) h += uiText('<div class="tv-analysis"><span>AI 的分析（', "<div class=\"tv-analysis\"><span>AI analysis (") + tesc(String(s.analysis.at || '').slice(5, 16)) + '）：</span>' + tesc(s.analysis.text) + '</div>';
  var ucN = TV.uc ? s.cases_list.filter(function (c) { return c.usecase === TV.uc; }).length : 0;
  h += '<div class="tv-body"><div class="tv-list"><div class="tv-filter">' + (TV.uc ? [['uc', uiText('用例 ', "Case ") + TV.uc.split('#')[1] + uiText(' 的 ', " · ") + ucN]] : []).concat([['need', uiText('需要看的 ', "Needs attention ") + t.need], ['fail', uiText('失败 ', "Failed ") + (t.fail + t.broken)], ['all', uiText('全部 ', "All ") + t.total]]).map(function (x) {
    return '<button type="button" class="sm" data-f="' + x[0] + '" aria-pressed="' + (TV.f === x[0]) + '">' + x[1] + '</button>';
  }).join('') + uiText('<input type="search" id="tv-q" placeholder="搜名称" value="', "<input type=\"search\" id=\"tv-q\" placeholder=\"Search by name\" value=\"") + tesc(TV.q) + uiText('" aria-label="搜名称"></div><div id="tv-rows">', "\" aria-label=\"Search by name\"></div><div id=\"tv-rows\">") + tvRows(s) + '</div></div>';
  h += '<div class="tv-main">' + tvDetail(s) + '</div></div>';
  return h;
}
function tvRows(s) {
  var gs = TV.f === 'uc' ? TM.caseGroups(s.cases_list.filter(function (c) { return c.usecase === TV.uc; }), 'all', TV.q) : TM.caseGroups(s.cases_list, TV.f, TV.q), h = '';
  if (TV.n === 'new') h += uiText('<div class="tv-grp">新加的</div><button type="button" class="tv-case" aria-current="true" data-n="new"><span class="tv-s unrun">+</span><span>', "<div class=\"tv-grp\">New</div><button type=\"button\" class=\"tv-case\" aria-current=\"true\" data-n=\"new\"><span class=\"tv-s unrun\">+</span><span>") + tesc((TV.edits.new || {}).name || uiText('新的一条', "New case")) + '</span></button>';
  gs.forEach(function (g) {
    h += '<div class="tv-grp">' + tesc(g.cat) + ' · ' + g.rows.length + '</div>' + g.rows.map(function (c) {
      var st = TM.caseStatus(c);
      return '<button type="button" class="tv-case" data-n="' + c.n + '" aria-current="' + (c.n === TV.n) + '"><span class="tv-s ' + st + '">' + ({ pass: '✓', fail: '✗', broken: '!', unrun: '?' })[st] + '</span><span>' + tesc(c.name)
        + (c.usecase ? uiText('<span class="uc-tag" title="对接的测试用例">用例 ', "<span class=\"uc-tag\" title=\"Linked planning case\">Case ") + tesc(String(c.usecase).split('#')[1]) + '</span>' : '')
        + (c.unseen ? uiText(' <span class="ts-pill a">AI 新加</span>', " <span class=\"ts-pill a\">Added by AI</span>") : '') + (c.changed ? uiText(' <span class="ts-pill b">改动 ', " <span class=\"ts-pill b\">Change ") + c.changed + '</span>' : '') + (TV.edits[c.n] ? uiText(' <span class="ts-pill">没保存</span>', " <span class=\"ts-pill\">Unsaved</span>") : '') + '</span></button>';
    }).join('');
  });
  if (!gs.length && TV.n !== 'new') h += '<div class="small muted" style="padding:12px">' + (!s.cases_list.length ? uiText('还没有数据。点「加一条」，或者到「让 AI 改」里让 AI 补。', "No data. Add a case or ask AI to add some.") : TV.f === 'need' && !TV.q ? uiText('没有需要看的：都通过了，也没有 AI 新加还没看的。', "All cases passed and new AI cases have been reviewed.") : uiText('没有符合的。', "No matches.")) + '</div>';
  return h;
}
// 编辑框里现在的样子：改过的用改过的文字，否则用数据本身
function tvFields(c) {
  var e = TV.edits[TV.n === 'new' ? 'new' : c.n];
  if (e) return e;
  var f = TM.caseFields(c);
  return { name: f.name, cat: f.cat, input: TM.json(f.input), expect: 'expect' in f ? TM.json(f.expect) : '', raises: f.raises || '', mode: f.raises ? 'raises' : 'expect' };
}
function tvDetail(s) {
  var c = TV.n === 'new' ? { n: 'new', name: '', cat: '正常', input: {}, expect: null } : tvCase();
  if (!c) return '<p class="muted">' + (s.cases_list.length ? uiText('在左边选一条。', "Select a case on the left.") : uiText('还没有数据。', "No test data yet.")) + '</p>';
  var f = tvFields(c), st = TM.caseStatus(c), res = c.result, running = !!TS.running[tvKey()];
  var dirty = TV.n === 'new' || !!TV.edits[c.n];
  var h = '<div class="tv-chead"><input class="name" data-e="name" value="' + tesc(f.name) + uiText('" placeholder="这一条覆盖哪种情况（必填）" aria-label="名称">', "\" placeholder=\"Name the scenario this case covers (required)\" aria-label=\"Name\">")
    + (TV.n === 'new' ? uiText('<span class="ts-pill a">还没保存</span>', "<span class=\"ts-pill a\">Unsaved</span>") : tvStatusPill(c))
    + uiText('<select class="sm" data-e="cat" aria-label="类别">', "<select class=\"sm\" data-e=\"cat\" aria-label=\"Category\">") + TM.CATS.map(function (k) { return '<option value="' + k + '"' + (k === f.cat ? ' selected' : '') + '>' + TM.categoryName(k) + '</option>'; }).join('') + '</select>'
    + (TV.n === 'new' ? '' : '<button type="button" class="sm" data-v="runone"' + (running || s.stale ? ' disabled' : '') + '>' + (running ? uiText('正在跑…', "Running…") : uiText('只跑这一条', "Run this case")) + uiText('</button><button type="button" class="sm quiet" data-v="del">删掉这条</button>', "</button><button type=\"button\" class=\"sm quiet\" data-v=\"del\">Delete case</button>")) + '</div>';
  if (st === 'broken') {
    var hd = TS.handled[s.id];
    h += uiText('<div class="tv-box bad"><b>跑不起来</b><div>不是结果不对，是这个测试集没能启动起来（测试代码出错、缺依赖、没编译、准备步骤没过……），这条数据根本没有执行到。', "<div class=\"tv-box bad\"><b>Couldn't run</b><div>The test set failed to start, so this case did not execute. Check the test code, dependencies, build, or preparation steps. ")
      + uiText('可以让 AI 来处理：程序把下面的错误、测试环境的名字和不保密的设置交给一个会话，它试出做法以后交回建议，你采纳了再跑一次。被测的代码改了名字或删掉了的，这个测试集也可能该标成过期。</div><pre>', "You can ask AI to investigate using the error and non-secret environment settings. Apply the suggested fix before rerunning. If the code under test is gone, consider marking the test set obsolete.</div><pre>") + tesc(TM.whyFailed(c)) + '</pre>'
      + '<div class="tv-acts"><button type="button" class="sm ts-btn" data-v="handle"' + (TV.busy === 'handle' ? ' disabled' : '') + '>' + (TV.busy === 'handle' ? uiText('正在交…', "Sending…") : uiText('让 AI 来处理', "Ask AI to investigate")) + '</button>'
      + '<button type="button" class="sm" data-v="runall"' + (running ? ' disabled' : '') + '>' + (running ? uiText('正在跑…', "Running…") : uiText('再跑一次', "Run again")) + '</button>'
      + (hd ? uiText('<span class="small">已交给', "<span class=\"small\">Sent to ") + tesc(hd.title) + uiText('。 <button type="button" class="link" data-v="gohandle">去那个会话看</button></span>', ". <button type=\"button\" class=\"link\" data-v=\"gohandle\">Open session</button></span>") : '') + '</div></div>';
  }
  else if (st === 'fail') h += uiText('<div class="tv-box bad"><b>为什么失败</b>', "<div class=\"tv-box bad\"><b>Failure details</b>") + (s.analysis ? uiText('<div class="small muted">AI 对整个测试集的分析在上面。</div>', "<div class=\"small muted\">See the full test set analysis above.</div>") : '') + '<pre>' + tesc(TM.whyFailed(c)) + '</pre></div>';
  if (c.unseen) h += uiText('<div class="tv-box warn"><b>AI 新加的</b>：期望输出是 AI 按需求推出来的，请你看一眼对不对；不对可以直接改下面的期望输出再保存。看过了点右边的按钮（只点开不算看过）。 <button type="button" class="sm primary" data-v="seen">看过了</button></div>', "<div class=\"tv-box warn\"><b>Added by AI.</b> Review the expected output. If needed, edit and save it below. <button type=\"button\" class=\"sm primary\" data-v=\"seen\">Mark reviewed</button></div>");
  if (c.changed) h += uiText('<div class="tv-box warn">这一条用的是 AI 提的改动 ', "<div class=\"tv-box warn\">This case uses the expected result from AI change ") + c.changed + uiText(' 里的新期望，等你在需求面板里同意或不同意；你在这里直接改了保存，那条改动就作废。</div>', ". Review the proposal in the requirement panel. Editing and saving the case here discards the proposal.</div>");
  if (c.stale_result) h += uiText('<div class="tv-box">数据改过了，上次的结果不再对应，重跑一遍才知道。</div>', "<div class=\"tv-box\">The data changed after the last run. Rerun the case for current results.</div>");
  h += tvUsecase(s, c);
  var expectBox = f.mode === 'raises'
    ? uiText('<h3>期望报错（报错信息里含这段话）</h3><input class="mono" style="width:100%" data-e="raises" value="', "<h3>Expected error (message contains)</h3><input class=\"mono\" style=\"width:100%\" data-e=\"raises\" value=\"") + tesc(f.raises) + uiText('"><div class="small"><button type="button" class="link" data-v="mode-expect">改成期望输出</button></div>', "\"><div class=\"small\"><button type=\"button\" class=\"link\" data-v=\"mode-expect\">Expect output instead</button></div>")
    : uiText('<h3>期望输出</h3><textarea class="json" data-e="expect" spellcheck="false" style="min-height:90px">', "<h3>Expected output</h3><textarea class=\"json\" data-e=\"expect\" spellcheck=\"false\" style=\"min-height:90px\">") + tesc(f.expect) + '</textarea>' + (TV.n === 'new' || f.mode !== 'raises' ? uiText('<div class="small"><button type="button" class="link" data-v="mode-raises">改成期望它报错</button></div>', "<div class=\"small\"><button type=\"button\" class=\"link\" data-v=\"mode-raises\">Expect an error instead</button></div>") : '');
  var actual = TV.n === 'new' ? uiText('<pre class="muted">保存后运行</pre>', "<pre class=\"muted\">Save before running</pre>")
    : st === 'broken' ? uiText('<pre class="muted">没有执行到</pre>', "<pre class=\"muted\">Not executed</pre>")
    : st === 'unrun' ? uiText('<pre class="muted">还没跑</pre>', "<pre class=\"muted\">Not run</pre>")
    : res.error && res.actual == null ? '<pre class="' + (st === 'fail' ? 'diff' : '') + uiText('">报错：', "\">Error: ") + tesc(res.error) + '</pre>'
    : '<pre class="' + (st === 'fail' ? 'diff' : '') + '">' + tesc(TM.json(res.actual)) + '</pre>';
  h += uiText('<div class="tv-io"><div><h3>输入</h3><textarea class="json" data-e="input" spellcheck="false" style="min-height:', "<div class=\"tv-io\"><div><h3>Input</h3><textarea class=\"json\" data-e=\"input\" spellcheck=\"false\" style=\"min-height:") + (st === 'pass' || st === 'unrun' ? 230 : 180) + 'px">' + tesc(f.input) + '</textarea></div>'
    + '<div>' + expectBox + uiText('<h3 style="margin-top:10px">实际输出', "<h3 style=\"margin-top:10px\">Actual output") + (res && res.ms != null ? ' <span class="muted" style="font-weight:400">' + res.ms + ' ms</span>' : '') + '</h3>' + actual + '</div></div>';
  h += '<div class="tv-acts" id="tv-save"' + (dirty ? '' : ' hidden') + '><button type="button" class="primary sm" data-v="save"' + (TV.busy === 'save' ? ' disabled' : '') + '>' + (TV.busy === 'save' ? uiText('正在保存…', "Saving…") : TV.n === 'new' ? uiText('保存这一条', "Save case") : uiText('保存', "Save")) + '</button>'
    + '<button type="button" class="sm quiet" data-v="discard">' + (TV.n === 'new' ? uiText('不加了', "Cancel") : uiText('放弃修改', "Discard changes")) + uiText('</button><span class="small muted">你在这里改的直接生效，不用确认。</span></div>', "</button><span class=\"small muted\">Your changes take effect immediately.</span></div>");
  return h;
}
// 这条数据对接的测试用例（立项用业务的话写的三段）：对照看数据是不是忠实于用例
function tvUsecase(s, c) {
  if (TV.n === 'new') return '';
  var u = c.usecase && s.usecases ? s.usecases[c.usecase] : null;
  if (!c.usecase) return (s.requirement || s.in_progress) && /^R-/.test(String(s.requirement || s.in_progress)) ? uiText('<div class="tv-uc none">这条数据没有挂测试用例（老需求、开发会话加的，或者对接以前加的）。</div>', "<div class=\"tv-uc none\">This data has no linked planning case.</div>") : '';
  if (!u) return uiText('<div class="tv-uc none">挂的用例 ', "<div class=\"tv-uc none\">Linked case ") + tesc(c.usecase) + uiText(' 找不到了。</div>', " could not be found.</div>");
  return uiText('<div class="tv-uc"><b>对接的用例 ', "<div class=\"tv-uc\"><b>Linked case ") + u.n + '「' + tesc(u.name) + '」</b> <span class="ts-pill">' + tesc(TM.categoryName(u.cat)) + '</span>' + (u.source === 'test' ? uiText(' <span class="ts-pill b">测试补充的</span>', " <span class=\"ts-pill b\">Added during testing</span>") : '') + (u.live ? '' : uiText(' <span class="ts-pill r">已删除</span>', " <span class=\"ts-pill r\">Deleted</span>"))
    + uiText('<span class="muted"> · 验收条件：', "<span class=\"muted\"> · Acceptance criterion: ") + tesc(u.check) + uiText('</span><dl><dt>前提</dt><dd>', "</span><dl><dt>Given</dt><dd>") + tesc(u.given) + uiText('</dd><dt>操作</dt><dd>', "</dd><dt>When</dt><dd>") + tesc(u.when) + uiText('</dd><dt>预期</dt><dd>', "</dd><dt>Then</dt><dd>") + tesc(u.then) + '</dd></dl>'
    + uiText('<div class="small muted">对照看：输入对得上前提，调用对得上操作，期望输出对得上预期。</div></div>', "<div class=\"small muted\">Match the input to Given, the call to When, and the expected output to Then.</div></div>");
}
function tvEdit(target) {
  var c = TV.n === 'new' ? { n: 'new', name: '', cat: '正常', input: {}, expect: null } : tvCase();
  if (!c) return;
  var key = TV.n === 'new' ? 'new' : c.n;
  var e = TV.edits[key] || tvFields(c);
  e = Object.assign({}, e);
  e[target.dataset.e] = target.value;
  TV.edits[key] = e;
  var bar = $('tv-save'); if (bar) bar.hidden = false;
}
function tvSaveCase() {
  var key = TV.n === 'new' ? 'new' : TV.n, e = TV.edits[key] || tvFields(tvCase());
  var input = TM.parseJson(e.input, uiText('输入', "Input"));
  if (input.error) { TV.error = input.error; return renderViewer(); }
  var body = { name: String(e.name || '').trim(), cat: e.cat || '正常', input: input.value };
  if (!body.name) { TV.error = uiText('写一下这一条覆盖哪种情况（名称）。', "Name the scenario this case covers."); return renderViewer(); }
  if (e.mode === 'raises') { if (!String(e.raises || '').trim()) { TV.error = uiText('写一下期望报错信息里含的那段话。', "Enter the text the error message must contain."); return renderViewer(); } body.raises = String(e.raises).trim(); }
  else { var ex = TM.parseJson(e.expect, uiText('期望输出', "Expected output")); if (ex.error) { TV.error = ex.error; return renderViewer(); } body.expect = ex.value; }
  TV.busy = 'save'; TV.error = ''; renderViewer();
  window.shell.testsSave(Object.assign({ id: TV.id, caseN: key, payload: body }, tvScope())).then(function (res) {
    TV.busy = '';
    if (res && res.error) { TV.error = res.error; return renderViewer(); }
    delete TV.edits[key];
    if (key === 'new' && res.saved && res.saved.added.length) TV.n = res.saved.added[0];
    TV.info = key === 'new' ? uiText('加好了，还没跑：点「只跑这一条」看结果。', "Case added. Select Run this case to check it.") : uiText('保存好了', "Saved") + (res.saved && res.saved.voided && res.saved.voided.length ? uiText('，AI 对它提的改动作废', ". The proposed AI change was discarded") : '') + '。';
    TV.data = res; renderViewer();
  });
}
function tvDelete() {
  var c = tvCase();
  if (!c || !window.confirm(uiText('删掉「', "Delete “") + c.name + uiText('」这一条数据？\n\n你在这里删的直接生效，不用确认；变更记录里会留一行。', "”?\n\nThe entry will be removed immediately and the deletion will be logged."))) return;
  var rest = TV.data.cases_list.filter(function (x) { return x.n !== c.n; });
  TV.busy = 'save'; renderViewer();
  window.shell.testsSave(Object.assign({ id: TV.id, payload: TM.payload(rest) }, tvScope())).then(function (res) {
    TV.busy = '';
    if (res && res.error) { TV.error = res.error; return renderViewer(); }
    delete TV.edits[c.n];
    TV.data = res; TV.n = TM.firstView(res.cases_list).n; TV.info = uiText('删掉了「', "Deleted “") + c.name + '」。'; renderViewer();
  });
}

// ---------- 测试代码的设置 ----------
function tvConfFrom(s) { return { lang: s.lang || 'python', entry: s.entry || '', classpath: (s.classpath || []).join('\n'), build: s.build || '' }; }
function tvConfHtml(s) {
  var c = TV.conf || tvConfFrom(s), java = c.lang === 'java', dirty = !!TV.conf && JSON.stringify(TV.conf) !== JSON.stringify(tvConfFrom(s));
  return uiText('<div class="tv-conf"><h3>测试代码的设置</h3><div class="tv-conf-g">', "<div class=\"tv-conf\"><h3>Test code settings</h3><div class=\"tv-conf-g\">")
    + uiText('<label>语言<select data-c="lang">', "<label>Language<select data-c=\"lang\">") + ['python', 'node', 'java'].map(function (x) { return '<option' + (x === c.lang ? ' selected' : '') + '>' + x + '</option>'; }).join('') + '</select></label>'
    + uiText('<label class="wide">接入代码（相对代码根目录；Java 也可以写类的全名）<input class="mono" data-c="entry" value="', "<label class=\"wide\">Entry point (relative to the code root. Java also accepts a fully qualified class name.)<input class=\"mono\" data-c=\"entry\" value=\"") + tesc(c.entry) + '"></label>'
    + (java ? uiText('<label class="wide">classpath（一行一个；相对路径相对代码根目录，jar 目录写 lib/*；空着就按 RELAYFORGE_TEST_CLASSPATH → 自动识别 Maven / Gradle → 代码目录本身）<textarea class="mono" data-c="classpath" rows="4" spellcheck="false" placeholder="target/classes&#10;target/test-classes&#10;lib/*">', "<label class=\"wide\">Classpath (one entry per line. Paths are relative to the code root. Use lib/* for jars. If empty, try RELAYFORGE_TEST_CLASSPATH, then Maven or Gradle detection, then the code root.)<textarea class=\"mono\" data-c=\"classpath\" rows=\"4\" spellcheck=\"false\" placeholder=\"target/classes&#10;target/test-classes&#10;lib/*\">") + tesc(c.classpath) + '</textarea></label>'
      + uiText('<label class="wide">build 命令（可以不写；写了就在每次运行前先在代码目录里运行它，最多 10 分钟，失败算跑不起来）<input class="mono" data-c="build" value="', "<label class=\"wide\">Build command (optional. Runs in the code directory before each test run, with a 10-minute timeout.)<input class=\"mono\" data-c=\"build\" value=\"") + tesc(c.build) + '" placeholder="mvn -q compile"></label>'
      + uiText('<p class="small muted wide">build 命令每次运行都执行。建议改成测试环境里的「准备步骤」：可以只在 pom.xml 之类的文件变了以后才执行，第一次执行前也会先请你看命令、点同意。</p>', "<p class=\"small muted wide\">The build runs before every test run. To run a command only when files such as pom.xml change, set it up as a preparation step instead.</p>") : '')
    + '</div><div class="tv-acts"><button type="button" class="sm primary" data-v="saveconf"' + (dirty && TV.busy !== 'conf' ? '' : ' disabled') + '>' + (TV.busy === 'conf' ? uiText('正在保存…', "Saving…") : uiText('保存设置', "Save settings")) + '</button>'
    + (dirty ? uiText('<button type="button" class="sm quiet" data-v="resetconf">放弃修改</button>', "<button type=\"button\" class=\"sm quiet\" data-v=\"resetconf\">Discard changes</button>") : '') + uiText('<span class="small muted">你在这里改的直接生效，变更记录里会写上改了什么。</span></div></div>', "<span class=\"small muted\">Changes take effect immediately and are logged.</span></div></div>");
}
function tvSaveConf() {
  var c = TV.conf;
  if (!c) return;
  var payload = { lang: c.lang, entry: c.entry.trim(), classpath: c.lang === 'java' ? c.classpath.split('\n').map(function (x) { return x.trim(); }).filter(Boolean) : [], build: c.lang === 'java' ? c.build.trim() : '' };
  TV.busy = 'conf'; TV.error = ''; renderViewer();
  window.shell.testsConfig({ id: TV.id, payload: payload }).then(function (res) {
    TV.busy = '';
    if (!res || res.error) { TV.error = (res && res.error) || uiText('没能保存。', "Couldn't save."); return renderViewer(); }
    TV.conf = null; TV.info = res.changed && res.changed.length ? uiText('保存好了：', "Saved: ") + res.changed.join('；') + '。' : uiText('设置没有变化。', "Settings are unchanged.");
    reloadViewer();
  });
}

// ---------- 测试数据（JSON 全文） ----------
function tvJson(s) {
  var text = TV.json != null ? TV.json : JSON.stringify(Object.assign({ title: s.title }, TM.payload(s.cases_list)), null, 2);
  return uiText('<div class="tv-main full"><p class="lead">这是数据文件 <span class="mono">', "<div class=\"tv-main full\"><p class=\"lead\">Data file: <span class=\"mono\">") + tesc(s.data_file) + uiText('</span> 里的数据', "</span>") + (s.scope ? uiText('（按「', " (as seen by “") + tesc(s.scope) + uiText('」眼里的样子）', "”)") : '')
    + uiText('：测试代码读它，这个界面也读它。可以直接改：n 是序号，不要改；新加的一条不写 n；删掉一项就是删掉那条数据。变更记录、待确认的改动等由程序管，不在这里。</p>', ". Edit the array to change the data. Keep n for existing cases, omit it for new ones, and remove an item to delete the case.</p>")
    + (TV.fresh && TV.json != null ? uiText('<p class="ts-err">数据文件刚刚变了（可能是 AI 改的）。你改的还没保存；要看最新的，点「放弃修改」。</p>', "<p class=\"ts-err\">The data file changed while you were editing. Select Discard changes to load the latest version.</p>") : '')
    + '<textarea class="json big" id="tv-json" spellcheck="false">' + tesc(text) + '</textarea>'
    + '<div class="tv-acts"><button type="button" class="primary sm" data-v="savejson"' + (TV.busy === 'save' ? ' disabled' : '') + '>' + (TV.busy === 'save' ? uiText('正在保存…', "Saving…") : uiText('保存并运行', "Save and run")) + '</button>'
    + (TV.json != null ? uiText('<button type="button" class="sm quiet" data-v="resetjson">放弃修改</button>', "<button type=\"button\" class=\"sm quiet\" data-v=\"resetjson\">Discard changes</button>") : '') + '</div></div>';
}
function tvSaveJson() {
  var text = $('tv-json').value, parsed = TM.parseJson(text, uiText('测试数据', "Test data"));
  if (parsed.error) { TV.error = parsed.error; return renderViewer(); }
  TV.json = text; TV.busy = 'save'; TV.error = ''; renderViewer();
  window.shell.testsSave(Object.assign({ id: TV.id, payload: parsed.value }, tvScope())).then(function (res) {
    TV.busy = '';
    if (res && res.error) { TV.error = res.error; return renderViewer(); }
    TV.json = null; TV.data = res; TV.edits = {};
    var sv = res.saved || { added: [], changed: [], removed: [] };
    TV.info = uiText('保存好了：新加 ', "Saved. Added ") + sv.added.length + uiText(' 条，改了 ', ", changed ") + sv.changed.length + uiText(' 条，删了 ', ", and removed ") + sv.removed.length + uiText(' 条。正在运行…', ". Running…");
    tvRunSet();
  });
}

// ---------- 让 AI 改 ----------
function tvAi(s) {
  var hd = s.handed;
  var h = uiText('<div class="tv-main"><p class="lead">直接说想怎么改这份测试数据（补边界值、补异常输入、换一批数据……）。发送以后交给一个 Claude Code 会话去做：', "<div class=\"tv-main\"><p class=\"lead\">Describe what to change in the test data, such as adding edge cases or invalid inputs. This goes to an Agent session: ")
    + (tvScope().req || tvScope().fix ? uiText('这个测试集属于进行中的需求，交给它的开发会话。', "The requirement's development session.") : uiText('新开一个日常聊天（在项目目录里）。', "A new chat in the project directory."))
    + uiText('AI 只用 rf.py test 改这个测试集的数据，不动被测的代码；它加的、改的会标出来等你看，改以前就有的数据要你同意。改完它会运行、写分析，这里会自动刷新。</p>', "The Agent uses rf.py test to edit the data and leaves product code alone. Review new and changed cases here. Edits to existing data need approval. The viewer refreshes after the run and analysis.</p>")
    + '<div class="tv-acts" style="margin-top:4px">' + TM.AI_EXAMPLES.map(function (x) { return '<button type="button" class="sm" data-say="' + tesc(x) + '">' + tesc(x) + '</button>'; }).join('') + '</div>'
    + uiText('<div class="tv-say"><textarea id="tv-say" placeholder="比如：加一条跨时区的，用户在东京、服务器在伦敦">', "<div class=\"tv-say\"><textarea id=\"tv-say\" placeholder=\"Example: add a case with a user in Tokyo and a server in London\">") + tesc(TV.say) + '</textarea>'
    + '<button type="button" class="primary" data-v="send"' + (TV.busy === 'ai' ? ' disabled' : '') + '>' + (TV.busy === 'ai' ? uiText('正在交…', "Sending…") : uiText('发送', "Send")) + '</button></div>';
  if (hd) h += uiText('<div class="tv-handed">已交给', "<div class=\"tv-handed\">Sent to ") + tesc(hd.title || uiText('会话', "session")) + '（' + tesc(new Date(hd.at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })) + uiText('）。它改完数据、跑完以后，查看器会自动刷新。', "). The viewer refreshes when tests finish.")
    + uiText(' <button type="button" class="link" data-v="goto">去那个会话看</button></div>', " <button type=\"button\" class=\"link\" data-v=\"goto\">Open session</button></div>");
  return h + '</div>';
}
function tvSend() {
  var text = String(($('tv-say') && $('tv-say').value) || TV.say).trim();
  if (!text) { TV.error = uiText('先写想怎么改，或者点一个例子。', "Describe the change or choose an example."); return renderViewer(); }
  TV.busy = 'ai'; TV.error = ''; renderViewer();
  window.shell.testsAi(Object.assign({ id: TV.id, text: text }, TV.ctx)).then(function (res) {
    TV.busy = '';
    if (!res || res.error) { TV.error = (res && res.error) || uiText('没能交出去。', "Couldn't send."); return renderViewer(); }
    if (res.kind === 'chat') tsChatInBackground(res.sessionId, res.title, res.say);
    TV.say = ''; TV.data.handed = res.handed; TV.info = uiText('已经交给', "Sent to ") + res.handed.title + '。';
    renderViewer();
  });
}

// ---------- 运行 ----------
function tvRunSet(caseN) {
  tsRun(tvKey(), Object.assign({ ids: [TV.id], caseN: caseN }, tvScope()), function (res) {
    if (res && res.error) TV.error = res.error;
    else { TV.error = ''; var x = (res.sets || [])[0]; TV.info = x ? uiText('跑完了：', "Run finished: ") + TM.resultText({ pass: x.counts.pass, fail: x.counts.fail, broken: x.counts.broken, unrun: 0 }) + (caseN != null ? uiText('（只跑了这一条）', " (this case only)") : '') + '。' : (res.skipped && res.skipped[0] ? uiText('没有运行：', "Tests did not run: ") + res.skipped[0].why : uiText('没有运行。', "Tests did not run.")); }
    reloadViewer();
  });
}

function viewerClick(e) {
  var b = e.target.closest('button');
  if (!b || !TV.data && b.dataset.v !== 'close') return;
  var v = b.dataset.v;
  if (v === 'close') return closeViewer();
  if (b.dataset.tab) { if (TV.tab === 'ai' && $('tv-say')) TV.say = $('tv-say').value; TV.tab = b.dataset.tab; TV.info = ''; return renderViewer(); }
  if (b.dataset.f) { TV.f = b.dataset.f; var g = TV.f === 'uc' ? TM.caseGroups(TV.data.cases_list.filter(function (c) { return c.usecase === TV.uc; }), 'all', TV.q) : TM.caseGroups(TV.data.cases_list, TV.f, TV.q); if (g.length && TV.n !== 'new') TV.n = g[0].rows[0].n; return renderViewer(); }
  if (b.dataset.n) {
    if (b.dataset.n === 'new') return;
    TV.n = Number(b.dataset.n); TV.tab = 'cases'; TV.info = ''; TV.error = ''; // 点开不算看过：要点「看过了」
    return renderViewer();
  }
  if (b.dataset.say) { TV.say = b.dataset.say; renderViewer(); var box = $('tv-say'); if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); } return; }
  TV.info = '';
  if (v === 'add') { TV.tab = 'cases'; TV.n = 'new'; TV.edits.new = { name: '', cat: '正常', input: '{}', expect: 'null', raises: '', mode: 'expect' }; return renderViewer(); }
  if (v === 'runall') return tvRunSet();
  if (v === 'runone') return tvRunSet(TV.n);
  if (v === 'del') return tvDelete();
  if (v === 'save') return tvSaveCase();
  if (v === 'discard') { delete TV.edits[TV.n === 'new' ? 'new' : TV.n]; if (TV.n === 'new') TV.n = TM.firstView(TV.data.cases_list).n; TV.error = ''; return renderViewer(); }
  if (v === 'mode-raises' || v === 'mode-expect') {
    var c2 = TV.n === 'new' ? { n: 'new', name: '', cat: '正常', input: {}, expect: null } : tvCase();
    var key = TV.n === 'new' ? 'new' : c2.n;
    TV.edits[key] = Object.assign({}, TV.edits[key] || tvFields(c2), { mode: v === 'mode-raises' ? 'raises' : 'expect' });
    if (v === 'mode-expect' && !TV.edits[key].expect) TV.edits[key].expect = 'null';
    return renderViewer();
  }
  if (v === 'seen') { window.shell.testsAct({ action: 'seen', id: TV.id, caseN: TV.n }).then(function (res) { if (res && res.error) TV.error = res.error; else TV.info = uiText('标成看过了。', "Marked as reviewed."); reloadViewer(); }); return; }
  if (v === 'seenall') { window.shell.testsAct({ action: 'seen', id: TV.id }).then(function (res) { if (res && res.error) TV.error = res.error; else TV.info = uiText('AI 新加的 ', "Marked ") + res.cleared + uiText(' 条都标成看过了。', " new AI cases as reviewed."); reloadViewer(); }); return; }
  if (v === 'handle') {
    TV.busy = 'handle'; renderViewer();
    return tsAiHandle(TV.id, tvScope(), function (err) { TV.busy = ''; if (err) TV.error = err; else TV.info = uiText('已经交给', "Sent to ") + TS.handled[TV.id].title + uiText('。它交回的建议在测试环境页的「AI 的建议」里；采纳以后点「再跑一次」。', ". Review the suggestions in Test environments, then run again."); renderViewer(); });
  }
  if (v === 'gohandle') { var hr = TS.handled[TV.id] && TS.handled[TV.id].run; closeViewer(); return tsGoTo(hr); }
  if (v === 'saveconf') return tvSaveConf();
  if (v === 'resetconf') { TV.conf = null; return renderViewer(); }
  if (v === 'savejson') return tvSaveJson();
  if (v === 'resetjson') { TV.json = null; TV.fresh = false; return renderViewer(); }
  if (v === 'send') return tvSend();
  if (v === 'goto') { var run = TV.data.handed && TV.data.handed.run; closeViewer(); return tsGoTo(run); }
}

// 绑定事件：由 main.js 按顺序调用
function wireTestViewer() {
  var d = $('ts-viewer');
  d.addEventListener('click', viewerClick);
  d.addEventListener('input', function (e) {
    if (e.target.id === 'tv-q') { TV.q = e.target.value; $('tv-rows').innerHTML = tvRows(TV.data); return; }
    if (e.target.id === 'tv-json') { TV.json = e.target.value; return; }
    if (e.target.id === 'tv-say') { TV.say = e.target.value; return; }
    if (e.target.dataset.c) { TV.conf = Object.assign(TV.conf || tvConfFrom(TV.data), { [e.target.dataset.c]: e.target.value }); if (e.target.tagName === 'SELECT') renderViewer(); else { var sv = TV.data && $('ts-viewer').querySelector('[data-v="saveconf"]'); if (sv) sv.disabled = false; } return; }
    if (e.target.dataset.e) tvEdit(e.target);
  });
  d.addEventListener('change', function (e) {
    if (e.target.tagName === 'SELECT' && e.target.dataset.e) tvEdit(e.target);
  });
  d.addEventListener('close', function () { TV.id = null; TV.data = null; });
}
