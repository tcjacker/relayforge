// 「改动」块：已完成的需求（右边面板）和已解决的问题（对话底部）下面，安静地列出它带来的提交、文件和会话。
// 数据由主进程用 rf.py trace 算（只读）；这里只管取、缓存、画。没取到或取失败就什么都不显示。
var TR = { cache: {}, asking: {}, ttl: 30000 };

function traceOf(ref) {
  var hit = TR.cache[ref], now = Date.now();
  if (!hit || now - hit.at > TR.ttl) {
    if (!TR.asking[ref]) {
      TR.asking[ref] = true;
      window.shell.traceGet(ref).then(function (t) {
        TR.asking[ref] = false;
        var changed = JSON.stringify(t) !== JSON.stringify(hit && hit.t);
        TR.cache[ref] = { t: t, at: Date.now() };
        if (changed) { panelTrace(); if (typeof renderIssueDetail === 'function') { IS.lastLen = ''; renderIssueDetail(); } }
      }, function () { TR.asking[ref] = false; });
    }
  }
  return hit ? hit.t : null;
}

// 需求面板：已完成的需求才有
function panelTrace() {
  var box = $('req-trace'), r = byId(current);
  var t = r && r.state === 'done' ? traceOf(r.id) : null;
  var h = t ? TraceModel.html(t) : '';
  box.hidden = !h; box.innerHTML = h;
}

// 问题页：已解决、改过代码的问题（修复挂着的那次）；返回一小块 HTML
function issueTraceHtml(i) {
  if (!i || i.state !== 'resolved' || !i.fix || (i.fix.finished || []).includes(i.id)) return '';
  var t = traceOf(i.id);
  var h = t ? TraceModel.html(t) : '';
  return h ? '<div class="trace issue-trace">' + h + '</div>' : '';
}

function wireTrace() {
  window.shell.onPublish(function () { TR.cache = {}; panelTrace(); }); // 发布状态变了：「有没有发布」要重算
}
