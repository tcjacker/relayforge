// 问题页的按钮、对话框、粘贴截图和修复会话的终端。所有按钮都带 data-act，在这里统一处理。
var fixTerms = {}; // 修复会话（fix:F-005）和排查会话（probe:I-033）的编号 -> { term, fit, box, dead }：藏起来时会话照常在跑，切回来接着看
var IDLG = null, newShots = [];

function ishow(i, how) {
  IS.picker = false;
  if (i) { iReveal(i); if (how !== 'keep') IS.sel = i.id; }
  renderIssues();
}
function itoast(text, bad) { say(text, bad); }
function iBusyDo(i, promise, then) {
  IS.busy[i.id] = true; delete IS.errors[i.id]; IS.rkey = ''; renderIssues();
  return promise.then(function (made) {
    delete IS.busy[i.id]; IS.rkey = '';
    if (made && made.error) { IS.errors[i.id] = made.error; renderIssues(); return null; }
    if (then) then(made);
    renderIssues();
    return made;
  });
}

// ---------- 修复会话的终端 ----------
function fixTerm(run, say) {
  var t = fixTerms[run];
  if (t && !t.dead) { if (say) window.shell.open({ id: run, say: say }); return t; }
  if (t) { t.term.dispose(); t.box.remove(); }
  var box = el('div'); box.hidden = true;
  $('issue-term').appendChild(box);
  var term = new Terminal({ fontFamily: 'Menlo, "SF Mono", Consolas, "DejaVu Sans Mono", monospace', fontSize: 13, cursorBlink: true, allowProposedApi: true, theme: { background: '#111318' } });
  var fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(box);
  term.onData(function (data) { window.shell.input(run, data); });
  t = fixTerms[run] = { term: term, fit: fit, box: box, dead: false };
  var visible = !$('issues').hidden && !$('issue-term').hidden;
  if (visible) { box.hidden = false; fit.fit(); }
  var i = IS.issues.filter(function (x) { return fixRun(x) === run || probeRun(x) === run; })[0];
  var title = !i ? run : probeRun(i) === run ? uiText('问题 ', "Investigation for issue ") + i.num + uiText(' 的排查', "") : i.batch > 1 ? uiText('修复 ', "Fix ") + i.fix.id + uiText(' · 一起修 ', " · Fixing ") + i.batch + uiText(' 条', " together") : uiText('问题 ', "Fix for issue ") + i.num + uiText(' 的修复', "");
  window.shell.open({ id: run, cols: visible ? term.cols : 100, rows: visible ? term.rows : 30, title: title, say: say });
  return t;
}
function showFixTerm(i) {
  var run = termRun(i), t = fixTerm(run);
  Object.keys(fixTerms).forEach(function (k) { fixTerms[k].box.hidden = k !== run; });
  requestAnimationFrame(function () { refitFix(); t.term.focus(); });
}
function refitFix() {
  var i = curIssue(), run = termRun(i), t = run && fixTerms[run];
  if (!t || t.dead || t.box.hidden || $('issues').hidden || $('issue-term').hidden) return;
  t.fit.fit();
  window.shell.resize(run, { cols: t.term.cols, rows: t.term.rows });
}
function dropFixTerm(run) { var t = fixTerms[run]; if (t) { t.term.dispose(); t.box.remove(); delete fixTerms[run]; } }

// ---------- 对话框 ----------
function iOpenDialog(o) {
  IDLG = o;
  $('issue-dlg-title').textContent = o.title;
  $('issue-dlg-body').innerHTML = o.body;
  $('issue-dlg-ok').textContent = o.ok || uiText('确定', "OK"); $('issue-dlg-ok').hidden = !o.ok; $('issue-dlg-ok').disabled = false;
  $('issue-dlg-cancel').textContent = o.cancel || uiText('取消', "Cancel");
  $('issue-dlg').showModal();
  var f = $('issue-dlg').querySelector('input:not([type=file]),textarea'); if (f) f.focus();
}
function iCloseDialog() { IDLG = null; if ($('issue-dlg').open) $('issue-dlg').close(); }
function iDialogError(text) { var e = $('issue-dlg-error'); e.textContent = text || ''; e.hidden = !text; $('issue-dlg-ok').disabled = false; }
function refreshNewShots() { var box = $('issue-new-shots'); if (box) box.innerHTML = ithumbs(newShots, true); }

function iFinishMenu(open) {
  var menu = $('issue-finish-menu');
  if (!menu) return;
  menu.hidden = !open;
  $('issue-finish-toggle').setAttribute('aria-expanded', String(open));
}

function openFinishIssue(i, destination) {
  if (!i || i.state !== 'fixing' || IS.busy[i.id] || (destination !== 'resolved' && destination !== 'closed')) return;
  var label = destination === 'resolved' ? uiText('已处理', "Resolved") : uiText('已取消', "Cancelled");
  iFinishMenu(false);
  iOpenDialog({ title: uiText('转入', "Move to ") + label + '？', ok: uiText('确认转入', "Confirm move") + label, cancel: uiText('返回', "Back"), body:
    '<p>' + iesc(i.num + ' ' + i.title) + uiText('</p><p class="muted">确认后，这条问题会移到「', "</p><p class=\"muted\">This issue will move to “") + label + '」。</p>'
    + uiText('<label class="field">备注（选填）<textarea name="reason" maxlength="200"></textarea></label><p id="issue-dlg-error" class="ierror" hidden></p>', "<label class=\"field\">Note (optional)<textarea name=\"reason\" maxlength=\"200\"></textarea></label><p id=\"issue-dlg-error\" class=\"ierror\" hidden></p>"),
    okFn: function (form) {
      var dialog = IDLG;
      var value = form.elements.reason.value.trim();
      dialog.pending = true;
      $('issue-dlg-ok').disabled = true;
      $('issue-dlg-cancel').disabled = true;
      function fail(message) {
        dialog.pending = false;
        $('issue-dlg-cancel').disabled = false;
        iDialogError(message);
      }
      window.shell.setIssue({ id: i.id, action: 'finish-' + destination, value: value }).then(function (made) {
        if (made.error) return fail(made.error);
        $('issue-dlg-cancel').disabled = false;
        iCloseDialog();
        if (made.fix && made.fix.state === 'ended') dropFixTerm(fixRun(made));
        delete IS.pane[i.id];
        IS.rkey = '';
        mergeIssue(made);
        ishow(made);
      }, function () { fail(uiText('结束处理失败，请重试。', "Couldn't close the issue. Try again.")); });
      return false;
    } });
}

function openNewIssue() {
  newShots = [];
  iOpenDialog({ title: uiText('记个问题', "Report issue"), ok: uiText('记下', "Report"), body:
    uiText('<label class="field">哪里出了问题？<input name="title" maxlength="180" placeholder="用一句话说说看到的现象"></label>', "<label class=\"field\">What went wrong?<input name=\"title\" maxlength=\"180\" placeholder=\"Describe what you saw in one sentence\"></label>")
    + uiText('<label class="field">补充说明（选填）<textarea name="desc" placeholder="你做了什么，实际看到了什么？"></textarea></label>', "<label class=\"field\">Details (optional)<textarea name=\"desc\" placeholder=\"What did you do? What happened?\"></textarea></label>")
    + uiText('<div><b>截图（选填）</b><p class="muted small" style="margin:2px 0 6px">在上面的输入框里粘贴截图，或点下面选择图片。</p><div id="issue-new-shots"></div><button type="button" data-act="pickfile">选择图片</button></div>', "<div><b>Screenshots (optional)</b><p class=\"muted small\" style=\"margin:2px 0 6px\">Paste a screenshot above or select images below.</p><div id=\"issue-new-shots\"></div><button type=\"button\" data-act=\"pickfile\">Choose images</button></div>")
    + uiText('<p class="muted small">提交后 AI 先初判，下一步由你决定。</p><p id="issue-dlg-error" class="ierror" hidden></p>', "<p class=\"muted small\">AI will triage the issue. You decide what happens next.</p><p id=\"issue-dlg-error\" class=\"ierror\" hidden></p>"),
    okFn: function (form) {
      var title = form.elements.title.value.trim();
      if (!title) { form.elements.title.focus(); return false; }
      $('issue-dlg-ok').disabled = true;
      window.shell.newIssue({ title: title, desc: form.elements.desc.value.trim(), images: newShots.slice() }).then(function (made) {
        if (made.error) return iDialogError(made.error);
        iCloseDialog(); newShots = [];
        IS.kind = 'all'; IS.feat = 'all';
        mergeIssue(made); ishow(made);
      });
      return false;
    } });
}
function openToReq(i) {
  var shots = i.thread.reduce(function (n, e) { return n + (e.shots || []).length; }, 0);
  iOpenDialog({ title: uiText('转为需求', "Convert to requirement"), ok: uiText('创建需求草稿', "Create draft requirement"), body:
    uiText('<p>这条问题会变成一份立项中的需求，下面这些会一起带过去：</p><ul><li>问题描述</li><li>截图（', "<p>The new draft requirement will include:</p><ul><li>Issue description</li><li>Screenshots (") + shots + uiText(' 张）</li><li>整个对话（', ")</li><li>Full conversation (") + i.thread.length + uiText(' 条）</li></ul>', " entries)</li></ul>")
    + uiText('<p class="muted small">原问题会移到「已取消」，标记为「已转需求」。需求在「需求」里立项，立项会话开始时会读到这些。</p><p id="issue-dlg-error" class="ierror" hidden></p>', "<p class=\"muted small\">The original issue moves to Cancelled and is marked as converted. The planning session will read the original material.</p><p id=\"issue-dlg-error\" class=\"ierror\" hidden></p>"),
    okFn: function () {
      $('issue-dlg-ok').disabled = true;
      window.shell.issueToReq(i.id).then(function (made) {
        if (made.error) return iDialogError(made.error);
        iCloseDialog();
        var req = made.requirement;
        if (made.images && made.images.length) firstImages[req.id] = made.images;
        firstSay[req.id] = uiText('这个需求是从问题 ', "This requirement came from issue ") + made.issue.num + uiText(' 转来的：原来的描述和完整对话在 ', ". The description and full conversation are in ") + made.thread_file + uiText('，先读一下', ". Read them ") + (made.images && made.images.length ? uiText('，附的截图也看一下', " and review any attached screenshots ") : '') + uiText('，再开始立项。', " before planning.");
        reqs = [req].concat(reqs.filter(function (r) { return r.id !== req.id; }));
        mergeIssue(made.issue); ishow(made.issue);
        itoast(uiText('已转为需求 ', "Converted to requirement ") + req.id + uiText('，到「需求」里接着立项。', ". Continue planning on the Requirements page."));
      });
      return false;
    } });
}
// 需求转问题：立项中的需求其实是现有功能的毛病。问题记在项目主目录里，需求留着，记下转成了哪条问题
function openFromReq(r) {
  var opts = uiText('<option value="">不关联</option>', "<option value=\"\">No linked feature</option>") + IS.features.map(function (f) { return '<option value="' + iesc(f.id) + '">' + iesc(f.id + ' ' + f.title) + '</option>'; }).join('');
  iOpenDialog({ title: uiText('转为问题', "Convert to issue"), ok: uiText('确认转换', "Confirm conversion"), body:
    '<p>「' + iesc(r.title) + uiText('」会记成一条问题，需求说明作为问题的现象，由 AI 先初判。需求本身留着，记下转成了哪条问题。</p>', "” will be recorded as an issue using its description, then sent for AI triage. The requirement will keep a link to the issue.</p>")
    + uiText('<label class="field">关联功能点（选填）<select name="feature">', "<label class=\"field\">Link a feature (optional)<select name=\"feature\">") + opts + '</select></label><p id="issue-dlg-error" class="ierror" hidden></p>',
    okFn: function (form) {
      $('issue-dlg-ok').disabled = true;
      window.shell.issueFromReq({ id: r.id, feature: form.elements.feature.value }).then(function (made) {
        if (made.error) return iDialogError(made.error);
        iCloseDialog(); mergeIssue(made); view('issues', made.id);
        itoast(uiText('已转为问题 ', "Converted to issue ") + made.num + '。');
      });
      return false;
    } });
}
function openDrop(i) {
  iOpenDialog({ title: uiText('不处理', "Close issue"), ok: uiText('确定', "OK"), body: uiText('<label class="field">原因，或重复的问题编号（如 #18）<input name="why" maxlength="120" placeholder="一句话就够"></label><p id="issue-dlg-error" class="ierror" hidden></p>', "<label class=\"field\">Reason or duplicate issue number (e.g. #18)<input name=\"why\" maxlength=\"120\" placeholder=\"One sentence is enough\"></label><p id=\"issue-dlg-error\" class=\"ierror\" hidden></p>"),
    okFn: function (form) {
      var v = form.elements.why.value.trim();
      if (!v) { form.elements.why.focus(); return false; }
      $('issue-dlg-ok').disabled = true;
      window.shell.setIssue({ id: i.id, action: 'dismiss', value: v }).then(function (made) {
        if (made.error) return iDialogError(made.error);
        iCloseDialog(); mergeIssue(made); ishow(made);
      });
      return false;
    } });
}

// 一起修：把勾选的几条放进同一个修复会话。弹出确认，列出这几条，再开一个修复会话
function openBatchFix() {
  var l = iPickList();
  if (l.length >= 2) openFix(l);
}
function openFix(items) {
  if (items.some(function (i) { return IS.busy[i.id]; })) return;
  var batch = items.length > 1;
  var ids = items.map(function (i) { return i.id; });
  var hint = batch ? iMultiHint(items) : '';
  var details = batch
    ? uiText('<p>这几条会放进同一个修复会话，修好后逐条交给你确认。</p><ul class="blist">', "<p>These issues share one fix session. Review each fix separately when it is ready.</p><ul class=\"blist\">") + items.map(function (x) {
      return '<li><span class="mono">' + x.num + '</span> ' + iesc(x.title) + '<span class="f">' + (x.feature ? iesc(featName(x.feature)) : uiText('未关联功能点', "No linked feature")) + '</span></li>';
    }).join('') + '</ul>' + (hint ? '<p class="small" style="color:var(--amber)">' + hint + '</p>' : '')
      + uiText('<p class="muted small">每一条单独确认；同批都确认了，才把这次修复合并进项目目录。</p>', "<p class=\"muted small\">The fix merges into the project checkout after you confirm every issue in this batch.</p>")
    : '<p>' + iesc(items[0].num + ' ' + items[0].title) + '</p>';
  iOpenDialog({ title: batch ? uiText('一起修 ', "Fix ") + items.length + uiText(' 条问题', " issues together") : uiText('交给 AI 修复', "Ask AI to fix"),
    ok: batch ? uiText('开始修复 ', "Start fixing ") + items.length + uiText(' 条', " issues") : uiText('开始修复', "Start fix"),
    body: details + '<div class="req-session-fields">'
      + '<label>CLI<select id="issue-fix-cli"></select></label>'
      + uiText('<label>模型<select id="issue-fix-model"></select></label>', "<label>Model<select id=\"issue-fix-model\"></select></label>")
      + '</div><p id="issue-dlg-error" class="ierror" hidden></p>',
    okFn: function () {
      if (ok.disabled) return false;
      dialog.pending = true;
      ok.disabled = cancel.disabled = cli.disabled = model.disabled = true;
      window.shell.fixIssue(batch ? ids : ids[0], picker.choice()).then(function (made) {
        if (made?.error) return showError(made.error);
        cancel.disabled = false;
        iCloseDialog();
        if (batch) IS.pick = {};
        IS.picker = false;
        IS.sel = ids[0]; IS.pane[ids[0]] = 'term';
        var first = issueById(ids[0]); if (first) iReveal(first);
        renderIssues();
      }, showError);
      return false;
      function showError(error) {
        dialog.pending = false;
        cancel.disabled = cli.disabled = model.disabled = false;
        iDialogError(error.message || String(error));
      }
    } });
  var dialog = IDLG;
  var ok = $('issue-dlg-ok'), cancel = $('issue-dlg-cancel');
  var cli = $('issue-fix-cli'), model = $('issue-fix-model');
  var picker = sessionPicker(cli, model);
  ok.disabled = cli.disabled = model.disabled = true;
  window.shell.getSettings().catch(function () { return {}; }).then(function (settings) {
    if (IDLG !== dialog) return;
    if (settings.catalogs) applyRequirementCatalogs(settings.catalogs);
    var agent = Object.hasOwn(RequirementSessions.catalog, settings.agent) ? settings.agent : 'claude';
    picker.reset(agent);
    ok.disabled = cli.disabled = false;
    window.shell.discoverCliModels().then(function (catalogs) {
      applyRequirementCatalogs(catalogs);
      if (IDLG === dialog && !dialog.pending) picker.refresh();
    }).catch(function () {});
  });
}
// 确认完一条以后：同批里下一条还在等你确认的，直接跳过去，并提醒还剩几条
function iAdvance(made) {
  var left = (made.siblings || []).filter(function (x) { return x.state === 'review' && !x.confirmed; }).sort(function (a, b) { return a.id < b.id ? 1 : -1; });
  if (!left.length) return;
  IS.sel = left[0].id; IS.picker = false;
  var next = issueById(left[0].id); if (next) iReveal(next);
  itoast(uiText('还有 ', "") + left.length + uiText(' 条等你确认', " still awaiting confirmation"));
}

// ---------- 发送回复 ----------
function iSend() {
  var i = curIssue();
  if (!i || IS.busy[i.id] || i.state === 'resolved' || i.state === 'closed') return;
  var text = (IS.drafts[i.id] || '').trim(), shots = (IS.shots[i.id] || []).slice();
  if (!text && !shots.length) return;
  if (IS.hot[i.id] && i.state === 'review' && !text) { IS.errors[i.id] = uiText('说一句还有什么问题。', "Describe what is still wrong."); renderIssues(); return; }
  var call = IS.hot[i.id] && i.state === 'review' ? window.shell.rejectIssue({ id: i.id, text: text, images: shots }) : window.shell.replyIssue({ id: i.id, text: text, images: shots });
  iBusyDo(i, call, function (made) { IS.drafts[i.id] = ''; IS.shots[i.id] = []; delete IS.hot[i.id]; mergeIssue(made); iReveal(made); });
}
function iAddShots(paths, target) {
  paths = paths.filter(Boolean);
  if (target === 'new') { paths.forEach(function (p) { if (newShots.indexOf(p) < 0) newShots.push(p); }); refreshNewShots(); return; }
  var i = curIssue(); if (!i) return;
  IS.shots[i.id] = (IS.shots[i.id] || []).concat(paths); IS.rkey = ''; renderIssueReply(i);
  var t = $('issue-text'); if (t) t.focus();
}
function mergeIssue(made) {
  if (!made || !made.id) return;
  IS.issues = [made].concat(IS.issues.filter(function (x) { return x.id !== made.id; })).sort(function (a, b) { return a.id < b.id ? 1 : -1; });
}

// ---------- 按钮 ----------
function issueClick(e) {
  var t = e.target.closest('[data-act]');
  if (!t) { if (IS.picker && !e.target.closest('.picker')) { IS.picker = false; renderIssueHead(curIssue()); } return; }
  var a = t.dataset.act, i = curIssue();
  var set = function (action, value) { return iBusyDo(i, window.shell.setIssue({ id: i.id, action: action, value: value || '' }), function (made) { mergeIssue(made); iReveal(made); }); };
  switch (a) {
    case 'tab': IS.tab = t.dataset.k; IS.kind = 'all'; IS.picker = false; iEnsureSel(); renderIssues(); break;
    case 'chk': if (t.checked) IS.pick[t.dataset.id] = true; else delete IS.pick[t.dataset.id]; renderIssueSelbar(); return;
    case 'selclear': IS.pick = {}; renderIssues(); break;
    case 'selfeat': IS.issues.filter(function (x) { return iEligible(x) && x.feature === t.dataset.c; }).forEach(function (x) { IS.pick[x.id] = true; }); renderIssues(); break;
    case 'batchfix': openBatchFix(); break;
    case 'jump': {
      var to = issueById(t.dataset.id);
      if (!to) break;
      IS.sel = to.id;
      IS.picker = false;
      if (t.dataset.pane === 'thread') IS.pane[to.id] = 'thread';
      iReveal(to);
      renderIssues();
      break;
    }
    case 'unconfirm': iBusyDo(i, window.shell.unconfirmIssue(i.id), function (made) { mergeIssue(made); iReveal(made); }); break;
    case 'kind': IS.kind = t.dataset.k; iEnsureSel(); renderIssues(); break;
    case 'source':
      IS.source = IS.source === t.dataset.k ? 'all' : t.dataset.k;
      IS.picker = false;
      iEnsureSel();
      renderIssues();
      $('issue-rows').scrollTop = 0;
      break;
    case 'open': IS.sel = t.dataset.id; IS.picker = false; renderIssues(); break;
    case 'finish-menu': iFinishMenu($('issue-finish-menu').hidden); break;
    case 'finish': openFinishIssue(i, t.dataset.k); break;
    case 'new': openNewIssue(); break;
    case 'pickfile': window.shell.pickImages().then(function (p) { iAddShots(p, 'new'); }); break;
    case 'attach': window.shell.pickImages().then(function (p) { iAddShots(p); }); break;
    case 'zoom': iOpenDialog({ title: uiText('截图', "Screenshot"), cancel: uiText('关闭', "Close"), body: uiText('<img class="big" alt="放大的截图" src="', "<img class=\"big\" alt=\"Enlarged screenshot\" src=\"") + iesc(ifile(t.dataset.src)) + '">' }); break;
    case 'rmshot':
      if ($('issue-dlg').open && $('issue-new-shots')) { newShots.splice(+t.dataset.k, 1); refreshNewShots(); }
      else if (i) { IS.shots[i.id].splice(+t.dataset.k, 1); IS.rkey = ''; renderIssueReply(i); }
      break;
    case 'send': iSend(); break;
    case 'pick': IS.picker = !IS.picker; renderIssueHead(i); break;
    case 'setfeat': IS.picker = false; set('feature', t.dataset.c); break;
    case 'unfeat': set('feature', 'none'); break;
    case 'featfilter': IS.feat = i.feature; IS.tab = i.state; renderIssues(); break;
    case 'pane': IS.pane[i.id] = t.dataset.k; IS.rkey = ''; renderIssues(); break;
    case 'opensess': case 'openprobe': IS.pane[i.id] = 'term'; renderIssues(); break;
    case 'probe':
      // 交给 AI 排查：先记下（待处理的才行），再把右边切到排查会话；已经有排查会话就直接打开它，接着上次的
      iBusyDo(i, window.shell.probeIssue(i.id), function (made) { mergeIssue(made); IS.pane[i.id] = 'term'; });
      break;
    case 'fix': case 'fixasbug':
      if (i) openFix([i]);
      break;
    case 'toreq': openToReq(i); break;
    case 'toreq-kind': set('kind', 'request').then(function (made) { if (made) openToReq(made); }); break;
    case 'defer': set('defer'); break;
    case 'undefer': set('undefer'); break;
    case 'solved': set('solved'); break;
    case 'stillno': IS.drafts[i.id] = uiText('还是不行，', "Still unresolved: "); IS.rkey = ''; renderIssueReply(i); iFocusReply(); break;
    case 'drop': openDrop(i); break;
    case 'retry': window.shell.retryIssue(i.id); break;
    case 'reopen': set('reopen'); break;
    case 'gotoreq': view('reqs'); select(i.requirement); break;
    case 'stillbad': IS.hot[i.id] = true; IS.rkey = ''; renderIssueReply(i); iFocusReply(); break;
    case 'cancelbad': delete IS.hot[i.id]; IS.rkey = ''; renderIssueReply(i); break;
    case 'mergeretry': retryMerge(fixRun(i), uiText('修复会话', "Fix session")).then(function (made) { if (made && made.error) { IS.errors[i.id] = made.error; } renderIssues(); }); break;
    case 'mergeabort': window.shell.abortMerge(fixRun(i)).then(renderIssues); break;
    case 'confirmfix':
      var others = (i.siblings || []).filter(function (x) { return !x.confirmed; });
      if (!window.confirm(uiText('确认问题 ', "Confirm issue ") + i.num + '「' + i.title + uiText('」修好了？\n\n', " is fixed?\n\n") + (others.length
        ? uiText('这一条是和 ', "This issue shares a fix with ") + others.map(function (x) { return x.num; }).join('、') + uiText(' 一起修的：先记下你对这一条的确认，同批都确认了，才把修复 ', ". RelayForge will record your confirmation. Fix ") + i.fix.id + uiText(' 的改动合并进项目目录。确认了还可以撤回。', " merges when the whole batch is confirmed. You can withdraw your confirmation before the merge.")
        : uiText('确认后，修复 ', "After confirmation, fix ") + i.fix.id + uiText(' 的改动会合并进项目目录现在的分支，修复会话和工作目录会收掉。有冲突时会回到修复会话里让 AI 解决，再自动合并；别的需求或修复正在合并时会排队。', " merges into the current branch and its session and worktree close. Conflicts go back to the fix session before retrying. The merge waits if another is running.")))) break;
      iBusyDo(i, window.shell.confirmIssue(i.id), function (made) {
        if (made.merging) { if (made.merging.phase === 'waiting') itoast(uiText('已排队：轮到它会自动合并。', "Queued. It will merge when its turn comes.")); return; }
        mergeIssue(made); iReveal(made);
        if (made.state === 'resolved') { dropFixTerm('fix:' + i.fix.id); delete IS.pane[i.id]; itoast(i.batch > 1 ? uiText('同批 ', "All ") + i.batch + uiText(' 条都已解决，改动合并进去了。', " issues in the batch are resolved and merged.") : uiText('问题 ', "Issue ") + made.num + uiText(' 已解决，改动合并进去了。', " resolved and merged.")); }
        else if (made.confirmed_waiting) iAdvance(made);
      });
      break;
  }
}
function iFocusReply() { var t = $('issue-text'); if (t) { t.focus(); t.setSelectionRange(t.value.length, t.value.length); } }

function wireIssues() {
  $('view-issues').addEventListener('click', function () { view('issues'); });
  $('issues').addEventListener('click', issueClick);
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.finish-action')) iFinishMenu(false);
  });
  $('issue-dlg').addEventListener('click', function (e) {
    var t = e.target.closest('[data-act]');
    if (!t) return;
    if (t.dataset.act === 'dcancel') return iCloseDialog();
    issueClick(e);
  });
  // 「确定」和在输入框里回车都走这里；okFn 返回 false 表示它自己会关（要等主进程回话）
  $('issue-dlg-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var o = IDLG; if (!o || o.pending) return;
    if (typeof o.okFn !== 'function' || o.okFn($('issue-dlg-form')) !== false) iCloseDialog();
  });
  $('issue-dlg').addEventListener('cancel', function (e) { if (IDLG && IDLG.pending) e.preventDefault(); });
  $('issue-dlg').addEventListener('close', function () { IDLG = null; });
  $('issue-feat').addEventListener('change', function (e) { IS.feat = e.target.value; iEnsureSel(); renderIssues(); });
  $('issues').addEventListener('input', function (e) { var i = curIssue(); if (e.target.id === 'issue-text' && i) { IS.drafts[i.id] = e.target.value; iAutosize(e.target); } });
  $('issues').addEventListener('keydown', function (e) {
    var t = e.target;
    var menu = $('issue-finish-menu');
    if (e.key === 'Escape' && menu && !menu.hidden) { iFinishMenu(false); $('issue-finish-toggle').focus(); return; }
    if (t.id === 'issue-text' && e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); iSend(); return; }
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && !/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) && !t.closest('.fixterm') && !e.altKey && !e.ctrlKey && !e.metaKey) {
      var v = iVisible(); if (!v.length) return;
      var k = v.findIndex(function (x) { return x.id === IS.sel; });
      k = Math.max(0, Math.min(v.length - 1, k + (e.key === 'ArrowDown' ? 1 : -1)));
      e.preventDefault(); IS.sel = v[k].id; IS.picker = false; renderIssues();
      var row = document.querySelector('#issues .irow[data-id="' + IS.sel + '"]'); if (row) { row.scrollIntoView({ block: 'nearest' }); row.focus(); }
    }
    if (e.key === 'Escape' && IS.picker) { IS.picker = false; renderIssueHead(curIssue()); }
  });
  // 空格：焦点不在输入框里时，勾选或取消勾选当前这一行（和点它前面的勾选框一样）
  document.addEventListener('keydown', function (e) {
    if (e.key !== ' ' || $('issues').hidden || IS.tab !== 'todo' || $('issue-dlg').open || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    var t = e.target;
    if (!(t === document.body || (t.classList && t.classList.contains('irow')))) return;
    var c = curIssue(); if (!iEligible(c)) return;
    e.preventDefault();
    if (IS.pick[c.id]) delete IS.pick[c.id]; else IS.pick[c.id] = true;
    renderIssueRows(); renderIssueSelbar();
    if (t !== document.body) { var row = document.querySelector('#issues .irow[data-id="' + c.id + '"]'); if (row) row.focus(); }
  });
  document.addEventListener('keyup', function (e) { if (e.key === ' ' && e.target.classList && e.target.classList.contains('irow') && iEligible(curIssue())) e.preventDefault(); });
  // 粘贴截图：记问题的对话框里贴给新问题，回复框里贴给这次回复。存图复用需求那边的逻辑（toPaths，见 compose.js）
  document.addEventListener('paste', function (e) {
    if ($('issues').hidden && !$('issue-dlg').open) return;
    var files = Array.prototype.slice.call((e.clipboardData || {}).files || []).filter(isImage);
    if (!files.length) return;
    var target = $('issue-dlg').open && $('issue-new-shots') ? 'new' : e.target.id === 'issue-text' ? 'reply' : null;
    if (!target) return;
    e.preventDefault(); e.stopPropagation();
    toPaths(files).then(function (p) { iAddShots(p, target === 'new' ? 'new' : undefined); });
  }, true);
  window.addEventListener('resize', refitFix);

  window.shell.onIssues(function (found) {
    var before = curIssue();
    IS.issues = found.issues || []; IS.features = found.features || [];
    var now = curIssue();
    if (before && now && before.state !== now.state) iReveal(now); // 你看着的这条换了状态（交回了、判完了）：左边跟着切到它所在的页签
    if (!IS.loaded) { IS.loaded = true; var first = IS.issues.filter(function (i) { return i.state === 'todo' && !i.deferred; })[0]; if (first && !IS.sel) IS.sel = first.id; }
    if (IS.sel && !curIssue()) IS.sel = null;
    // 开始修复了、问题不在待处理了：它的排查会话收起来（主进程那边已经关了）
    Object.keys(fixTerms).forEach(function (run) {
      if (run.indexOf('probe:') !== 0) return;
      var x = issueById(run.slice(6));
      if (!x || !probeOpen(x)) { dropFixTerm(run); if (x) delete IS.pane[x.id]; }
    });
    renderIssues();
  });
  window.shell.onMerge(function () { renderIssues(); });
  window.shell.onWaiting(function () { issueBadge(); if (!$('issues').hidden) { renderIssueRows(); var i = curIssue(); if (i) { renderIssueHead(i); if (IS.pane[i.id] !== 'term') renderIssueDetail(); } } });
  window.shell.onRunning(function () { if (!$('issues').hidden) { renderIssueRows(); if (curIssue() && IS.pane[IS.sel] !== 'term') renderIssueDetail(); } });
  window.shell.onData(function (p) { if (fixTerms[p.id]) fixTerms[p.id].term.write(p.data); });
  window.shell.onExit(function (p) {
    var t = fixTerms[p.id]; if (!t) return;
    t.dead = true;
    var name = p.id.indexOf('probe:') === 0 ? uiText('排查会话', "Investigation session") : uiText('修复会话', "Fix session");
    var message = p.headless && p.kind !== 'exit'
      ? uiText('在后台的这一轮做完了。', "Background turn finished.") : uiText('已退出。', " exited.");
    t.term.write('\r\n\x1b[2m' + name + message + uiText('切回「对话」再切到「', "Switch to Conversation and back to “") + name + uiText('」可以在终端里接着聊。\x1b[0m\r\n', "” to resume in the terminal.\u001b[0m\r\n"));
  });
  // 主进程要往修复会话里说一句（你补充了一句、退回了），会话还没开着：开起来，这句作为第一句话
  window.shell.onIssueSay(function (p) { fixTerm(p.id, p.say); });
  window.shell.onIssueResolve(function () { // 主进程已经把话交给修复会话（开着经收件箱，没开着在后台跑一轮）
    itoast(uiText('合并回去之前，先在修复会话里把目标分支合并进来：已经交给 AI，弄好会自动合并回去。', "The fix session will merge the target branch and resolve conflicts. RelayForge will complete the merge afterward."));
    renderIssues();
  });
  window.shell.onIssueMerged(function (p) {
    dropFixTerm(p.id);
    if (p.issue && p.issue.id) { mergeIssue(p.issue); if (p.issue.state === 'resolved') itoast(p.issue.batch > 1 ? uiText('同批 ', "All ") + p.issue.batch + uiText(' 条都已解决，改动合并进去了。', " issues in the batch are resolved and merged.") : uiText('问题 ', "Issue ") + p.issue.num + uiText(' 已解决，改动合并进去了。', " resolved and merged.")); }
    renderIssues();
  });
  window.shell.listIssues().then(function (found) { if (found && found.issues && !IS.loaded) { IS.loaded = true; IS.issues = found.issues; IS.features = found.features || []; renderIssues(); } });
}
// 打开问题页（view('issues')），可以直接停在某一条
function showIssues(id) {
  var i = id ? issueById(id) : null;
  if (i) { iReveal(i); IS.sel = i.id; }
  else iEnsureSel();
  IS.rkey = '';
  renderIssues();
}
