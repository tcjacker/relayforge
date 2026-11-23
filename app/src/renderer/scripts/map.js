// 大图：主线上的产品全貌。右边面板里的编号可以点，直接跳到大图里的那个对象
var mapRender;
function view(name, id) {
  // 测试环境拒绝离开时，菜单入口也要留在当前页面。
  if (name !== 'tests' && !$('tests').hidden && typeof envLeaveOk === 'function' && EV.tab === 'env' && !envLeaveOk()) return false;
  var map = name === 'map', issues = name === 'issues', tests = name === 'tests';
  document.querySelector('main').hidden = map || issues || tests; $('map').hidden = !map; $('issues').hidden = !issues; $('tests').hidden = !tests;
  ['map', 'reqs', 'issues', 'tests', 'chats'].forEach(function (k) {
    var button = $('view-' + k), active = k === name;
    button.classList.toggle('on', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  ['sync', 'status'].forEach(function (k) { $(k).style.visibility = map || issues || tests ? 'hidden' : ''; });
  if (issues) { showIssues(id); return; } // 问题页：issue-actions.js
  if (tests) { window.shell.viewing(null); showTests(); return; } // 测试页：tests.js
  if (map) window.shell.viewing(mapEdit && mapEdit.open ? MAP_EDIT : null);
  if (name !== 'reqs') resetBoardPages(); // 离开需求页，看板上展开的已完成、已取消回到 10 张（board.js）
  if (!map) {
    tab = name;
    $('req-pane').hidden = name !== 'reqs'; $('chat-pane').hidden = name !== 'chats';
    select(chosen[name]);
    return;
  }
  $('map-note').textContent = uiText('正在生成大图…', "Generating project map…"); $('map-note').hidden = false;
  var render = mapRender = window.shell.renderMap();
  render.then(function (made) {
    if (render !== mapRender || $('map').hidden) return;
    if (made.error) { $('map-note').textContent = uiText('大图没能生成：', "Couldn't generate the project map: ") + made.error; return; }
    $('map-note').hidden = true;
    $('map-frame').src = encodeURI('file://' + made.file) + '?embed=map&language=' + uiText('zh-CN', 'en') + (mapEdit && mapEdit.open ? '&editing=1' : '') + '&t=' + Date.now() + '#' + (id || 'map');
  });
}

// 大图的编辑窗口：这个项目固定的大图编辑会话（一个真正的 Claude Code），点大图上的「编辑」打开，关掉只是藏起来，会话不断
var MAP_EDIT = 'map-edit', mapEdit = null; // { term, fit, open, dead, busy }
function mapEditShow(open) {
  if (!mapEdit) mapEdit = { open: false };
  mapEdit.open = open;
  $('map-edit').classList.toggle('open', open); $('map-edit').setAttribute('aria-hidden', String(!open));
  try { $('map-frame').contentWindow.postMessage({ rf: 'map-edit-state', open: open }, '*'); } catch (e) { /* 大图还没载入 */ }
  window.shell.viewing(open ? MAP_EDIT : null);
  if (!open) return;
  if (!mapEdit.term || mapEdit.dead) mapEditStart();
  else setTimeout(function () { mapEditFit(); mapEdit.term.focus(); }, 220);
}
function mapEditStart() {
  if (mapEdit.term) { mapEdit.term.dispose(); $('map-term').innerHTML = ''; }
  var term = new Terminal({ fontFamily: 'Menlo, "SF Mono", Consolas, "DejaVu Sans Mono", monospace', fontSize: 13, cursorBlink: true, allowProposedApi: true, theme: { background: '#111318' } });
  var fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($('map-term'));
  term.onData(function (data) { window.shell.input(MAP_EDIT, data); });
  mapEdit.term = term; mapEdit.fit = fit; mapEdit.dead = false;
  setTimeout(function () { fit.fit(); window.shell.open({ id: MAP_EDIT, cols: term.cols, rows: term.rows, title: uiText('调整大图', "Edit project map") }); term.focus(); }, 220); // 等滑出来再量大小
}
function mapEditFit() {
  if (!mapEdit || !mapEdit.term || !mapEdit.open) return;
  mapEdit.fit.fit();
  window.shell.resize(MAP_EDIT, { cols: mapEdit.term.cols, rows: mapEdit.term.rows });
}
// 编辑会话回复完了：重新生成大图，停在刚才看的地方
function mapRefresh() {
  if ($('map').hidden) return;
  var at = null;
  try { at = $('map-frame').contentWindow.location.hash.slice(1); } catch (e) { /* 读不到就回到大图开头 */ }
  view('map', at);
}

// 绑定事件、开始加载：由 main.js 按顺序调用
function wireMap() {
  window.addEventListener('message', function (e) {
    if (e.source !== $('map-frame').contentWindow || !e.data) return;
    if (e.data.rf === 'map-trunk') { // 大图里点了「设为主干」：只认这一种字段，编号由后端核对
      window.shell.setTrunk(String(e.data.id || ''), !!e.data.on).then(function (r) { if (r && r.error) say(r.error, true); else mapRefresh(); });
      return;
    }
    if (e.data.rf !== 'map-edit') return;
    mapEditShow(!!e.data.open);
  });
  $('map-edit-close').addEventListener('click', function () { mapEditShow(false); });
  window.addEventListener('resize', mapEditFit);
  window.shell.onData(function (p) { if (p.id === MAP_EDIT && mapEdit && mapEdit.term) mapEdit.term.write(p.data); });
  window.shell.onExit(function (p) {
    if (p.id !== MAP_EDIT || !mapEdit || !mapEdit.term) return;
    mapEdit.dead = true;
    mapEdit.term.write(uiText('\r\n\x1b[2m会话已退出。关掉窗口再点「编辑」可以接着聊。\x1b[0m\r\n', "\r\n\u001b[2mSession exited. Close this panel and select Edit to resume.\u001b[0m\r\n"));
  });
  window.shell.onRunning(function (ids) {
    var busy = ids.indexOf(MAP_EDIT) >= 0;
    if (mapEdit && mapEdit.busy && !busy) mapRefresh(); // 一轮做完了，改动已经写进文件
    if (mapEdit) mapEdit.busy = busy;
  });
  window.shell.onState(function (p) { if (p.id !== MAP_EDIT) return; if (p.state && p.state.id) $('map-edit-tag').textContent = p.state.id; $('map-demo-live').hidden = !p.demo; if (mapEdit && mapEdit.term) mapEdit.term.options.disableStdin = !!p.demo; });
  window.shell.onInitialization(function (status) {
    if (!status.running && !status.failure) mapRefresh();
  });
  $('view-reqs').addEventListener('click', function () { view('reqs'); });
  $('view-chats').addEventListener('click', function () { view('chats'); });
  $('view-map').addEventListener('click', function () { view('map'); });
  $('side').addEventListener('click', function (e) { var id = e.target && e.target.dataset && e.target.dataset.obj; if (id) view('map', id); });
}
