// 手动改状态：左侧需求列表和看板卡片上右击出菜单，看板卡片拖到别的栏。
// 规则由程序算好放在 r.moves 里（能改为 null，不能改是原因）；改到已完成、已取消先弹确认框，其余直接改并提示。
// 已取消的需求在这里「恢复取消前的状态」（rf.py req restore）。
var menuFor = null, dragging = null;
var movesOf = function (r) { return r.moves || {}; };
var lockedReq = function (r) { return r.state === 'done' || r.state === 'dropped'; };
var stageName = function (key) { return (STAGES.filter(function (s) { return s[0] === key; })[0] || [key, key])[1]; };

function say(text, bad) {
  $('notice').replaceChildren(el('span', bad ? 'notice-bad' : '', text));
  $('notice').hidden = false;
  clearTimeout(say.timer); say.timer = setTimeout(function () { $('notice').hidden = true; }, bad ? 6000 : 3500);
}

// 已取消的需求：「保留」它的分支（rf.py req keep），不被按设置里的天数和个数清理。分支已经清理掉的没有可保留的
function keepTitle(r) {
  return r.keep_branch ? uiText('分支 ', "Branch ") + r.branch + uiText(' 保留中，不会被自动清理。点一下取消保留，到期会和别的已取消需求一样清理', " is pinned. Unpin it to allow automatic cleanup when it expires.")
    : uiText('保留分支 ', "Pin branch ") + r.branch + uiText('：不按设置里的天数和个数清理，以后随时能恢复这个需求', " to keep it beyond the automatic cleanup limits.");
}
function keepButton(r) {
  if (r.state !== 'dropped' || r.branch_removed || (states[r.id] && states[r.id].demo)) return null;
  var b = el('button', 'req-keep' + (r.keep_branch ? ' on' : ''));
  b.type = 'button'; b.title = keepTitle(r); b.setAttribute('aria-pressed', String(!!r.keep_branch));
  b.setAttribute('aria-label', (r.keep_branch ? uiText('取消保留', "Unpin") : uiText('保留', "Pin")) + '《' + r.title + uiText('》的分支', "” branch"));
  b.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12v18l-6-4-6 4z"/></svg>';
  b.addEventListener('click', function (event) { event.stopPropagation(); toggleKeep(r); });
  return b;
}
function toggleKeep(r) {
  var keep = !r.keep_branch;
  window.shell.keepReq(r.id, keep).then(function (made) {
    if (made && made.error) { say(made.error, true); return; }
    say('「' + r.title + '」' + (keep ? uiText('的分支设为保留，不会被自动清理。', " is pinned and will be kept.") : uiText('的分支不再保留，到期会清理。', " is unpinned and can expire.")));
  }, function (error) { say(error.message, true); });
}

function openStageMenu(r, x, y) {
  if (states[r.id] && states[r.id].demo) return; // 虚构演示记录不能改
  menuFor = r.id; list();
  var m = $('stage-menu'); m.replaceChildren();
  m.appendChild(el('div', 'head', r.title));
  menuItem(m, uiText('打开', "Open"), function () { if (tab !== 'reqs') view('reqs'); select(r.id); });
  if (r.state === 'draft') menuItem(m, uiText('转为问题…', "Convert to issue…"), function () { openFromReq(r); }); // 立项中的需求其实是个毛病：见 issue-actions.js
  m.appendChild(el('hr'));
  var head = menuItem(m, uiText('修改状态', "Change status"), null); head.appendChild(el('span', 'k', '▸')); head.classList.add('has-sub');
  var sub = el('div', 'stage-menu sub');
  STAGES.forEach(function (s) {
    var why = movesOf(r)[s[0]], it = menuItem(sub, s[1], why ? null : function () { changeStage(r, s[0]); }, stageOf(r) === s[0] ? '✓' : '');
    if (why) { it.classList.add('dis'); it.title = why; }
  });
  sub.appendChild(el('div', 'hint', lockedReq(r) ? (r.state === 'done' ? uiText('已完成的需求已经合并进主线，状态不能再改。', "Completed requirements have been merged and their status is final.") : uiText('已取消的需求只读，要接着做请用下面的「恢复取消前的状态」。', "Cancelled requirements are read-only. Restore the previous status to continue.")) : uiText('灰掉的项鼠标停上去能看到原因。', "Hover over an unavailable action for details.")));
  head.appendChild(sub);
  if (!lockedReq(r)) { m.appendChild(el('hr')); menuItem(m, uiText('取消这个需求…', "Cancel this requirement…"), function () { changeStage(r, 'dropped'); }).classList.add('bad'); }
  if (r.state === 'dropped') {
    m.appendChild(el('hr')); menuItem(m, uiText('恢复取消前的状态', "Restore previous status"), function () { restoreStage(r); }).title = uiText('重建原分支的工作目录，内容、代码、会话和取消前所在的栏都保留', "Recreate the worktree and restore the original column, description, code, and conversations");
    if (!r.branch_removed) menuItem(m, r.keep_branch ? uiText('取消保留分支', "Unpin branch") : uiText('保留分支', "Pin branch"), function () { toggleKeep(r); }).title = keepTitle(r);
  }
  m.hidden = false;
  var w = m.offsetWidth, h = m.offsetHeight;
  m.style.left = Math.max(4, Math.min(x, innerWidth - w - 230)) + 'px';
  m.style.top = Math.max(4, Math.min(y, innerHeight - h - 8)) + 'px';
  sub.classList.toggle('up', y + 200 > innerHeight);
}
function menuItem(menu, text, fn, check) {
  var it = el('div', 'mi'); it.appendChild(el('span', 'chk', check || '')); it.appendChild(el('span', '', text));
  if (fn) it.addEventListener('click', function (e) { e.stopPropagation(); closeStageMenu(); fn(); });
  menu.appendChild(it); return it;
}
function closeStageMenu() {
  if ($('stage-menu').hidden) return;
  $('stage-menu').hidden = true; menuFor = null; list();
}
function onContext(node, r) {
  node.addEventListener('contextmenu', function (e) { e.preventDefault(); e.stopPropagation(); openStageMenu(r, e.clientX, e.clientY); });
}

// 改状态：会合并或会取消的，先问；其余直接改
// to 是 'skip'：面板上的「跳过测试，直接验收」按钮（rf.py req skip-test，记成跳过测试）；菜单和拖动从测试中改到待验收记成手动放行
function changeStage(r, to) {
  var why = movesOf(r)[to === 'skip' ? 'check' : to];
  if (why) return say(why, true);
  // 开发中点「交给测试」/「跳过测试」：计划没做完（或没记计划）先问一句；全做完不多问
  if ((to === 'test' || to === 'skip') && stageOf(r) === 'build' && !r.gapAsked) {
    var gap = StageTabsModel.planGap(((states[r.id] || {}).state || {}).plan);
    if (gap) {
      var skipping = to === 'skip';
      return askStage(skipping ? uiText('计划还没做完，仍要跳过测试？', "Plan unfinished. Skip testing anyway?") : uiText('计划还没做完，仍要交给测试？', "Plan unfinished. Send to testing anyway?"),
        [StageTabsModel.gapLine(gap, skipping)], skipping ? uiText('仍要跳过', "Skip anyway") : uiText('仍要交给测试', "Send anyway"), 'primary',
        function () { changeStage(Object.assign({}, r, { gapAsked: true }), to); });
    }
  }
  if (to === 'done') {
    var checks = r.sections['验收条件'] || [];
    return testsBeforeDone(r, function () { askStage(uiText('确认完成并合并？', "Mark complete and merge?"), [uiText('把「', "Mark “") + r.title + uiText('」标成已完成。', "” complete.")].concat(checks.length ? [uiText('验收条件：', "Acceptance criteria:")].concat(checks.map(function (c) { return '· ' + c; })) : []).concat([uiText('分支 ', "Branch ") + r.branch + uiText(' 会合并进项目目录现在的分支，它的会话和工作目录会收掉，之后状态不能再改。有冲突时回到它的会话里让 AI 解决再合并；别的需求正在合并时会排队。', " will merge into the branch checked out in the project. Its sessions and worktree will close, and the status will be final. Conflicts go to its session for resolution. The merge waits in the queue if another is running.")]), uiText('确认完成', "Mark complete"), 'primary', function () { applyStage(r, to); }); }, function (on) { if (on) say(uiText('正在跑「', "Running the test sets for “") + r.title + uiText('」的测试集，跑完再确认…', "” before confirming…")); });
  }
  // 跳过测试：进行中或测试中改到待验收。需求的记录里会如实写一行「跳过了测试」，先问一句
  if (!r.gapAsked && (to === 'check' || to === 'skip') && (stageOf(r) === 'build' || stageOf(r) === 'test') && r.testing && r.testing.flow) {
    var manual = to === 'check' && stageOf(r) === 'test';
    return askStage(uiText('跳过测试，直接验收？', "Skip testing and start acceptance?"), ['「' + r.title + uiText('」不经过', "” will skip ") + (stageOf(r) === 'test' ? uiText('测试会话的结论', "the test session's verdict") : uiText('测试', "testing")) + uiText('，直接进入待验收。', " and move to Ready for acceptance."),
      uiText('需求的测试结论和变更记录里会如实写「跳过了测试」', "The test verdict and change history will record that testing was skipped") + (manual ? uiText('（测试中直接改到待验收，记为「手动放行」）', " (as a manual override if it was already in testing)") : '') + uiText('，确认完成时的完成记录也会带上。', ". The completion record will include it too.")], uiText('跳过测试', "Skip testing"), 'primary', function () { applyStage(r, to); });
  }
  if (to === 'dropped') {
    return askStage(uiText('取消这个需求？', "Cancel this requirement?"), ['「' + r.title + uiText('」会移到「已取消」，只能查看，不能编辑或继续对话；以后可以右击它恢复。', "” will move to Cancelled and become read-only. Right-click it later to restore it."), uiText('它的会话会关掉、工作目录会收掉；聊过的内容', "Its sessions and worktree will close. Conversation history") + (r.state === 'draft' ? '' : uiText('和已经提交的代码', " and committed code")) + uiText('先提交到分支 ', " will be committed to ") + r.branch + uiText(' 上，分支保留以便恢复。没提交的代码改动也一起提交上去，不会丢。', " and the branch will be kept for recovery. Uncommitted code will be saved there too.") +
      uiText('分支上没有自己的提交时一起删掉；保留的分支在取消 30 天后、或取消的需求超过 20 个时由较早的开始清理。', "Branches without their own commits are deleted. The default cleanup keeps up to 20 cancelled branches for 30 days, with the oldest cleaned first.")], uiText('取消需求', "Cancel requirement"), 'danger', function () { applyStage(r, to); });
  }
  applyStage(r, to);
}
function applyStage(r, to) {
  var id = r.id, from = stageOf(r);
  window.shell.setReq(id, to).then(function (made) {
    if (made.error) return say(uiText('没改成：', "Couldn't change status: ") + made.error, true);
    if (to === 'done') return afterDone(id, made);
    if (to === 'dropped' || to === 'done' || r.state === 'draft') { // 会话已经被关掉：界面上的终端也收掉
      if (terms[id]) { terms[id].term.dispose(); terms[id].box.remove(); delete terms[id]; }
      delete states[id];
    }
    reqs = reqs.map(function (x) { return x.id === id ? made : x; });
    if (current === id) select(visibleReq(made) ? id : null); else list();
    panel();
    var short = r.title.length > 16 ? r.title.slice(0, 16) + '…' : r.title;
    say('「' + short + '」：' + stageName(from) + ' → ' + stageName(made.stage || to) + (to === 'dropped' ? (made.branch_removed ? uiText('，分支上没有自己的提交，一起删掉了。', ". The branch had no unique commits and was deleted.") : uiText('，聊过的内容保留在分支 ', ". Conversation history was kept on ") + made.branch + uiText(' 上。', ".")) : ''));
  });
}
// 恢复：回到取消前的状态和栏；左侧筛选把那一栏也勾上，免得恢复了却看不到
function restoreStage(r) {
  var id = r.id;
  window.shell.restoreReq(id).then(function (made) {
    if (made.error) return say(uiText('没恢复成：', "Couldn't restore: ") + made.error, true);
    delete states[id];
    reqs = reqs.map(function (x) { return x.id === id ? made : x; });
    reqFilter[stageOf(made)] = true;
    var box = document.querySelector('#req-filter input[value="' + stageOf(made) + '"]');
    if (box) box.checked = true;
    if (current === id) select(id); else list();
    panel();
    say('「' + (r.title.length > 16 ? r.title.slice(0, 16) + '…' : r.title) + uiText('」已恢复到', "” restored to ") + stageName(stageOf(made)) + '。');
  });
}
function askStage(title, lines, yes, cls, fn) {
  $('stage-dlg-title').textContent = title;
  $('stage-dlg-body').replaceChildren.apply($('stage-dlg-body'), lines.map(function (l) { return el('p', '', l); }));
  $('stage-dlg-yes').textContent = yes; $('stage-dlg-yes').className = cls;
  $('stage-dlg-yes').onclick = function () { $('stage-dlg').close(); fn(); };
  $('stage-dlg').showModal();
  $('stage-dlg-no').focus();
}

// 看板拖拽：拿起卡片时，放不进去的栏变灰，鼠标停上去看原因
function dragCard(card, r) {
  card.draggable = !lockedReq(r) && !(states[r.id] && states[r.id].demo);
  card.addEventListener('dragstart', function (e) {
    dragging = r.id; e.dataTransfer.setData('text/plain', r.id); e.dataTransfer.effectAllowed = 'move';
    setTimeout(function () {
      card.classList.add('dragging');
      document.querySelectorAll('#board .col').forEach(function (col) {
        var why = col.dataset.stage !== stageOf(r) && movesOf(r)[col.dataset.stage];
        col.classList.toggle('no', !!why); col.title = why ? uiText('不能拖到这里：', "Can't move here: ") + why : '';
      });
    }, 0);
  });
  card.addEventListener('dragend', function () { dragging = null; card.classList.remove('dragging'); clearCols(); });
}
function dropCol(col, stage) {
  col.dataset.stage = stage;
  col.addEventListener('dragover', function (e) { var r = byId(dragging); if (!r || movesOf(r)[stage]) return; e.preventDefault(); col.classList.add('over'); });
  col.addEventListener('dragleave', function (e) { if (!col.contains(e.relatedTarget)) col.classList.remove('over'); });
  col.addEventListener('drop', function (e) {
    e.preventDefault(); var r = byId(dragging); clearCols();
    if (r && stageOf(r) !== stage) changeStage(r, stage);
  });
}
function clearCols() {
  document.querySelectorAll('#board .col').forEach(function (col) { col.classList.remove('over', 'no'); col.title = ''; });
}

// 绑定事件、开始加载：由 main.js 按顺序调用
function wireStageMenu() {
  document.addEventListener('mousedown', function (e) { if (!$('stage-menu').contains(e.target)) closeStageMenu(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeStageMenu(); });
  window.addEventListener('blur', closeStageMenu);
  window.addEventListener('resize', closeStageMenu);
  $('stage-dlg-no').addEventListener('click', function () { $('stage-dlg').close(); });
}
