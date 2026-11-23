// 问题页右边：一条问题就是一条对话。头部（编号、状态、标题、相关功能点、对话和修复会话的切换），
// 对话（提报、AI 初判、补充、你的决定、修复报告），底部的回复框。
function ithumbs(paths, removable) {
  if (!paths || !paths.length) return '';
  return '<div class="thumbs">' + paths.map(function (p, k) {
    return '<span style="position:relative"><button class="th" data-act="zoom" data-src="' + iesc(p) + uiText('" aria-label="放大截图"><img alt="截图" src="', "\" aria-label=\"Enlarge screenshot\"><img alt=\"Screenshot\" src=\"") + iesc(ifile(p)) + '"></button>'
      + (removable ? '<button class="x" data-act="rmshot" data-k="' + k + uiText('" aria-label="去掉这张截图">×</button>', "\" aria-label=\"Remove screenshot\">×</button>") : '') + '</span>';
  }).join('') + '</div>';
}
var ib = function (act, text, primary, extra) { return '<button data-act="' + act + '" class="' + (primary ? 'primary' : 'quiet') + '"' + (extra || '') + '>' + text + '</button>'; };

// 最后一张判断卡片带着按钮：按现在的类别（你纠正过就按你改的）给出下一步，由你点
// 「交给 AI 排查」：待处理的每张判断卡片上都有一个安静的按钮（待补充和没判出来时是自然的下一步，别的类别可有可无）；已经有排查会话就是打开它
function probeButton(i) { return ib('probe', i.probe ? uiText('打开排查会话', "Open investigation session") : uiText('交给 AI 排查', "Ask AI to investigate"), 0, ' title="' + (i.probe ? uiText('接着上次的排查会话', "Resume the last investigation") : uiText('一个只读的会话：读代码、复现，不改任何文件', "Opens a read-only session to inspect code and reproduce the issue")) + '"'); }
function judgeActions(i) {
  if (i.state !== 'todo' || iBusy(i) || IS.busy[i.id]) return '';
  var p = probeButton(i);
  if (i.deferred) return ib('undefer', uiText('拿回来处理', "Restore to Open")) + uiText('<span class="ihint">已标记「以后再说」，随时可以拿回来</span>', "<span class=\"ihint\">Deferred. Restore it at any time.</span>") + p + ib('drop', uiText('不处理', "Close issue"));
  if (i.kind === 'bug') return ib('fix', uiText('开始修复', "Start fix"), 1) + ib('toreq-kind', uiText('其实是新需求', "This is a requirement")) + p + ib('defer', uiText('以后再说', "Defer")) + ib('drop', uiText('不处理', "Close issue"));
  if (i.kind === 'request') return ib('toreq', uiText('转为需求', "Convert to requirement"), 1) + ib('fixasbug', uiText('按问题直接修', "Fix as an issue")) + p + ib('defer', uiText('以后再说', "Defer")) + ib('drop', uiText('不处理', "Close issue"));
  if (i.kind === 'usage') return ib('solved', uiText('解决了', "Resolved"), 1) + ib('stillno', uiText('还是不行', "Still unresolved")) + p + ib('drop', uiText('不处理', "Close issue"));
  return p + ib('drop', uiText('不处理', "Close issue")) + '<span class="ihint">' + (probeLive(i) ? uiText('或在下面回答，话会交给排查会话', "Reply below to send your answer to the investigation session") : uiText('或在下面回答，AI 会带着你的回答再判断一次', "Reply below so AI can reassess with your answer")) + '</span>';
}
function judgeCard(i, e, last) {
  if (!last) return uiText('<div class="collapsed">之前判断：', "<div class=\"collapsed\">Previous assessment: ") + iesc(IKINDS[e.kind] || e.kind) + '</div>';
  var acts = judgeActions(i);
  return uiText('<div class="entry jcard"><div class="lbl"><span class="aitag">AI</span><span>AI 初判</span><span class="at">', "<div class=\"entry jcard\"><div class=\"lbl\"><span class=\"aitag\">AI</span><span>AI triage</span><span class=\"at\">") + iesc(e.at) + '</span></div>'
    + '<h3><span class="kdot" style="background:' + IKCLR[e.kind] + '"></span>' + IKINDS[e.kind] + (e.kind === 'bug' ? uiText('<small>只看了产品事实，还没复现；修复时先复现</small>', "<small>Based on project facts. Reproduction is still pending.</small>") : '') + '</h3>'
    + '<p>' + iesc(e.reason) + '</p>'
    + (e.kind === 'ask' && e.question ? uiText('<div class="q"><b>我想问：</b>', "<div class=\"q\"><b>Question:</b> ") + iesc(e.question) + '</div>' : '')
    + (e.kind === 'usage' && e.howto ? uiText('<div class="how"><b>怎么做：</b>', "<div class=\"how\"><b>Steps:</b> ") + iesc(e.howto) + '</div>' : '')
    + (acts ? '<div class="actions">' + acts + '</div>' : '') + '</div>';
}
function failCard(i, e, last) {
  if (!(last && i.triage_failed)) return uiText('<div class="collapsed">有一次没判出来</div>', "<div class=\"collapsed\">An assessment failed</div>");
  return uiText('<div class="entry jcard failed"><div class="lbl"><span class="aitag">AI</span><span>AI 初判</span><span class="at">', "<div class=\"entry jcard failed\"><div class=\"lbl\"><span class=\"aitag\">AI</span><span>AI triage</span><span class=\"at\">") + iesc(e.at) + '</span></div>'
    + uiText('<h3>没判出来</h3><p class="muted small">', "<h3>Assessment failed</h3><p class=\"muted small\">") + iesc(e.error || uiText('回答的格式不对', "Unexpected response format")) + '</p>'
    + '<div class="actions">' + ib('retry', uiText('重试', "Retry"), 1) + probeButton(i) + ib('drop', uiText('不处理', "Close issue")) + '</div></div>';
}
// 排查会话记下的结论：读过代码，和只看产品事实的初判分开；实际复现的和只是推断的分开写
function findingCard(i, e, last) {
  if (!last) return uiText('<div class="collapsed">之前排查的结论：', "<div class=\"collapsed\">Previous investigation: ") + iesc(IKINDS[e.kind] || e.kind) + '</div>';
  var acts = judgeActions(i), part = function (name, text, cls) { return text ? '<div class="part ' + (cls || '') + '"><b>' + name + '</b><p>' + iesc(text) + '</p></div>' : ''; };
  return uiText('<div class="entry jcard findcard"><div class="lbl"><span class="aitag">AI</span><span>AI 排查结论 · 读过代码</span><span class="at">', "<div class=\"entry jcard findcard\"><div class=\"lbl\"><span class=\"aitag\">AI</span><span>AI investigation · Code inspected</span><span class=\"at\">") + iesc(e.at) + '</span></div>'
    + '<h3><span class="kdot" style="background:' + IKCLR[e.kind] + '"></span>' + IKINDS[e.kind] + '</h3>'
    + '<p>' + iesc(e.text) + '</p>'
    + part(uiText('怎么复现', "Reproduction steps"), e.steps) + part(uiText('问题在', "Where the problem is"), e.where) + part(uiText('实际复现、验证过的', "Reproduced or verified"), e.verified, 'ok') + part(uiText('只是推断、没验证的', "Inferred, still unverified"), e.inferred, 'guess')
    + (e.kind === 'ask' ? uiText('<div class="q"><b>还缺：</b>在下面回答，或回到排查会话里说。</div>', "<div class=\"q\"><b>Still needed:</b> Reply below or in the investigation session.</div>") : '')
    + (acts ? '<div class="actions">' + acts + '</div>' : '') + '</div>';
}
function fixCard(i, e, last) {
  var open = last && i.state === 'review', m = merging[fixRun(i)];
  var acts = '';
  if (open && m) {
    // 和需求一样：一律先在修复会话里把目标分支合并进来，合好了才合并回去；卡住了由你处理后再试
    var p = iphrase(i);
    acts = '<span class="ph ' + p[1] + '">' + iesc(p[0]) + '</span>'
      + (m.phase === 'syncing' ? '<span class="ihint">' + (m.files && m.files.length ? uiText('有冲突（', "Conflicts (") + iesc(m.files.join('、')) + '）。' : '') + uiText('AI 在修复会话里把 ', "The fix session is merging ") + iesc(m.base) + uiText(' 合并进来、跑测试', " and running tests") + (m.busy ? uiText('，还在干活', " · Working") : '') + uiText('；说完程序检查好就自动合并回去</span>', ". RelayForge will check and merge after this turn.</span>") : '')
      + (m.note ? '<span class="ihint">' + iesc(m.note) + '</span>' : '')
      + (m.phase === 'syncing' || m.phase === 'stuck' ? ib('mergeretry', uiText('重新检查并合并', "Check and merge")) : '') + ib('mergeabort', uiText('中止合并', "Abort merge"));
  }
  else if (open && !IS.busy[i.id] && i.confirmed_waiting) {
    // 一起修：这一条你已经确认了，同批还有没确认的，所以还没合并；想改主意可以撤回，也可以说还有问题
    acts = uiText('<span class="ph green">已确认，等同批的 ', "<span class=\"ph green\">Confirmed · Waiting for ") + iNums(i.waiting_for) + '</span>' + ib('unconfirm', uiText('撤回确认', "Withdraw confirmation")) + ib('stillbad', uiText('还有问题', "Still unresolved"))
      + uiText('<span class="ihint">同批都确认了才一起合并</span>', "<span class=\"ihint\">The batch merges once every fix is confirmed.</span>");
  }
  else if (open && !IS.busy[i.id]) acts = ib('confirmfix', i.confirmed ? uiText('再试一次合并', "Retry merge") : uiText('确认修好了', "Confirm fix"), 1) + ib('stillbad', uiText('还有问题', "Still unresolved")) + (i.confirmed ? uiText('<span class="ihint">你已经确认过，上次没能合并进去</span>', "<span class=\"ihint\">You confirmed the fix. The last merge failed.</span>") : '');
  return uiText('<div class="entry jcard fixcard"><div class="lbl"><span>修复报告 · 修复会话 ', "<div class=\"entry jcard fixcard\"><div class=\"lbl\"><span>Fix report · Session ") + iesc(e.fix_id || '') + '</span><span class="at">' + iesc(e.at) + '</span></div>'
    + uiText('<div class="part"><b>改了什么</b><p>', "<div class=\"part\"><b>Changes</b><p>") + iesc(e.changed) + '</p></div>'
    + uiText('<div class="part"><b>怎样验证</b><p>', "<div class=\"part\"><b>How to verify</b><p>") + iesc(e.verify) + '</p></div>'
    + (e.regression ? uiText('<div class="part"><b>回归数据</b><p>', "<div class=\"part\"><b>Regression data</b><p>") + iesc(e.regression) + '</p></div>' : '')
    + (acts ? '<div class="actions">' + acts + '</div>' : '') + '</div>';
}
// 可合并的结论、确认合并以后：点一下跳到保留的原单
function originalLink(id) {
  if (!id) return '';
  var target = issueById(id);
  return target
    ? '<button class="issue-link" type="button" data-act="jump" data-pane="thread" data-id="' + iesc(target.id) + uiText('">查看原单 ', "\">Open original issue ") + iesc(target.num) + '</button>'
    : uiText('原单 ', "Original issue ") + iesc('#' + String(id).slice(2));
}
// AI 复查（问题页的「刷新」）对这一条的结论：是什么、依据、做了什么、复现步骤
var REVIEW_WORDS = { gone: uiText('不存在', "Cannot confirm"), stale: uiText('已过期', "Out of date"), merge: uiText('可合并', "Can be grouped"), still: uiText('仍存在', "Still present"), unclear: uiText('没查清', "Inconclusive"), recheck: uiText('要重查', "Needs another review") };
function reviewCard(e) {
  return uiText('<div class="entry jcard"><div class="lbl"><span>AI 复查 · ', "<div class=\"entry jcard\"><div class=\"lbl\"><span>AI review · ") + iesc(REVIEW_WORDS[e.kind] || e.kind) + '</span><span class="at">' + iesc(e.at) + '</span></div>'
    + uiText('<p class="muted">分支 ', "<p class=\"muted\">Branch ") + iesc(e.branch || uiText('（不在任何分支上）', "(detached HEAD)")) + (e.commit ? ' · ' + iesc(e.commit.slice(0, 12)) : '') + '</p>'
    + '<p>' + iesc(e.text) + uiText('</p><p class="muted">AI 做了：', "</p><p class=\"muted\">AI checks: ") + iesc(e.ran) + '</p>'
    + (e.steps ? uiText('<p>复现步骤：', "<p>Steps to reproduce: ") + iesc(e.steps) + uiText('</p><p>实际结果：', "</p><p>Actual result: ") + iesc(e.observed) + '</p>' : '')
    + (e.target ? uiText('<p>并入 ', "<p>Merge into ") + originalLink(e.target) + '</p>' : '') + '</div>';
}
function threadEntry(i, e, lastJudge, lastFix) {
  switch (e.t) {
    case 'report': return uiText('<div class="entry report"><div class="lbl"><span>提报 · ', "<div class=\"entry report\"><div class=\"lbl\"><span>Report · ") + iesc(e.who) + '</span><span class="at">' + iesc(e.at) + '</span></div><p>' + iesc(e.text) + '</p>' + ithumbs(e.shots) + '</div>';
    case 'reproduction': return uiText('<div class="entry jcard"><div class="lbl"><span>Agent · 复现补充</span><span class="at">', "<div class=\"entry jcard\"><div class=\"lbl\"><span>Agent · Reproduction details</span><span class=\"at\">") + iesc(e.at) + '</span></div>'
      + uiText('<p>复现步骤：', "<p>Steps to reproduce: ") + iesc(e.steps) + uiText('</p><p>实际看到：', "</p><p>Observed: ") + iesc(e.observed) + '</p>'
      + uiText('<div class="muted small">来源会话 <span class="mono">', "<div class=\"muted small\">Source session <span class=\"mono\">") + iesc(e.origin) + '</span></div>' + ithumbs(e.shots) + '</div>';
    case 'possible_duplicate': {
      var candidate = issueById(e.candidate);
      var number = '#' + String(e.candidate).slice(2);
      return uiText('<div class="entry jcard"><div class="lbl"><span>Agent · 可能重复</span></div><p>', "<div class=\"entry jcard\"><div class=\"lbl\"><span>Agent · Possible duplicate</span></div><p>")
        + (candidate ? '<button class="issue-link" type="button" data-act="jump" data-pane="thread" data-id="' + iesc(candidate.id) + '">' + iesc(candidate.num + ' ' + candidate.title) + '</button>' : iesc(number))
        + '</p><p>' + iesc(e.reason) + '</p></div>';
    }
    case 'ai': return '<div class="entry bubble ai"><div class="lbl"><span class="aitag">AI</span><span class="at">' + iesc(e.at) + '</span></div><p>' + iesc(e.text) + '</p></div>';
    case 'user': return uiText('<div class="entry bubble me"><div class="lbl"><span>你', "<div class=\"entry bubble me\"><div class=\"lbl\"><span>You") + (e.probe ? uiText(' · 说给排查会话', " · Sent to investigation") : '') + '</span><span class="at">' + iesc(e.at) + '</span></div>' + (e.text ? '<p>' + iesc(e.text) + '</p>' : '') + ithumbs(e.shots) + '</div>';
    case 'judge': return judgeCard(i, e, e === lastJudge);
    case 'fail': return failCard(i, e, e === lastJudge);
    case 'finding': return findingCard(i, e, e === lastJudge);
    case 'fix': return fixCard(i, e, e === lastFix);
    case 'review': return reviewCard(e);
    case 'review_resolution': {
      var done = { gone: uiText('确认不是问题 · 转入已取消', "Confirmed as not an issue · Cancelled"), stale: uiText('确认已过期 · 转入已处理', "Confirmed out of date · Resolved"), merge: uiText('标记为重复', "Marked as duplicate"), duplicate: uiText('标记为重复', "Marked as duplicate"), fixed: uiText('确认结案', "Confirmed closure") };
      return uiText('<div class="entry jcard"><div class="lbl"><span>AI 复查 · 你确认了：', "<div class=\"entry jcard\"><div class=\"lbl\"><span>AI review · Applied: ") + iesc(done[e.kind] || (e.target ? uiText('标记为重复', "Marked as duplicate") : uiText('确认结案', "Confirmed closure")))
        + '</span><span class="at">' + iesc(e.at) + uiText('</span></div><p class="muted">分支 ', "</span></div><p class=\"muted\">Branch ") + iesc(e.branch || uiText('（游离 HEAD）', "(detached HEAD)"))
        + (e.commit ? ' · ' + iesc(e.commit.slice(0, 12)) : '') + '</p><p>' + iesc(e.evidence) + '</p>' + originalLink(e.target) + '</div>';
    }
    case 'sys': return '<div class="sys' + (e.close ? ' close' : '') + '"><span>' + iesc(e.text) + '</span></div>';
  }
  return '';
}
function sessionNote(i) {
  var run = fixRun(i), w = waiting[run];
  if (w) return uiText('<span class="ph amber">修复会话', "<span class=\"ph amber\">Fix session") + WAIT[w.kind] + '</span>';
  if (running[run]) return uiText('<span class="muted small">正在干活</span>', "<span class=\"muted small\">Working</span>");
  return '';
}
// 同批的问题：点一下跳过去，各自的状态是修复中、等你确认、已确认、已处理
function sibState(x) {
  if (x.state === 'fixing') return [uiText('修复中', "Fixing"), 'blue'];
  if (x.state === 'review') return x.confirmed ? [uiText('已确认', "Confirmed"), 'green'] : [uiText('等你确认', "Awaiting confirmation"), 'amber'];
  if (x.state === 'resolved') return [uiText('已处理', "Resolved"), 'green'];
  return [x.state_name, 'quiet'];
}
function sibsHtml(i, all) {
  if (!i.siblings || !i.siblings.length) return '';
  return uiText('<div class="sibs"><span class="muted small">一起修的还有</span>', "<div class=\"sibs\"><span class=\"muted small\">Also in this fix</span>") + i.siblings.map(function (x) {
    var st = sibState(x);
    return '<button class="sibchip" data-act="jump" data-id="' + x.id + '" title="' + iesc(x.num + ' ' + x.title) + '"><span class="mono">' + x.num + '</span><span class="t">' + iesc(x.title) + '</span>'
      + (all || x.state !== 'fixing' ? '<span class="st ph ' + st[1] + '">' + st[0] + '</span>' : '') + '</button>';
  }).join('') + '</div>';
}
function threadHtml(i) {
  var judges = i.thread.filter(function (e) { return e.t === 'judge' || e.t === 'fail' || e.t === 'finding'; }), fixes = i.thread.filter(function (e) { return e.t === 'fix'; });
  var h = i.thread.map(function (e) { return threadEntry(i, e, judges[judges.length - 1], fixes[fixes.length - 1]); }).join('');
  var duplicates = IS.issues.filter(function (issue) { return issue.closed_as === 'duplicate' && issue.duplicate_of === i.id; });
  if (duplicates.length) h += uiText('<div class="entry jcard"><div class="lbl">重复提报</div>', "<div class=\"entry jcard\"><div class=\"lbl\">Duplicate report</div>") + duplicates.map(function (issue) {
    return '<button class="issue-link" type="button" data-act="jump" data-pane="thread" data-id="' + iesc(issue.id) + '">' + iesc(issue.num + ' ' + issue.title) + '</button>';
  }).join('') + '</div>';
  if (iBusy(i)) h += uiText('<div class="entry jcard"><div class="lbl"><span class="aitag">AI</span><span>AI 初判</span></div><div class="analysing"><span class="spin"></span><b>', "<div class=\"entry jcard\"><div class=\"lbl\"><span class=\"aitag\">AI</span><span>AI triage</span></div><div class=\"analysing\"><span class=\"spin\"></span><b>") + (i.triage === 'queued' ? uiText('排队等分析…', "Queued for triage…") : uiText('正在分析…', "Analyzing…")) + uiText('</b></div><p class="muted small">已收到。只看产品事实，不读代码。你可以回到列表继续记别的问题。</p></div>', "</b></div><p class=\"muted small\">Received. Triage uses project facts. You can return to the list and report other issues.</p></div>");
  var pw = waiting[probeRun(i)];
  if (probeOpen(i) && !iBusy(i) && (running[probeRun(i)] || waiting[probeRun(i)])) {
    var probeStatus = pw ? '<span class="ph amber">' + iesc(WAIT[pw.kind]) + '</span>'
      : uiText('<span class="muted small">正在干活</span>', '<span class="muted small">Working</span>');
    h += uiText('<div class="iwait"><span>排查会话在看这条问题（只读，不改任何文件）</span>',
      '<div class="iwait"><span>The read-only session is investigating this issue</span>')
      + probeStatus + ib('openprobe', uiText('打开会话', 'Open session')) + '</div>';
  }
  if (i.state === 'fixing' && i.fix) h += uiText('<div class="iwait"><span>修复会话 ', "<div class=\"iwait\"><span>Fix session ") + iesc(i.fix.id) + uiText(' 正在处理', " is working") + (i.batch > 1 ? uiText(' · 一起修 ', " · Fixing ") + i.batch + uiText(' 条', " issues together") : '') + '</span>' + sessionNote(i) + ib('opensess', uiText('打开会话', "Open session")) + sibsHtml(i, false) + '</div>';
  if (i.state === 'review' && i.siblings && i.siblings.length) h += '<div class="iwait">' + sibsHtml(i, true) + uiText('<span class="ihint" style="margin-left:0;flex-basis:100%">每一条都要单独看过再确认；同批都确认了才一起合并。</span></div>', "<span class=\"ihint\" style=\"margin-left:0;flex-basis:100%\">Check each fix separately. The batch merges after all are confirmed.</span></div>");
  h += issueTraceHtml(i) + issueUsageHtml(i);
  if (IS.errors[i.id]) h += '<div class="ierror">' + iesc(IS.errors[i.id]) + '</div>';
  return '<div class="inner">' + h + '</div>';
}

function stripHtml(i) {
  var h = uiText('<span class="muted small">相关功能点</span>', "<span class=\"muted small\">Related feature</span>");
  if (!i.feature) h += uiText('<span class="fchip none">未关联</span><button class="s" data-act="pick">关联功能点</button>', "<span class=\"fchip none\">Unlinked</span><button class=\"s\" data-act=\"pick\">Link feature</button>");
  else h += uiText('<button class="fchip link" data-act="featfilter" title="只看这个功能点的问题">', "<button class=\"fchip link\" data-act=\"featfilter\" title=\"Show issues linked to this feature\">") + iesc(featName(i.feature)) + uiText('</button><button class="s" data-act="pick" aria-label="更改功能点">改</button><button class="s" data-act="unfeat" aria-label="去掉功能点">去掉</button>', "</button><button class=\"s\" data-act=\"pick\" aria-label=\"Change feature\">Change</button><button class=\"s\" data-act=\"unfeat\" aria-label=\"Unlink feature\">Unlink</button>");
  if (termRun(i)) {
    var term = IS.pane[i.id] === 'term', dot = waiting[termRun(i)] ? ' class="dot"' : '';
    h += uiText('<span class="views" role="group" aria-label="右边显示"><button data-act="pane" data-k="thread" aria-pressed="', "<span class=\"views\" role=\"group\" aria-label=\"Detail view\"><button data-act=\"pane\" data-k=\"thread\" aria-pressed=\"") + !term + uiText('">对话</button><button data-act="pane" data-k="term" aria-pressed="', "\">Conversation</button><button data-act=\"pane\" data-k=\"term\" aria-pressed=\"") + term + '"' + dot + '>' + termName(i) + '</button></span>';
  }
  if (i.state === 'fixing') {
    h += '<div class="finish-action"><button id="issue-finish-toggle" type="button" data-act="finish-menu" aria-expanded="false" aria-controls="issue-finish-menu"'
      + (IS.busy[i.id] ? ' disabled' : '') + uiText('>结束处理 ▾</button>', ">Close issue ▾</button>")
      + uiText('<div id="issue-finish-menu" class="finish-menu" hidden><button type="button" data-act="finish" data-k="resolved">转入已处理</button>', "<div id=\"issue-finish-menu\" class=\"finish-menu\" hidden><button type=\"button\" data-act=\"finish\" data-k=\"resolved\">Mark resolved</button>")
      + uiText('<button type="button" data-act="finish" data-k="closed">转入已取消</button></div></div>', "<button type=\"button\" data-act=\"finish\" data-k=\"closed\">Cancel issue</button></div></div>");
  }
  if (IS.picker) {
    var groups = {};
    IS.features.forEach(function (f) { (groups[f.module + ' ' + f.module_title] = groups[f.module + ' ' + f.module_title] || []).push(f); });
    h += '<div class="picker" role="menu">' + (IS.features.length ? Object.keys(groups).map(function (g) {
      return '<div class="grp">' + iesc(g) + '</div>' + groups[g].map(function (f) { return '<button role="menuitem" data-act="setfeat" data-c="' + f.id + '">' + iesc(f.id + ' ' + f.title) + '</button>'; }).join('');
    }).join('') : uiText('<div class="grp">这个项目还没有功能点。</div>', "<div class=\"grp\">This project has no features yet.</div>")) + '</div>';
  }
  return '<div class="strip">' + h + '</div>';
}
function renderIssueHead(i) {
  var p = iphrase(i);
  $('issue-head').innerHTML = uiText('<div class="inner"><div><span class="mono">问题 ', "<div class=\"inner\"><div><span class=\"mono\">Issue ") + i.num + '</span><span class="pill" style="color:' + ISCLR[i.state] + '">' + i.state_name + '</span>'
    + (p[0] === i.state_name ? '' : '<span class="pill">' + iesc(p[0]) + '</span>') + '</div>'
    + '<h2>' + iesc(i.title) + uiText('</h2><div class="muted small">来源：', "</h2><div class=\"muted small\">Source: ") + iesc(i.source) + uiText(' · 记于 ', " · Reported ") + iesc(String(i.created).slice(0, 16)) + '</div>' + stripHtml(i) + '</div>';
}

// 排查会话的终端开着：这时在对话里说的话交给它，不再做只看产品事实的重新初判（主进程按它是不是真开着来定，见 issues.js 的 reply）
function probeLive(i) { var t = i && probeOpen(i) && fixTerms[probeRun(i)]; return !!(t && !t.dead); }
function replyHot(i) { return !!IS.hot[i.id] || (i.state === 'todo' && !iBusy(i) && !i.deferred && i.kind === 'ask'); }
function replyPlaceholder(i) {
  if (i.state === 'fixing') return uiText('补充给修复会话…', "Send an update to the fix session…");
  if (probeLive(i)) return uiText('补充给排查会话…（它读过代码，会带着你的话接着查）', "Send more details to the investigation session…");
  if (i.state === 'review') return IS.hot[i.id] ? uiText('说说还有什么问题，发送后回到修复会话 ', "Describe what still happens. Your reply goes to fix session ") + i.fix.id + '…' : uiText('有话想说，直接回复…', "Reply…");
  if (i.kind === 'ask' && !iBusy(i)) return uiText('回答 AI 的问题，或补充你看到的情况…', "Answer the AI's question or describe what you observed…");
  return uiText('不同意这个判断，或想补充什么，直接说…', "Disagree with this assessment or add more details…");
}
function renderIssueReply(i) {
  var box = $('issue-reply');
  var key = [i.id, i.state, iBusy(i), replyHot(i), (IS.shots[i.id] || []).length, i.closed_as, i.deferred, IS.busy[i.id], probeLive(i)].join('|');
  if (key === IS.rkey) return;
  IS.rkey = key;
  box.className = 'replybox' + (replyHot(i) ? ' hot' : '');
  if (i.state === 'resolved' || i.state === 'closed') {
    box.innerHTML = '<div class="closedbar">' + (i.closed_as === 'requirement'
      ? uiText('<span>已转为需求 ', "<span>Converted to requirement ") + iesc(i.requirement) + uiText('，后续在需求里跟进</span><button class="quiet" data-act="gotoreq">去看需求</button>', ". Follow up on the requirement.</span><button class=\"quiet\" data-act=\"gotoreq\">Open requirement</button>")
      : uiText('<span>这条问题', "<span>This issue ") + iesc(i.state_name) + uiText('</span><button class="quiet" data-act="reopen">重新打开</button>', "</span><button class=\"quiet\" data-act=\"reopen\">Reopen</button>")) + '</div>';
    return;
  }
  box.innerHTML = '<div class="inner">' + ithumbs(IS.shots[i.id], true) + uiText('<textarea id="issue-text" rows="1" aria-label="回复" placeholder="', "<textarea id=\"issue-text\" rows=\"1\" aria-label=\"Reply\" placeholder=\"") + iesc(replyPlaceholder(i)) + '"></textarea>'
    + uiText('<div class="tools"><button class="quiet" data-act="attach" aria-label="添加截图">▣ 截图</button><span class="grow">Enter 发送 · Shift+Enter 换行 · 可直接粘贴截图</span>', "<div class=\"tools\"><button class=\"quiet\" data-act=\"attach\" aria-label=\"Attach screenshot\">▣ Screenshot</button><span class=\"grow\">Enter to send · Shift+Enter for a new line · Paste screenshots</span>")
    + (IS.hot[i.id] ? ib('cancelbad', uiText('算了', "Cancel")) : '') + '<button class="primary" data-act="send"' + (IS.busy[i.id] ? ' disabled' : '') + '>' + (IS.hot[i.id] ? uiText('退回修复', "Return to fix") : uiText('发送', "Send")) + '</button></div></div>';
  var t = $('issue-text'); t.value = IS.drafts[i.id] || ''; iAutosize(t);
}
function iAutosize(t) { t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight + 2, 160) + 'px'; }

function renderIssueDetail() {
  var i = curIssue();
  var termOn = !!(i && termRun(i) && IS.pane[i.id] === 'term');
  $('issue-thread').hidden = termOn; $('issue-reply').hidden = termOn || !i; $('issue-term').hidden = !termOn;
  showInbox('issue-inbox', termOn ? termRun(i) : null);
  window.shell.viewing($('issues').hidden || !i ? null : termOn ? termRun(i) : 'issue:' + i.id);
  if (!i) { $('issue-head').innerHTML = ''; $('issue-thread').innerHTML = '<div class="noselect">' + (IS.issues.length ? uiText('选择一条问题查看详情', "Select an issue to see its details") : uiText('用的时候发现毛病，点左边的「＋ 记个问题」记下来。AI 先判断是哪一类，下一步由你决定。', "Report an issue on the left. AI will triage it and leave the next step to you.")) + '</div>'; IS.rkey = ''; return; }
  renderIssueHead(i);
  if (termOn) { showFixTerm(i); return; }
  renderIssueReply(i);
  var th = $('issue-thread'), len = i.thread.length + '|' + i.state + '|' + iBusy(i);
  var stick = IS.lastSel !== i.id || IS.lastLen !== len, top = th.scrollTop;
  th.innerHTML = threadHtml(i);
  th.scrollTop = stick ? th.scrollHeight : top;
  IS.lastSel = i.id; IS.lastLen = len;
}
