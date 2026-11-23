// 初始化状态放在文档流中，不盖住其他 UI；关闭后仍可从项目菜单重新打开。
function showInitialization(status) {
  var failure = status && status.failure, running = status && status.running;
  $('initialization').hidden = !failure && !running;
  $('init-title').textContent = running ? uiText('正在初始化项目', "Setting up project") : uiText('初始化失败', "Project setup failed");
  $('init-summary').textContent = running ? uiText('正在读代码并生成大图草稿…', "Reading the code and drafting the project map…") : failure ? failure.stage + uiText('未完成，可以让 AI 检查并重试。', "Setup is incomplete. Ask AI to inspect it and retry.") : '';
  $('init-error').textContent = failure ? failure.error : '';
  $('init-details').hidden = !failure;
  $('init-retry').hidden = !failure;
  $('init-check').hidden = !failure;
  $('init-retry').disabled = !!running;
  $('init-check').disabled = !!running;
  requestAnimationFrame(function () { if (typeof refit === 'function') refit(); });
}

// 提醒：哪些会话停下来等你了
// 绑定事件、开始加载：由 main.js 按顺序调用
function wireAttention() {
  window.shell.onWaiting(function (map) {
    waiting = map;
    var keys = Object.keys(map).filter(function (k) { return k !== 'map-edit' && k.indexOf('fix:') !== 0 && k.indexOf('probe:') !== 0; }), chats = keys.filter(function (k) { return k.indexOf('session:') === 0; }).length; // 修复会话和排查会话在问题页里显示
    $('view-reqs').dataset.badge = keys.length - chats || ''; $('view-chats').dataset.badge = chats || '';
    list();
  });
  window.shell.onRunning(function (ids) {
    running = {}; ids.forEach(function (id) { running[id] = true; });
    list();
  });
  var noticeTimer;
  window.shell.onNotice(function (text) {
    clearTimeout(noticeTimer);
    $('notice').replaceChildren(el('span', '', text), el('button', 'notice-close', '×'));
    $('notice').querySelector('button').setAttribute('aria-label', uiText('关闭提示', "Dismiss notice"));
    $('notice').hidden = !text;
    noticeTimer = setTimeout(function () { $('notice').hidden = true; }, 8000);
  });
  window.shell.onInitialization(showInitialization);
  $('init-close').addEventListener('click', function () { $('initialization').hidden = true; refit(); });
  $('init-retry').addEventListener('click', async function () {
    $('init-retry').disabled = true;
    try {
      var made = await window.shell.repairInitialization();
      if (made.error) throw new Error(made.error);
      var exists = past.filter(function (item) { return item.session_id === made.session_id; })[0];
      if (!exists) past.unshift(made);
      view('chats'); select('session:' + made.session_id);
      $('init-summary').textContent = uiText('已打开 AI 修复会话。修复完成后可点击「检查结果」。', "Opened an AI repair session. When it finishes, select Check setup.");
    } catch (error) { $('init-summary').textContent = error.message; }
    finally { $('init-retry').disabled = false; }
  });
  $('init-check').addEventListener('click', async function () {
    $('init-check').disabled = true;
    try {
      var result = await window.shell.checkInitialization();
      if (result.error) $('init-summary').textContent = result.error;
      else $('initialization').hidden = true;
    } catch (error) { $('init-summary').textContent = error.message; }
    finally { $('init-check').disabled = false; refit(); }
  });
  $('notice').addEventListener('click', function () { $('notice').hidden = true; });
  window.shell.onGoto(function (id) {
    if (id === 'map-edit') { view('map'); mapEditShow(true); return; }
    if (id.indexOf('issue:') === 0) { view('issues', id.slice(6)); return; }
    if (id.indexOf('fix:') === 0) { // 修复会话在等你：打开问题页，停在这个修复的问题上，右边切到修复会话
      var hit = IS.issues.filter(function (i) { return fixRun(i) === id; })[0];
      if (hit) IS.pane[hit.id] = 'term';
      view('issues', hit && hit.id); return;
    }
    if (id.indexOf('probe:') === 0) { // 排查会话在等你：打开问题页，停在这条问题上，右边切到排查会话
      var asked = IS.issues.filter(function (i) { return probeRun(i) === id; })[0];
      if (asked && probeOpen(asked)) IS.pane[asked.id] = 'term';
      view('issues', asked && asked.id); return;
    }
    view(id.indexOf('session:') === 0 ? 'chats' : 'reqs'); select(id);
  });
}
