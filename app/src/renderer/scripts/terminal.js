// 终端：每个需求一个，切换时只是显示和隐藏，会话不会断
function terminal(id, say) {
  var t = terms[id];
  if (t && !t.dead) return t;
  if (t) { t.term.dispose(); t.box.remove(); }
  var box = el('div', 'term');
  $('terms').appendChild(box);
  var term = new Terminal({ fontFamily: 'Menlo, "SF Mono", Consolas, "DejaVu Sans Mono", monospace', fontSize: 13, cursorBlink: true, allowProposedApi: true, theme: { background: '#111318' } });
  var fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(box);
  term.onData(function (data) { window.shell.input(id, data); });
  t = terms[id] = { term: term, fit: fit, box: box, dead: false };
  show(id);
  fit.fit();
  window.shell.open({ id: id, cols: term.cols, rows: term.rows, blank: !!(pastOf(id) && pastOf(id).blank), title: (byId(id) || pastOf(id) || {}).title, images: firstImages[id], say: say || firstSay[id] });
  delete firstImages[id]; delete firstSay[id];
  return t;
}
var WELCOME = {
  reqs: uiText('在左边新建一个需求，或者点一个已有的需求接着聊。\n每个需求有自己的分支和会话，互不影响。', "Create a requirement on the left, or select one to continue the conversation.\nEach requirement has its own branch and sessions."),
  chats: uiText('点左边的「＋ 新聊天」，或者点一个以前的聊天接着聊。\n日常聊天直接在项目目录里进行，不开分支，也没有验收。', "Start a new chat on the left or continue an earlier conversation.\nChats run in the project directory without a branch or acceptance."),
};
function show(id) {
  showInbox('session-inbox', id);
  if (typeof showDemoSession === 'function') showDemoSession(!!(states[id] && states[id].demo));
  Object.keys(terms).forEach(function (k) { terms[k].box.hidden = k !== id; });
  $('welcome').hidden = !!id;
  $('welcome').textContent = WELCOME[tab] || '';
}
function refit() {
  var key = typeof termOf === 'function' ? termOf(current) : current; // 选的是「测试」标签时量的是测试会话的终端
  var t = terms[key];
  if (!t || t.dead || $('terms').hidden) return; // 正在看设计稿：终端藏着，量不出大小
  t.fit.fit();
  window.shell.resize(key, { cols: t.term.cols, rows: t.term.rows });
}

// 绑定事件、开始加载：由 main.js 按顺序调用
function wireTerminal() {
  window.addEventListener('resize', refit);
  window.shell.onData(function (p) { if (terms[p.id]) terms[p.id].term.write(p.data); });
  window.shell.onExit(function (p) {
    var t = terms[p.id];
    if (!t) return;
    if (!pastOf(p.id) && !hasChat(byId(String(p.id).replace(/^test:/, '')))) return;
    t.dead = true;
    var message = p.headless && p.kind !== 'exit'
      ? uiText('后台的这一轮做完了。', "The background turn finished.") : uiText('会话已退出。', "The session exited.");
    t.term.write('\r\n\x1b[2m' + message + uiText('再点一下左边这一项可以在终端里接着聊。\x1b[0m\r\n', " Select it again on the left to resume in the terminal.\u001b[0m\r\n"));
    list();
  });
}
