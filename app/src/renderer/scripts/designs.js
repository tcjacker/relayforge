// 设计稿：左边选中的需求下面列出「对话」和它的每一份设计稿，点哪个中间就整块显示哪个，像标签页一样切换。
// 看设计稿时终端只是藏起来，会话不断；点「对话」回来接着聊。
// 预览是 sandbox="allow-scripts" 的 iframe，没有 allow-same-origin：页面里的脚本能跑，但碰不到应用。
// 新画的一份不抢走聊天，只在左边标「新」；改过的那份正在看时自动刷新（看文件的修改时间）。
var shown = {}, fresh = {}, knownDesigns = null;
var fileUrl = function (p) { return encodeURI('file://' + p).replace(/#/g, '%23').replace(/\?/g, '%3F'); };
var designOf = function (r) { var id = r && shown[r.id]; return id && id !== 'test' && id !== 'accept' ? (r.designs || []).filter(function (d) { return d.id === id; })[0] : null; };
// 测试会话：进入过测试阶段（有测试会话，或者现在就在测试中）的开发中需求，左栏多一个「测试」标签，中间是它的终端（编号 test:需求编号）
var hasTestTab = function (r) { return !!(r && r.state === 'open' && r.worktree && ((r.test_sessions || []).length || stageOf(r) === 'test')); };
// 这个需求现在中间显示的是哪个会话：开发（立项）会话，还是测试会话
var termOf = function (id) { var r = byId(id); return r && shown[id] === 'test' && hasTestTab(r) ? 'test:' + id : id; };
function noteDesigns(found) {
  var first = !knownDesigns;
  knownDesigns = knownDesigns || {};
  found.forEach(function (r) {
    var ids = (r.designs || []).map(function (d) { return d.id; }), had = knownDesigns[r.id] || [];
    if (!first) ids.filter(function (id) { return had.indexOf(id) < 0; }).forEach(function (id) { fresh[id] = true; });
    knownDesigns[r.id] = ids;
  });
}
var designNote = function (d) {
  var what = d.features.concat(d['new'].map(function (x) { return uiText('新增：', "Added: ") + x; })).join('、');
  return [what ? uiText('画的：', "By: ") + what : '', d.status === 'current' ? uiText('现在的样子', "Current design") : d.status === 'superseded' ? uiText('已被替代', "Superseded") : '', d.changed ? uiText('改于 ', "Updated ") + d.changed.slice(5) : ''].filter(Boolean).join(' · ');
};
// 左栏里选中需求下面的子项：对话 +（进入过测试阶段的）测试 + 每份设计稿。都没有就不列，和原来一样只有聊天
function designTabs(r) {
  var list = (r && r.designs) || [], test = hasTestTab(r), acc = typeof hasAcceptTab === 'function' && hasAcceptTab(r);
  if ((!list.length && !test && !acc) || !hasChat(r)) return [];
  var tabOf = function (id, label, note, isNew) {
    var li = el('li', 'sub' + ((shown[r.id] || null) === id ? ' on' : '') + (id === 'accept' && stageOf(r) !== 'check' ? ' early' : ''));
    li.appendChild(el('i', id === 'test' ? 'kind test' : id === 'accept' ? 'kind accept' : id ? 'kind design' : 'kind chat'));
    li.appendChild(el('span', '', label));
    if (!id || id === 'test') li.insertAdjacentHTML('beforeend', InboxModel.badge(id === 'test' ? 'test:' + r.id : r.id));
    if (isNew) li.appendChild(el('em', 'new', uiText('新', "New")));
    if (note) li.title = note;
    li.addEventListener('click', function (e) { e.stopPropagation(); openTab(r, id); });
    return li;
  };
  var w = waiting['test:' + r.id];
  var up = typeof BENCH !== 'undefined' && ((BENCH.run[r.id] || {}).services || []).filter(function (s) { return s.status === 'running' || s.status === 'starting'; }).length;
  return [tabOf(null, uiText('对话', "Chat"), '', false)].concat(test ? [tabOf('test', uiText('测试', "Testing") + (stageOf(r) === 'test' ? uiText('（测试中）', " (testing)") : ''), w ? uiText('测试会话', "Test session") + WAIT[w.kind] : uiText('这个需求的测试会话：按验收条件写测试集或检查步骤，不改产品代码', "The test session uses the acceptance criteria to prepare test sets or manual checks. It leaves product code alone."), false)] : [])
    .concat(acc ? [tabOf('accept', uiText('人工验收', "Manual acceptance") + (up ? '（' + up + uiText(' 个服务开着）', " services running)") : stageOf(r) === 'check' ? uiText('（待验收）', " (ready for acceptance)") : ''), stageOf(r) === 'check' ? uiText('用这个需求的代码把程序跑起来，你自己试，然后在右边逐条点', "Run this requirement's code, try it, and mark each criterion on the right") : uiText('还没到待验收，也可以先把程序跑起来试', "You can start the app and try it before acceptance"), false)] : [])
    .concat(list.map(function (d) { return tabOf(d.id, d.title, designNote(d), !!fresh[d.id]); }));
}
function openTab(r, id) {
  shown[r.id] = id;
  if (id === 'test' && typeof viewTab === 'function') stageView[r.id] = { viewing: 'test', stage: stageOf(r) }; // 中间切到测试会话，右边跟着切到「测试」标签（单向：右边点标签不动中间）
  if (id) delete fresh[id];
  if (id === 'accept') { list(); panel(); return; } // 人工验收：中间整块是服务和日志（bench.js），终端藏起来，会话不断
  if (!id || id === 'test') { // 对话和测试都是终端：开着就切过去，没开就打开（测试会话第一次打开时由程序给开场白）
    var key = termOf(r.id);
    if (current === r.id) { terminal(key); show(key); }
  }
  list(); panel();
  var t = terms[termOf(r.id)];
  if ((!id || id === 'test') && t && !t.dead) { requestAnimationFrame(refit); t.term.focus(); }
}
function preview() {
  var r = tab === 'reqs' ? byId(current) : null, d = designOf(r), on = !!d, frame = $('preview-frame');
  var was = !$('preview').hidden || !$('bench').hidden;
  var bench = !on && typeof benchForReq === 'function' && benchForReq(); // 人工验收标签
  $('preview').hidden = !on;
  $('terms').hidden = on || bench;
  $('image').classList.toggle('away', on || bench);
  if (on) {
    var src = fileUrl(d.path) + '?v=' + d.mtime;
    if (frame.dataset.src !== src) { frame.src = src; frame.dataset.src = src; }
    $('preview-title').textContent = d.title;
    $('preview-meta').textContent = [d.id, designNote(d)].filter(Boolean).join(' · ');
  } else {
    if (frame.dataset.src) { frame.src = 'about:blank'; frame.dataset.src = ''; }
  }
  var message = $('finished');
  message.hidden = on || bench || !r || hasChat(r);
  if (!message.hidden) {
    if (r.state === 'dropped') message.textContent = uiText('这个需求已取消。在左边列表或看板上右击它，选「恢复取消前的状态」后可以继续对话。', "This requirement is cancelled. Right-click it in the list or board and restore its previous status to continue the conversation.");
    else if (r.state === 'done') message.textContent = uiText('这个需求已经确认完成。右边是它的内容和完成记录。', "This requirement is complete. Its details and completion record are on the right.");
    else if (r.state === 'draft') message.textContent = uiText('这个需求还在立项中，当前没有可打开的会话。你可以在右边查看需求内容。', "This requirement is still in planning and has no session to open. Its details are on the right.");
    else message.textContent = uiText('当前没有可打开的会话。你可以在右边查看需求内容和进展。', "No session is available. The requirement and its progress are on the right.");
  }
  if (was !== (on || bench)) requestAnimationFrame(refit);
}

// 绑定事件、开始加载：由 main.js 按顺序调用
function wireDesigns() {
  $('preview-back').addEventListener('click', function () { var r = byId(current); if (r) openTab(r, null); });
  $('preview-window').addEventListener('click', function () { var d = designOf(byId(current)); if (d) window.shell.openDesign(d.path, d.title); });
}
