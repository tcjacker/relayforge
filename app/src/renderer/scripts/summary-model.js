// 「总结」块：已完成的需求右边面板里，「改动」上面一小块：几句话的总结、概括标签、程序算的标签、会话记录存到本机了没有、
// 有没有聊到了但没记下的决定在等你确认。已取消的需求只显示会话记录那一行。
// 数据来自 rf.py req list --json（需求文件头和「总结」一节）和主进程 summary.js 推来的后台状态；这里只有纯函数（界面和测试都用）。
var SummaryModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var TAGS_SHOWN = 14;

  var esc = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).esc;

  // job：{ running: [编号], queued: [编号], failed: { 编号: 原因 } }
  function summary(r, job) {
    if (!r || (r.state !== 'done' && r.state !== 'dropped')) return null;
    job = job || {};
    var done = r.state === 'done';
    var running = done && (job.running || []).indexOf(r.id) >= 0;
    var queued = done && !running && (job.queued || []).indexOf(r.id) >= 0;
    var failed = done && !running && !queued && job.failed && job.failed[r.id] ? job.failed[r.id] : '';
    var lines = done ? (r.summary || []) : [];
    var status = !done ? '' : running ? uiText('正在总结…', "Summarizing…") : queued ? uiText('排队等总结', "Summary queued") : !r.summarized ? uiText('还没总结', "No summary yet") : '';
    var here = r.archive_here || [], missing = r.archive_missing || [], sessions = r.session_tags || [];
    var records = [];
    if (here.length) records.push(uiText('会话记录已存到本机（', "Session records saved locally (") + here.length + uiText(' 个）', ")"));
    var elsewhere = (r.archived || []).filter(function (t) { return here.indexOf(t) < 0; });
    if (elsewhere.length) records.push(elsewhere.length + uiText(' 个存在别的电脑上', " saved on another computer"));
    if (missing.length) records.push(uiText('原始记录已不在：', "Original record missing: ") + missing.join('、'));
    if (!records.length && sessions.length) records.push(uiText('会话记录还没存到本机', "Session records haven't been saved locally"));
    return {
      done: done, status: status, failed: failed, canRedo: done && !running && !queued,
      lines: lines, topics: done ? (r.topics || []) : [],
      tags: (r.tags || []).slice(0, TAGS_SHOWN), tagsMore: Math.max(0, (r.tags || []).length - TAGS_SHOWN),
      records: records.join('；'),
      pending: done ? (r.pending_decisions || []) : [],
      id: r.id,
    };
  }

  function html(r, job) {
    var s = summary(r, job);
    if (!s) return '';
    var h = '';
    if (s.done) {
      h += uiText('<div class="trace-head"><b>总结</b>', "<div class=\"trace-head\"><b>Summary</b>")
        + (s.topics.length ? '<span class="sum-topics">' + s.topics.map(function (t) { return '<span>' + esc(t) + '</span>'; }).join('') + '</span>' : '')
        + (s.status ? '<span>' + esc(s.status) + '</span>' : '')
        + (s.canRedo ? '<button type="button" class="sum-redo" data-summarize="' + esc(s.id) + uiText('" title="在后台再让 AI 读一遍这个需求和它的会话，替换现在的总结和概括标签">重新总结</button>', "\" title=\"Ask AI to reread this requirement and its sessions in the background, and replace the existing summary and tags\">Regenerate summary</button>") : '')
        + '</div>';
      if (s.failed) h += uiText('<p class="sum-failed">上次没总结出来：', "<p class=\"sum-failed\">The last summary attempt failed: ") + esc(s.failed) + '</p>';
      if (s.lines.length) h += '<ul class="sum-lines">' + s.lines.map(function (l) { return '<li>' + esc(l) + '</li>'; }).join('') + '</ul>';
      if (s.tags.length) h += uiText('<div class="trace-sessions">涉及：', "<div class=\"trace-sessions\">Related: ") + s.tags.map(esc).join('、') + (s.tagsMore ? uiText(' 等 ', " and ") + (s.tags.length + s.tagsMore) + uiText(' 个', " more") : '') + '</div>';
      if (s.pending.length) {
        h += uiText('<div class="sum-pending">有 ', "<div class=\"sum-pending\">") + s.pending.length + uiText(' 条聊到了但没记下的决定，等你确认（', " discussed decisions are awaiting confirmation (") + s.pending.map(esc).join('、') + '）。'
          + uiText('在会话里说「确认一下待确认的」，或运行 rf.py pending 看。</div>', "). Ask the session to review pending decisions or run rf.py pending.</div>");
      }
    }
    if (s.records) h += '<div class="trace-sessions">' + esc(s.records) + '</div>';
    return h;
  }

  return { summary: summary, html: html, esc: esc };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = SummaryModel;
