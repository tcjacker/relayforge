// 需求列表
var byId = function (id) { return reqs.filter(function (x) { return x.id === id; })[0]; };
var pastOf = function (id) { return past.filter(function (x) { return 'session:' + x.session_id === id; })[0]; };
function select(id) {
  var r = byId(id);
  if (id !== current && typeof stageView !== 'undefined') delete stageView[id]; // 打开一个需求：停在它现在的阶段
  current = id;
  if (r && r.state === 'dropped') $('quote').hidden = true;
  if (tab !== 'map') chosen[tab] = id;
  window.shell.viewing(tab === 'map' ? null : id);
  board(); // 先把看板收起来，终端才能按真实的宽度排版
  if (!id) { show(''); list(); panel(); return; }
  if (hasChat(r) || pastOf(id)) {
    var key = typeof termOf === 'function' ? termOf(id) : id; // 选的是「测试」标签时是测试会话
    terminal(key);
    show(key);
    refit();
    if (!designOf(r) && !(typeof shown !== 'undefined' && shown[id] === 'accept')) terms[key].term.focus();
    if (r && r.testing && (r.testing.needs_kick || r.testing.to_tell) && window.shell.openTesting) window.shell.openTesting(id); // 应用没开着时进了测试中：现在开出测试会话
  } else {
    show('');
    $('welcome').hidden = true;
  }
  list(); panel();
}

// 左侧需求列表的一条（选中时下面带它的对话、设计稿等标签）。inFolder：在依赖关系的文件夹里，往里缩一点
function reqRow(r, inFolder) {
    var li = el('li', 'req' + (inFolder ? ' in-folder' : '') + (r.id === current ? ' on' : '') + (r.id === menuFor ? ' ctx' : '') + (r.state === 'done' || r.state === 'dropped' ? ' done' : ''));
    var live = terms[r.id] && !terms[r.id].dead;
    li.insertAdjacentHTML('beforeend', InboxModel.badge(r.id) + InboxModel.badge('test:' + r.id));
    li.appendChild(el('b', '', r.title));
    var dn = DepsModel.listNote(r); // 前置需求：等谁、已取消、可以开始了
    if (dn) li.appendChild(el('small', 'dep ' + (dn === uiText('可以开始了', "Ready to start") ? 'dep-ready' : dn.indexOf(uiText('取消', "Cancel")) >= 0 ? 'dep-bad' : 'dep-wait'), dn));
    var w = waiting[r.id] || waiting['test:' + r.id];
    if (r.state !== 'dropped' && w) li.appendChild(el('small', 'wait ' + w.kind, '● ' + (waiting[r.id] ? '' : uiText('测试会话', "Test session")) + WAIT[w.kind]));
    else if (r.state !== 'dropped' && (running[r.id] || running['test:' + r.id])) li.appendChild(el('small', 'run', running[r.id] ? uiText('正在干活', "Working") : uiText('测试会话正在测', "Tests in progress")));
    if (merging[r.id]) li.appendChild(el('small', 'merge', '⇄ ' + mergeText(merging[r.id])));
    li.appendChild(el('small', '', r.state === 'dropped' ? (r.branch_removed ? uiText('已取消 · 分支已清理', "Cancelled · Branch cleaned up") : r.keep_branch ? uiText('已取消 · 分支保留中', "Cancelled · Branch retained") : uiText('已取消 · 只读', "Cancelled · Read-only")) : r.state === 'done' ? uiText('已完成', "Completed") : r.state === 'draft' ? uiText('立项中 · ', "Planning · ") + levelName(r) + (live ? uiText(' · 会话开着', " · Session open") : '') : stageOf(r) === 'check' ? uiText('待验收', "Ready for acceptance") + (r.testing && r.testing.last ? (r.testing.last.verdict === 'ok' ? uiText(' · 测试通过', " · Tests passed") : uiText(' · 跳过了测试', " · Testing skipped")) : r.steps ? uiText(' · 计划 ', " · Plan ") + r.done + ' / ' + r.steps : '') + (live ? uiText(' · 会话开着', " · Session open") : '') : stageOf(r) === 'test' ? uiText('测试中 · 第 ', "Testing · Round ") + ((r.testing && r.testing.round) || 1) + uiText(' 轮', "") + (live ? uiText(' · 会话开着', " · Session open") : '') : r.steps ? uiText('计划 ', "Plan ") + r.done + ' / ' + r.steps + (live ? uiText(' · 会话开着', " · Session open") : '') : live ? uiText('会话开着', "Session open") : r.sessions.length ? uiText('进行中', "In development") : uiText('还没开始', "Ready to start")));
    var keep = keepButton(r); // 已取消的需求：保留它的分支
    if (keep) { li.classList.add('has-keep'); li.appendChild(keep); }
    li.addEventListener('click', function () { select(r.id); });
    onContext(li, r);
    return [li].concat(r.id === current ? designTabs(r) : []);
}

// 有依赖关系的需求放进同一个文件夹（deps-model.js 的 groups）：文件夹出现在它第一个成员原来的位置，成员按先后排。
// 收起、展开只记在这台机器上（localStorage，读写不了就当全部展开）
var folderKey = 'relayforge.reqFolders.closed', foldersClosed = {};
try { foldersClosed = JSON.parse(localStorage.getItem(folderKey) || '{}') || {}; } catch (_) { foldersClosed = {}; }
function toggleFolder(key) {
  if (foldersClosed[key]) delete foldersClosed[key]; else foldersClosed[key] = true;
  try { localStorage.setItem(folderKey, JSON.stringify(foldersClosed)); } catch (_) { /* 记不住就算了 */ }
  list();
}
function folderRow(g, members) {
  var closed = !!foldersClosed[g.key], done = members.filter(function (x) { return x.state === 'done'; }).length;
  var li = el('li', 'req-folder' + (closed ? ' closed' : '') + (closed && members.some(function (x) { return x.id === current; }) ? ' on' : ''));
  li.setAttribute('role', 'button'); li.tabIndex = 0; li.setAttribute('aria-expanded', String(!closed));
  li.title = g.title + uiText(' 等 ', " and ") + members.length + uiText(' 个有依赖关系的需求，前置需求在前。点一下', " linked requirements. Prerequisites come first. Click to ") + (closed ? uiText('展开', "expand") : uiText('收起', "collapse"));
  li.appendChild(el('span', 'chev', closed ? '▸' : '▾'));
  li.appendChild(el('b', '', g.title));
  li.appendChild(el('small', 'muted', members.length + uiText(' 个', "") + (done ? uiText(' · 完成 ', " · Completed ") + done : '')));
  li.addEventListener('click', function () { toggleFolder(g.key); });
  li.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleFolder(g.key); } });
  return li;
}
function listRows() {
  var groupOf = {}, placed = {}, rows = [];
  DepsModel.groups(reqs).forEach(function (g) { g.ids.forEach(function (id) { groupOf[id] = g; }); });
  reqs.filter(visibleReq).forEach(function (r) {
    var g = groupOf[r.id];
    if (!g) { rows = rows.concat(reqRow(r, false)); return; }
    if (placed[g.key]) return;
    placed[g.key] = true;
    var members = g.ids.map(byId).filter(function (x) { return x && visibleReq(x); });
    rows.push(folderRow(g, members));
    if (!foldersClosed[g.key]) members.forEach(function (x) { rows = rows.concat(reqRow(x, true)); });
  });
  return rows;
}

function list() {
  board();
  preview();
  $('req-list').replaceChildren.apply($('req-list'), listRows());
  refreshInboxBadges($('req-list'));
  $('filter-count').textContent = reqs.filter(visibleReq).length + ' / ' + reqs.length;
  if (!reqs.filter(visibleReq).length) $('req-list').appendChild(el('li', 'empty', reqs.length ? uiText('没有符合筛选条件的需求。', "No requirements match your filters.") : uiText('还没有需求。', "No requirements yet.")));
  if (!past.length) { $('past-list').replaceChildren(el('li', 'empty', uiText('这个项目里还没有聊过。', "No chats in this project yet."))); return; }
  $('past-list').replaceChildren.apply($('past-list'), past.map(function (s) {
    var key = 'session:' + s.session_id, li = el('li', 'req' + (key === current ? ' on' : ''));
    li.insertAdjacentHTML('beforeend', InboxModel.badge(key));
    li.appendChild(el('b', '', s.title));
    if (waiting[key]) li.appendChild(el('small', 'wait ' + waiting[key].kind, '● ' + WAIT[waiting[key].kind]));
    else if (running[key]) li.appendChild(el('small', 'run', uiText('正在干活', "Working")));
    li.appendChild(el('small', '', s.updated + (terms[key] && !terms[key].dead ? uiText(' · 会话开着', " · Session open") : '')));
    li.title = s.first;
    li.addEventListener('click', function () { select(key); });
    return li;
  }));
  refreshInboxBadges($('past-list'));
}
// 日常聊天：刚点开、还没说过话的新聊天在磁盘上还没有记录，先留在列表里
function loadPast() {
  return window.shell.listSessions().then(function (found) {
    var have = {}; found.forEach(function (s) { have[s.session_id] = true; });
    past = past.filter(function (s) { return s.blank && !have[s.session_id] && terms['session:' + s.session_id]; }).concat(found);
    list(); panel();
  });
}

// 新开一个日常聊天（在项目目录里）。sid、title、say 都可以省；say 是交给它的第一句话（发布时的「让 AI 来合并」用）
function startChat(sid, title, say) {
  if (tab !== 'chats') view('chats');
  sid = sid || crypto.randomUUID();
  var now = new Date(), pad = function (n) { return (n < 10 ? '0' : '') + n; };
  past.unshift({ session_id: sid, blank: true, title: title || uiText('新聊天', "New chat"), first: '', updated: now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate()) + ' ' + pad(now.getHours()) + ':' + pad(now.getMinutes()) });
  if (say) firstSay['session:' + sid] = say;
  select('session:' + sid);
}

// 绑定事件、开始加载：由 main.js 按顺序调用
function wireReqs() {
  // 需求之间的冲突：主进程算好推过来（app/src/main/conflicts.js），看板和面板跟着重画
  window.shell.onConflicts(function (data) { conflictsData = data; board(); panel(); });
  window.shell.getConflicts().then(function (data) { if (data) { conflictsData = data; board(); panel(); } });
  window.shell.onReqs(function (found) { noteDesigns(found); reqs = found; var r = byId(current); if (r && r.state === 'dropped') { if (terms[current]) { terms[current].term.dispose(); terms[current].box.remove(); delete terms[current]; } select(current); } else { list(); panel(); } loadPast(); });
  window.shell.listReqs().then(function (found) { noteDesigns(found); reqs = found; list(); });
  loadPast();
  $('new-chat').addEventListener('click', function () { startChat(); });
}
