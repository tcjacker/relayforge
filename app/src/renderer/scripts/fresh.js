// 功能点核对的建议对话框：大图上点「查看」打开（map.js），逐条采纳或忽略，都处理完点「核对完」。数据和动作在主进程 fresh.js
var FR = { id: '', data: null, error: '' };

function freshRender() {
  $('fresh-dlg').innerHTML = FreshModel.html(FR.id, FR.data, FR.error);
}

// reload：核对会话或别处改了建议，对话框开着就重读内容；已经关了（比如刚点了「核对完」）不再弹出来
function freshOpen(id, reload) {
  FR.id = id; FR.error = '';
  if (!reload) FR.data = null;
  window.shell.freshShow(id).then(function (data) {
    if (reload && !$('fresh-dlg').open) return;
    FR.data = data && !data.error ? data : null; FR.error = data && data.error ? data.error : '';
    freshRender();
    if (!$('fresh-dlg').open) $('fresh-dlg').showModal();
  });
}

// 大图：把哪些功能点可能过时、核对到哪了交给大图页面显示
function mapFresh() {
  window.shell.freshOverview().then(function (data) {
    try { $('map-frame').contentWindow.postMessage({ rf: 'map-fresh', data: data || {} }, '*'); } catch (e) { /* 大图还没载入 */ }
  });
}

function wireFresh() {
  $('map-frame').addEventListener('load', mapFresh);
  window.shell.onFreshChanged(function (p) {
    mapFresh();
    if ($('fresh-dlg').open && p && p.id === FR.id) freshOpen(FR.id, true);
  });
  window.addEventListener('message', function (e) {
    if (e.source !== $('map-frame').contentWindow || !e.data) return;
    var id = String(e.data.id || '');
    if (e.data.rf === 'map-fresh-check') window.shell.freshCheck(id).then(function (r) { if (r && r.error) say(r.error, true); else say(uiText('已开始核对 ', 'Checking ') + id + uiText('，在后台跑，交回以后大图上会提示。', '. It runs in the background; the map will show when it is done.')); mapFresh(); });
    if (e.data.rf === 'map-fresh-open') freshOpen(id);
  });
  $('fresh-dlg').addEventListener('click', function (e) {
    var b = e.target.closest('[data-fr]');
    if (!b) return;
    var act = b.getAttribute('data-fr');
    if (act === 'close') { $('fresh-dlg').close(); return; }
    b.disabled = true;
    var call = act === 'adopt' ? window.shell.freshAdopt(FR.id, [Number(b.getAttribute('data-k'))])
      : act === 'ignore' ? window.shell.freshIgnore(FR.id, [Number(b.getAttribute('data-k'))]) : window.shell.freshDone(FR.id);
    call.then(function (r) {
      if (r && r.error) { FR.error = r.error; freshRender(); return; }
      if (act === 'done') { $('fresh-dlg').close(); say(FR.id + uiText(' 核对完了。', ' check finished.')); mapFresh(); return; }
      FR.error = ''; FR.data = r; freshRender();
    });
  });
}
