// 「改动」块：一个已完成的需求、或一条已解决的问题，它带来了多少提交和文件、挂了哪些会话、有没有发布。
// 数据是 rf.py trace --json 算出来的；这里只有纯函数（界面和测试都用），不碰页面。普通脚本，也能被 node 用 require 读进来。
var TraceModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var FILES_SHOWN = 12;

  // 返回 null 表示什么都不显示（读不到、或这个对象还没有改动可说）
  function summary(t) {
    if (!t || t.error || !t.id) return null;
    var none = !t.commit_count && !t.file_count;
    var publish = '';
    if (t.merged) publish = t.published ? uiText('已发布', "Published") + (t.remotes && t.remotes.length ? '（' + t.remotes[0] + '）' : '') : uiText('还没发布', "Not published");
    else if (t.state !== 'done') publish = uiText('还没合并', "Not merged");
    var files = (t.files || []).slice(0, FILES_SHOWN).map(function (f) { return { path: f.path, text: '+' + f.added + ' -' + f.deleted }; });
    var more = Math.max(0, (t.file_count || 0) - files.length);
    return {
      head: none ? uiText('没有代码改动', "No code changes") : t.commit_count + uiText(' 个提交、', " commits, ") + t.file_count + uiText(' 个文件', " files"),
      publish: none ? '' : publish,
      files: files, filesMore: more,
      sessions: (t.sessions || []).map(function (s) { return { tag: s.tag, title: s.title || '', path: s.path || '' }; }),
      ref: t.id,
      cmd: 'rf.py trace ' + t.id,
    };
  }

  var esc = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).esc;

  // 一小块 HTML：一行概况，文件列表和会话折叠着
  function html(t) {
    var s = summary(t);
    if (!s) return '';
    var h = uiText('<div class="trace-head"><b>改动</b><span>', "<div class=\"trace-head\"><b>Changes</b><span>") + esc(s.head) + '</span>' + (s.publish ? '<span class="trace-pub">' + esc(s.publish) + '</span>' : '') + '</div>';
    if (s.files.length) {
      h += uiText('<details class="trace-files"><summary>文件（', "<details class=\"trace-files\"><summary>Files (") + (s.files.length + s.filesMore) + '）</summary><ul>'
        + s.files.map(function (f) { return '<li><span>' + esc(f.path) + '</span><small>' + esc(f.text) + '</small></li>'; }).join('')
        + (s.filesMore ? uiText('<li><small>…另有 ', "<li><small>… ") + s.filesMore + uiText(' 个，用 ', " more. Run ") + esc(s.cmd) + uiText(' 看全部</small></li>', " to see all files</small></li>") : '') + '</ul></details>';
    }
    if (s.sessions.length) {
      h += uiText('<div class="trace-sessions">会话：', "<div class=\"trace-sessions\">Sessions: ") + s.sessions.map(function (x) { return '<span title="' + esc(x.path || x.tag) + '">' + esc(x.tag) + (x.title ? ' ' + esc(x.title) : '') + '</span>'; }).join('、') + '</div>';
    }
    return h;
  }

  return { summary: summary, html: html, esc: esc, FILES_SHOWN: FILES_SHOWN };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = TraceModel;
