// 问题页「刷新」（AI 复查待处理问题）的 model 层：不碰 DOM，tests/test_app_issue_review.cjs 直接在 Node 里调用。
// 数据是主进程推来的复查状态（rf.py issue review-status）：state 为 idle、running、ready、failed；
// running 时有 items（每条的 status：queued、running、done、skipped）、done、current、phase（investigate 逐条查，group 归并）；
// ready 时有 rows（kind：gone、stale、merge、still、unclear、recheck，带 detail、ran、target，可勾选的带 stamp）；failed 时有 error。
var IssueReviewModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var esc = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).esc;
  var GROUPS = [
    ['gone', uiText('不存在', "Cannot confirm"), 'var(--red)', uiText('确认后结为「已取消 · 不是问题」', "Mark as Cancelled · Not an issue")],
    ['stale', uiText('已过期', "Out of date"), 'var(--green)', uiText('确认后进「已处理」', "Mark as Resolved")],
    ['merge', uiText('可合并', "Can be grouped"), 'var(--blue)', uiText('确认后标为重复，记录并到保留的原单', "Mark as duplicate and add its history to the original issue")],
    ['still', uiText('仍存在', "Still present"), 'var(--amber)', uiText('留在待处理，复查结论记进这条问题', "Keep open and record the findings")],
    ['unclear', uiText('没查清', "Inconclusive"), 'var(--muted)', uiText('留在待处理，写明缺什么', "Keep open and describe the missing evidence")],
    ['recheck', uiText('要重查', "Needs another review"), 'var(--amber)', uiText('查完以后又有了新回复，AI 没看到，结论作废，留在待处理', "New replies arrived after the review. Keep the issue open for another review.")],
  ];
  var PICKABLE = { gone: 1, stale: 1, merge: 1 };
  var numOf = function (id) { return '#' + String(id || '').slice(2); };

  var active = function (s) { return !!s && (s.state === 'running' || s.state === 'ready'); };
  var total = function (s) { return (s.items || []).length; };
  var percent = function (s) { return total(s) ? Math.round((s.done || 0) / total(s) * 100) : 0; };
  var currentItem = function (s) { return (s.items || []).filter(function (x) { return x.id === s.current; })[0] || null; };
  var pickable = function (s) { return (s.rows || []).filter(function (r) { return PICKABLE[r.kind] && r.stamp; }); };

  // 按钮：没在复查时是「刷新」；在查显示进度，点了看进度；有结果显示「复查结果」，点了看结果
  function button(s) {
    if (s && s.state === 'running') return { label: uiText('复查中 ', "Reviewing ") + (s.done || 0) + '/' + total(s), title: uiText('点开看进度', "View progress"), running: true, dot: false };
    if (s && s.state === 'ready') return { label: uiText('复查结果', "Review results"), title: uiText('点开看复查结果', "View review results"), running: false, dot: true };
    return { label: uiText('刷新', "Refresh"), title: uiText('让 AI 复查待处理的问题：是不是真的存在、能不能合并、是不是已经过期', "Ask AI to review open issues for validity, duplicates, and outdated reports"), running: false, dot: false };
  }

  // 列表里每一行：正在查的「复查中…」，查完的「已复查」
  function rowMark(s, id) {
    if (!s || s.state !== 'running') return '';
    var item = (s.items || []).filter(function (x) { return x.id === id; })[0];
    if (!item) return '';
    if (item.status === 'running') return uiText('<span class="rv now">复查中…</span>', "<span class=\"rv now\">Reviewing…</span>");
    if (item.status === 'done') return uiText('<span class="rv">已复查</span>', "<span class=\"rv\">Reviewed</span>");
    return '';
  }

  // 窗口关掉以后问题页顶上的那一条：在查（进度条、正在查哪一条）、查完了、失败了
  function strip(s) {
    if (!s) return null;
    if (s.state === 'running') {
      var cur = currentItem(s);
      var now = s.phase === 'group' ? uiText('逐条查完了，正在把同一个故障的归到一起', "Individual reviews finished. Grouping duplicates.") : cur ? uiText('正在查 ', "Reviewing ") + cur.num + ' ' + cur.title : uiText('正在准备代码副本', "Preparing a copy of the code");
      return { cls: '', html: uiText('<div class="top"><b>AI 复查中 ', "<div class=\"top\"><b>AI review in progress ") + (s.done || 0) + '/' + total(s) + '</b><span class="now">' + esc(now)
        + uiText('</span><button type="button" class="quiet" data-review="open">看进度</button></div>', "</span><button type=\"button\" class=\"quiet\" data-review=\"open\">View progress</button></div>")
        + '<div class="rbar"><i style="width:' + percent(s) + '%"></i></div>' };
    }
    if (s.state === 'ready') {
      var can = pickable(s).length;
      return { cls: 'ready', html: uiText('<div class="top"><b>AI 复查完成</b><span class="now">', "<div class=\"top\"><b>AI review complete</b><span class=\"now\">") + esc((s.rows || []).length + uiText(' 条里 ', " reviewed · ") + can + uiText(' 条可以处理', " ready for action")
        + (can ? uiText('，等你确认', " · Awaiting confirmation") : '')) + uiText('</span><button type="button" class="quiet" data-review="open">查看结果</button></div>', "</span><button type=\"button\" class=\"quiet\" data-review=\"open\">View results</button></div>") };
    }
    if (s.state === 'failed') {
      return { cls: 'failed', html: uiText('<div class="top"><b>AI 复查失败</b><span class="now wrap">', "<div class=\"top\"><b>AI review failed</b><span class=\"now wrap\">") + esc(s.error || uiText('没有给出原因', "No reason provided"))
        + uiText('</span><button type="button" class="quiet" data-review="dismiss">知道了</button></div>', "</span><button type=\"button\" class=\"quiet\" data-review=\"dismiss\">Dismiss</button></div>") };
    }
    return null;
  }

  // 复查窗口里的进度：每一条在做什么
  function progressHtml(s) {
    return '<div class="bar"><i style="width:' + percent(s) + '%"></i></div>'
      + uiText('<p class="muted">AI 正在后台复查 ', "<p class=\"muted\">AI is reviewing ") + total(s) + uiText(' 条待处理问题，已查完 ', " open issues in the background. Completed reviews: ") + (s.done || 0) + uiText(' 条', "")
      + (s.phase === 'group' ? uiText('，正在把同一个故障的归到一起', " · Grouping duplicates") : '') + uiText('。可以关掉这个窗口，进度会一直显示在问题页顶上。</p>', ". You can close this window and follow progress at the top of the Issues page.</p>")
      + '<ol class="steps">' + (s.items || []).map(function (x) {
        var map = { done: ['done', '✓', x.ran || uiText('已查完', "Reviewed")], running: ['now', '…', uiText('正在复现、重跑相关的测试', "Reproducing the issue and rerunning related tests")],
          skipped: ['', '–', uiText('复查期间已经不在待处理，没有查', "Skipped because the issue is no longer open")], queued: ['', '·', uiText('排队中', "Queued")] }[x.status] || ['', '·', uiText('排队中', "Queued")];
        return '<li class="' + map[0] + '"><b>' + map[1] + '</b><span>' + esc(x.num + ' ' + x.title) + '<span class="ran">' + esc(map[2]) + '</span></span></li>';
      }).join('') + '</ol>';
  }

  // 结果：分组；只有不存在、已过期、可合并能勾选（默认勾上）
  function resultsHtml(s) {
    var rows = s.rows || [], can = pickable(s).length;
    if (!rows.length) return uiText('<p>这次复查的问题都已经不在待处理了，没有要处理的。</p>', "<p>All issues in this review have already left the open list. There are no actions to apply.</p>");
    return uiText('<p>已复查 ', "<p>Reviewed ") + rows.length + uiText(' 条：', " issues: ") + can + uiText(' 条可以处理，', " ready for action, ") + (rows.length - can) + uiText(' 条留在待处理。每条的结论也记进了它自己的对话。</p>', " staying open. The findings are in each issue's conversation.</p>")
      + GROUPS.map(function (g) {
        var group = rows.filter(function (r) { return r.kind === g[0]; });
        if (!group.length) return '';
        return '<h3 class="grp" data-kind="' + g[0] + '"><span style="color:' + g[2] + '">' + g[1] + ' · ' + group.length + uiText(' 条</span><em>', "</span><em>") + g[3] + '</em></h3>'
          + group.map(function (r) {
            var head = '<strong>' + esc(r.num + ' ' + r.title) + '</strong>'
              + (r.target ? uiText(' <span class="muted">→ 并入 <button type="button" class="link" data-review="goto" data-id="', " <span class=\"muted\">→ Merge into <button type=\"button\" class=\"link\" data-review=\"goto\" data-id=\"") + esc(r.target) + '">'
                + esc(numOf(r.target)) + '</button></span>' : '');
            var text = '<span>' + head + '<br><span class="muted">' + esc(r.detail) + '</span>'
              + uiText('<span class="ran">AI 做了：', "<span class=\"ran\">AI checks: ") + esc(r.ran) + '</span>'
              + (r.steps ? uiText('<span class="ran">复现步骤：', "<span class=\"ran\">Steps to reproduce: ") + esc(r.steps) + uiText('；实际结果：', ". Actual result: ") + esc(r.observed || '') + '</span>' : '') + '</span>';
            return PICKABLE[r.kind] && r.stamp
              ? '<label><input type="checkbox" value="' + esc(r.id) + '" checked>' + text + '</label>'
              : '<div class="keep">' + text + '</div>';
          }).join('');
      }).join('');
  }

  // 确认以后的一句话：处理了几条，哪几条在复查之后变过、没处理
  function appliedNote(done) {
    var text = uiText('本次已处理 ', "Applied results to ") + (done.applied || []).length + uiText(' 条，原记录可在对应状态查看。', " issues. Find each issue under its new status.");
    return text + (done.skipped || []).map(function (x) { return x.reason; }).join('');
  }

  return { GROUPS: GROUPS, active: active, button: button, rowMark: rowMark, strip: strip, progressHtml: progressHtml,
    resultsHtml: resultsHtml, pickable: pickable, appliedNote: appliedNote };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = IssueReviewModel;
