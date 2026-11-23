// 后台运行的会话需要你（human-in-the-loop）：右下角的卡片。批准或拒绝它要做的一步、回复它的问题，
// 或者「在终端里接管」——停掉后台运行，在需求页或问题页用交互终端接着这个会话。数据和动作在主进程 hitl.js。
var HITL = { data: { approvals: [], questions: [] }, folded: false };

function hitlRender() {
  var box = $('hitl');
  var html = HitlModel.html(HITL.data, HITL.folded);
  // 正在写回复时不重画，免得打的字丢了
  if (box.contains(document.activeElement) && document.activeElement.tagName === 'TEXTAREA') return;
  box.innerHTML = html;
  box.hidden = !html;
}

// 接管：把这个会话原来的终端作废，再按交互方式打开（同一个会话编号，接着原来的聊天记录）
function hitlOpenTerminal(id) {
  if (id.indexOf('fix:') === 0 || id.indexOf('probe:') === 0) {
    if (fixTerms[id]) fixTerms[id].dead = true;
    var hit = IS.issues.filter(function (i) { return fixRun(i) === id || probeRun(i) === id; })[0];
    if (hit) IS.pane[hit.id] = 'term';
    view('issues', hit && hit.id);
    return;
  }
  if (terms[id]) terms[id].dead = true;
  if (id.indexOf('session:') === 0) { view('chats'); select(id); return; }
  view('reqs'); select(id.replace(/^test:/, ''));
  terminal(id);
  show(id);
}

function wireHitl() {
  var box = $('hitl');
  window.shell.onHitl(function (data) { HITL.data = data || { approvals: [], questions: [] }; hitlRender(); });
  window.shell.onHitlTakeover(function (p) { hitlOpenTerminal(p.id); });
  box.addEventListener('click', function (event) {
    var b = event.target.closest('[data-hitl]');
    if (!b) return;
    var act = b.getAttribute('data-hitl'), card = b.closest('.hitl-card'), id = card && card.getAttribute('data-id');
    if (act === 'fold') { HITL.folded = !HITL.folded; hitlRender(); return; }
    if (act === 'take') { window.shell.hitlTakeover(b.getAttribute('data-run')); return; }
    var done = function (r) { if (r && r.error) say(r.error, true); };
    if (act === 'allow' || act === 'deny') { b.disabled = true; window.shell.hitlDecide(id, act === 'allow').then(done); return; }
    if (act === 'reply') {
      var text = card.querySelector('textarea').value.trim();
      if (!text) return;
      b.disabled = true;
      card.querySelector('textarea').blur();
      window.shell.hitlReply(id, text).then(function (result) {
        if (result && result.error) {
          b.disabled = false;
          done(result);
        } else hitlRender();
      });
    }
  });
  window.shell.hitlList().then(function (data) { if (data) { HITL.data = data; hitlRender(); } });
}
