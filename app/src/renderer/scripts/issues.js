// 问题页：左边的列表（状态页签、待处理下的类别、按来源和功能点筛选），以及大家共用的小工具。
// 数据由主进程的 issues.js 推过来（rf.py issue list --json，加上哪些正在初判）。右边的对话在 issue-thread.js，按钮和对话框在 issue-actions.js。
var IS = {
  issues: [], features: [], loaded: false,
  tab: 'todo', kind: 'all', source: 'all', feat: 'all', sel: null, picker: false,
  pick: {},   // 选中要一起修的问题编号 -> true（只在待处理页签里；离开页签、开始修复、取消选择都清掉）
  pane: {},   // 问题编号 -> 'thread' | 'term'：右边显示对话还是修复会话（或排查会话），默认对话
  drafts: {}, // 问题编号 -> 没发出去的回复
  shots: {},  // 问题编号 -> 回复里附的截图路径
  hot: {},    // 问题编号 -> 点了「还有问题」，这次回复是退回
  errors: {}, // 问题编号 -> 上一次操作的错误
  busy: {},   // 问题编号 -> 正在等主进程回话的操作
  rkey: '', lastSel: null, lastLen: '',
};
var ISTATES = [['todo', uiText('待处理', "Open")], ['fixing', uiText('处理中', "In progress")], ['review', uiText('待你确认', "Awaiting confirmation")], ['resolved', uiText('已处理', "Resolved")], ['closed', uiText('已取消', "Cancelled")]];
var IKINDS = { bug: uiText('是问题', "Issue"), request: uiText('可转需求', "Candidate requirement"), usage: uiText('已有用法', "Existing workflow"), ask: uiText('待补充', "Needs details") };
var IKCLR = { bug: '#f0a08e', request: '#9bbaff', usage: '#80dba6', ask: '#edbf70' };
var ISCLR = { todo: '#a1a8b4', fixing: '#9bbaff', review: '#edbf70', resolved: '#80dba6', closed: '#a1a8b4' };
var iesc = HtmlText.esc;
var ifile = function (p) { return encodeURI('file://' + p).replace(/#/g, '%23'); };
var issueById = function (id) { return IS.issues.filter(function (i) { return i.id === id; })[0]; };
var curIssue = function () { return issueById(IS.sel); };
var featOf = function (id) { return IS.features.filter(function (f) { return f.id === id; })[0]; };
var featName = function (id) { var f = featOf(id); return id + (f ? ' ' + f.title : ''); };
var fixRun = function (i) { return i && i.fix ? 'fix:' + i.fix.id : null; };
var fixOpen = function (i) { return !!(i && i.fix && i.fix.state === 'fixing' && (i.state === 'fixing' || i.state === 'review')); };
// 交给 AI 排查：只读的排查会话，编号 probe:问题编号。有排查会话的待处理问题，右边的切换是「对话 / 排查会话」；一开始修复，修复会话顶上来（排查的结论还在对话里）
var probeRun = function (i) { return i ? 'probe:' + i.id : null; };
var probeOpen = function (i) { return !!(i && i.state === 'todo' && i.probe && !fixOpen(i)); };
// 右边的终端是哪一个：修复中的是修复会话，否则有排查会话的是排查会话
var termRun = function (i) { return fixOpen(i) ? fixRun(i) : probeOpen(i) ? probeRun(i) : null; };
var termName = function (i) { return fixOpen(i) ? uiText('修复会话', "Fix session") : uiText('排查会话', "Investigation session"); };
var iBusy = function (i) { return i.state === 'todo' && !!(i.triage || i.needs_triage); }; // 在排队或正在初判
var iFeatOK = function (i) { return IS.feat === 'all' || (IS.feat === 'none' ? !i.feature : i.feature === IS.feat); };
var iSourceOK = function (i) { return IS.source === 'all' || IS.source === (i.source === 'Agent' ? 'ai' : 'human'); };
var iScopeOK = function (i) { return iFeatOK(i) && iSourceOK(i); };

// 能马上修的：判成「是问题」、没放到以后再说、不在分析、初判没失败。只有这样的行有勾选框
var iEligible = function (i) { return !!i && i.state === 'todo' && i.kind === 'bug' && !i.deferred && !iBusy(i) && !i.triage_failed; };
var iPickList = function () { return Object.keys(IS.pick).map(issueById).filter(iEligible).sort(function (a, b) { return a.id < b.id ? 1 : -1; }); };
var iPrunePick = function () { var keep = {}; if (IS.tab === 'todo') iPickList().forEach(function (i) { keep[i.id] = true; }); IS.pick = keep; };
// 勾了几条以后的提示：功能点不一样、都没关联功能点时，分开修更稳
function iMultiHint(list) {
  var k = {}; list.forEach(function (i) { k[i.feature || ''] = 1; });
  var n = Object.keys(k).length;
  if (n > 1) return uiText('涉及 ', "These issues involve ") + n + uiText(' 个功能点，分开修更稳。', " features. Fix them separately.");
  if (k[''] && list.length > 1) return uiText('这几条都还没关联功能点，分开修更稳。', "These issues have no linked features yet. Fix them separately.");
  return '';
}
var iNums = function (ids) { return ids.map(function (x) { return '#' + String(x).slice(2); }).join('、'); };

// 一句话的状态：列表每行、详情页头上都用它
function iphrase(i) {
  if (i.state === 'todo') {
    if (i.triage === 'queued') return [uiText('排队分析', "Queued"), 'blue'];
    if (iBusy(i)) return [uiText('分析中', "Analyzing"), 'blue'];
    var w = probeOpen(i) && waiting[probeRun(i)];
    if (probeOpen(i) && running[probeRun(i)]) return [uiText('排查中', "Investigating"), 'blue'];
    if (w && w.kind === 'answer') return [uiText('等你回答', "Waiting for your answer"), 'amber'];
    if (w && w.kind === 'approve') return [uiText('等你批准', "Approval required"), 'amber'];
    if (i.deferred) return [uiText('以后再说', "Deferred"), 'quiet'];
    if (i.triage_failed) return [uiText('没判出来', "No assessment"), 'red'];
    if (!i.kind) return [uiText('还没判断', "Unassessed"), 'quiet'];
    return i.kind === 'ask' ? [uiText('等你补充', "Needs more details"), 'amber'] : [uiText('等你决定', "Needs your decision"), 'blue'];
  }
  var m = fixOpen(i) && merging[fixRun(i)];
  if (m) return [m.phase === 'syncing' ? uiText('AI 在修复会话里合并 ', "The fix session is merging ") + (m.base || uiText('目标分支', "the target branch")) : m.phase === 'stuck' ? uiText('合并卡住了，要你处理', "Merge needs your help") : m.phase === 'merging' ? uiText('正在合并', "Merging") : uiText('排队等合并', "Queued for merge"), m.phase === 'stuck' ? 'red' : 'blue'];
  if (i.state === 'fixing') return [i.batch > 1 ? uiText('修复中 · 一起修 ', "Fixing together · ") + i.batch + uiText(' 条', " issues") : uiText('修复中', "Fixing"), 'blue'];
  if (i.state === 'review') return i.confirmed_waiting ? [uiText('已确认，等同批的 ', "Confirmed · Waiting for ") + iNums(i.waiting_for), 'green'] : i.confirmed ? [uiText('已确认，还没合并进去', "Confirmed · Awaiting merge"), 'amber'] : [uiText('等你确认', "Awaiting confirmation"), 'amber'];
  if (i.state === 'resolved') return [uiText('已处理', "Resolved"), 'green'];
  if (i.closed_as === 'requirement') return [uiText('已转需求', "Converted to requirement"), 'quiet'];
  if (i.closed_as === 'duplicate') return [uiText('重复 · #', "Duplicate · #") + String(i.duplicate_of || '').slice(2), 'quiet'];
  if (i.closed_as === 'not_issue') return [uiText('不是问题', "Not an issue"), 'quiet'];
  return [uiText('已取消', "Cancelled"), 'quiet'];
}
// 等你处理的：待补充、在等你回答的，和修好了等你确认的。「问题」图标上的数字就是这些
function iWaitsForYou(i) {
  if (i.state === 'review') return !i.confirmed;
  var w = probeOpen(i) && !iBusy(i) && waiting[probeRun(i)];
  if (w && (w.kind === 'answer' || w.kind === 'approve')) return true; // 排查会话在问你话、等你批准
  return i.state === 'todo' && i.kind === 'ask' && !iBusy(i) && !i.deferred && !i.triage_failed;
}

function iVisible() {
  return IS.issues.filter(function (i) { return i.state === IS.tab && iScopeOK(i) && (IS.tab !== 'todo' || IS.kind === 'all' || i.kind === IS.kind); })
    .sort(function (a, b) { return (a.deferred - b.deferred) || (a.id < b.id ? 1 : -1); });
}
function iReveal(i) {
  if (!i) return;
  IS.tab = i.state;
  if (!iFeatOK(i)) IS.feat = 'all';
  if (!iSourceOK(i)) IS.source = 'all';
  if (IS.tab === 'todo' && IS.kind !== 'all' && i.kind !== IS.kind) IS.kind = 'all';
}
function iEnsureSel() { var v = iVisible(); if (!v.some(function (x) { return x.id === IS.sel; })) IS.sel = v[0] ? v[0].id : null; }

function renderIssueTabs() {
  var base = IS.issues.filter(iScopeOK);
  $('issue-tabs').innerHTML = ISTATES.map(function (s) {
    var c = base.filter(function (i) { return i.state === s[0]; }).length;
    var attn = s[0] === 'review' ? base.some(function (i) { return i.state === 'review' && !i.confirmed; }) : false;
    return '<button class="tab' + (attn ? ' attn' : '') + '" role="tab" aria-selected="' + (IS.tab === s[0]) + '" data-act="tab" data-k="' + s[0] + '"><b>' + c + '</b><span>' + s[1] + '</span></button>';
  }).join('');
}
function renderIssueChips() {
  var box = $('issue-chips');
  box.hidden = IS.tab !== 'todo';
  if (box.hidden) { box.innerHTML = ''; return; }
  var base = IS.issues.filter(function (i) { return i.state === 'todo' && iScopeOK(i); });
  box.innerHTML = [['all', uiText('全部', "All")]].concat(Object.keys(IKINDS).map(function (k) { return [k, IKINDS[k]]; })).map(function (d) {
    var c = d[0] === 'all' ? base.length : base.filter(function (i) { return i.kind === d[0]; }).length;
    return '<button class="chip" aria-pressed="' + (IS.kind === d[0]) + '" data-act="kind" data-k="' + d[0] + '">' + d[1] + ' ' + c + '</button>';
  }).join('');
}
function renderIssueSources() {
  $('issue-sources').querySelectorAll('button').forEach(function (button) {
    button.setAttribute('aria-pressed', IS.source === button.dataset.k);
  });
}
function renderIssueFeat() {
  var sel = $('issue-feat');
  var want = [['all', uiText('全部功能点', "All features")]].concat(IS.features.map(function (f) { return [f.id, f.id + ' ' + f.title]; }), [['none', uiText('未关联', "Unlinked")]]);
  var key = want.map(function (x) { return x[0] + x[1]; }).join('|');
  if (sel.dataset.key !== key) { sel.innerHTML = want.map(function (x) { return '<option value="' + iesc(x[0]) + '">' + iesc(x[1]) + '</option>'; }).join(''); sel.dataset.key = key; }
  sel.value = IS.feat;
}
function renderIssueRows() {
  var v = iVisible();
  $('issue-rows').innerHTML = v.length ? v.map(function (i) {
    var p = iphrase(i), f = i.feature;
    var pk = IS.tab !== 'todo' ? '' : iEligible(i)
      ? '<label class="pk"><input type="checkbox" data-act="chk" data-id="' + i.id + uiText('" aria-label="选择 ', "\" aria-label=\"Select ") + i.num + uiText('，一起修"', " to fix together\"") + (IS.pick[i.id] ? ' checked' : '') + '></label>' : '<span class="pk"></span>';
    return '<div class="rw' + (i.deferred && i.state === 'todo' ? ' deferred' : '') + (i.id === IS.sel ? ' cur' : '') + '">' + pk + '<button class="irow" data-act="open" data-id="' + i.id + '" aria-current="' + (i.id === IS.sel) + '">'
      + '<strong>' + iesc(i.title) + '</strong><span class="imeta"><span>' + i.num + '</span><span title="' + (f ? iesc(featName(f)) : '') + '">' + (f ? iesc(f) : uiText('未关联', "Unlinked")) + '</span>'
      + (i.source !== '你' ? '<span class="src">' + iesc(i.source) + '</span>' : '') + '<span class="ph ' + p[1] + '">' + iesc(p[0]) + '</span>'
      + iReviewMark(i) + InboxModel.badge(termRun(i)) + '</span></button></div>';
  }).join('') : '<div class="iempty">' + (IS.issues.length ? uiText('这个筛选下还没有问题。', "No issues match these filters.") : uiText('还没有问题。用的时候发现毛病，点上面的「记个问题」。', "No issues yet. Select Report issue to report one.")) + '</div>';
  refreshInboxBadges($('issue-rows'));
}
// 选择条：已选几条、取消选择、一起修（两条起才能点）；同功能点的提示、不在当前筛选里的几条、分开修更稳的提示
function renderIssueSelbar() {
  var el = $('issue-selbar'), l = iPickList();
  if (!l.length) { el.hidden = true; el.innerHTML = ''; return; }
  var shown = {}; iVisible().forEach(function (i) { shown[i.id] = 1; });
  var hid = l.filter(function (i) { return !shown[i.id]; }).length;
  var fs = {}; l.forEach(function (i) { fs[i.feature || ''] = 1; });
  var f = Object.keys(fs).length === 1 ? Object.keys(fs)[0] : '';
  var more = f ? IS.issues.filter(function (i) { return iEligible(i) && i.feature === f && !IS.pick[i.id]; }).length : 0;
  var hint = iMultiHint(l);
  el.hidden = false;
  el.innerHTML = uiText('<div class="r1"><b>已选 ', "<div class=\"r1\"><b>Selected ") + l.length + uiText(' 条</b><button class="quiet" data-act="selclear">取消选择</button><button class="primary" data-act="batchfix"', "</b><button class=\"quiet\" data-act=\"selclear\">Clear selection</button><button class=\"primary\" data-act=\"batchfix\"") + (l.length < 2 ? uiText(' disabled title="至少选 2 条"', " disabled title=\"Select at least two issues\"") : '') + uiText('>一起修</button></div>', ">Fix together</button></div>")
    + (hid ? uiText('<div class="note">其中 ', "<div class=\"note\">") + hid + uiText(' 条不在当前筛选里</div>', " selected issues are outside the current filters</div>") : '')
    + (more ? '<div class="note"><button class="link" data-act="selfeat" data-c="' + iesc(f) + uiText('">把 ', "\">Also select the other issues in ") + iesc(f) + uiText(' 的另外 ', " (") + more + uiText(' 条也选上</button></div>', ")</button></div>") : '')
    + (hint ? '<div class="note warn">' + hint + '</div>' : '');
}
function renderIssueList() { iPrunePick(); renderIssueTabs(); renderIssueChips(); renderIssueSources(); renderIssueFeat(); renderIssueRows(); renderIssueSelbar(); }
function issueBadge() { $('view-issues').dataset.badge = IS.issues.filter(iWaitsForYou).length || ''; }
function renderIssues() {
  issueBadge();
  if ($('issues').hidden) return;
  renderIssueList(); renderIssueDetail();
}
