// 测试集：需求面板的两块、测试页的表格、查看器的用例列表、确认完成前的判断、「让 AI 改」交给谁。
// 数据来自 rf.py test list / show / run --json；这里只有纯函数（界面、主进程和测试都用），不碰页面。普通脚本，也能被 node 用 require 读进来。
var TestsModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var categories = { 正常: uiText('正常', 'Normal'), 边界: uiText('边界', 'Edge case'), 异常: uiText('异常', 'Error case') };
  var CATS = Object.keys(categories);
  function categoryName(cat) { return Object.hasOwn(categories, cat) ? categories[cat] : cat; }
  var STATUS = { pass: uiText('通过', "Passed"), fail: uiText('失败', "Failed"), broken: uiText('跑不起来', "Couldn't run"), unrun: uiText('还没跑', "Not run") };
  var AI_EXAMPLES = [uiText('补几条边界值', "Add edge cases"), uiText('补几条异常输入', "Add invalid inputs"), uiText('来一条数据量很大的', "Add a large input"), uiText('换一批不同的数据', "Try different data")];

  var esc = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).esc;
  function json(v) { return v === undefined ? '' : JSON.stringify(v, null, 2); }

  // 一个测试集（list 的一行）上次的结果：失败、跑不起来、还没跑、通过各几条。数据比上次跑的时候多了，多出来的算还没跑
  function counts(row) {
    var last = row && row.last, n = (row && row.cases) || 0;
    var c = last && last.counts ? last.counts : { pass: 0, fail: 0, broken: 0 };
    var out = { pass: c.pass || 0, fail: c.fail || 0, broken: c.broken || 0 };
    out.unrun = Math.max(0, n - out.pass - out.fail - out.broken);
    out.total = out.pass + out.fail + out.broken + out.unrun;
    return out;
  }
  // 比例条的几段，按失败、跑不起来、还没跑、通过的顺序；每段的宽度按条数（不是一条一格）
  function segments(c) { return [['f', c.fail], ['e', c.broken], ['n', c.unrun], ['p', c.pass]].filter(function (x) { return x[1] > 0; }); }
  function barHtml(c) {
    var s = segments(c);
    return '<div class="ts-bar" role="img" aria-label="' + esc(resultText(c)) + '">' + (s.length ? s.map(function (x) { return '<i class="' + x[0] + '" style="flex:' + x[1] + '"></i>'; }).join('') : '<i class="z"></i>') + '</div>';
  }
  function pillsHtml(c) {
    return (c.fail ? '<span class="ts-pill r">' + c.fail + uiText(' 条失败</span>', " failed</span>") : '') + (c.broken ? '<span class="ts-pill r">' + c.broken + uiText(' 条跑不起来</span>', " couldn't run</span>") : '')
      + (c.unrun ? '<span class="ts-pill a">' + c.unrun + uiText(' 条还没跑</span>', " not run</span>") : '') + (c.pass ? '<span class="ts-pill g">' + c.pass + uiText(' 条通过</span>', " passed</span>") : '')
      + (!c.total ? uiText('<span class="ts-pill">还没有数据</span>', "<span class=\"ts-pill\">No test data</span>") : '');
  }
  function resultText(c) {
    var parts = [];
    if (c.fail) parts.push(c.fail + uiText(' 条失败', " failed")); if (c.broken) parts.push(c.broken + uiText(' 条跑不起来', " couldn't run")); if (c.unrun) parts.push(c.unrun + uiText(' 条还没跑', " not run")); if (c.pass) parts.push(c.pass + uiText(' 条通过', " passed"));
    return parts.join('，') || uiText('还没有数据', "No test data");
  }
  var bad = function (row) { var c = counts(row); return c.fail + c.broken; };
  // 上次跑不起来（测试代码没启动起来、准备步骤没过）：给「让 AI 来处理」
  function isBroken(row) { return !!(row && !row.stale && row.last && row.last.result === 'broken'); }

  // ---------- 测试页 ----------
  // 「需要看的」：有失败、跑不起来、有提示，或者有改动等你确认；过期的不算
  function needsLook(row) { return !row.stale && (bad(row) > 0 || (row.hints || []).length > 0 || (row.pending_changes || []).length > 0); }
  var FILTERS = [['need', uiText('需要看的', "Needs attention")], ['all', uiText('全部在用的', "All active")], ['inprog', uiText('进行中的需求', "Active requirements")], ['stale', uiText('过期', "Obsolete")]];
  function pass(row, f) {
    if (f === 'stale') return !!row.stale;
    if (row.stale) return false;
    if (f === 'need') return needsLook(row);
    if (f === 'inprog') return !!row.in_progress;
    return true;
  }
  function filterCounts(rows) { var out = {}; FILTERS.forEach(function (x) { out[x[0]] = (rows || []).filter(function (r) { return pass(r, x[0]); }).length; }); return out; }
  // 按功能点分组（功能点编号的顺序），组里先放需要看的
  function groups(rows, f) {
    var by = {}, order = [];
    (rows || []).filter(function (r) { return pass(r, f); }).forEach(function (r) {
      var k = r.feature || '';
      if (!by[k]) { by[k] = { feature: k, title: r.feature_title || '', rows: [], cases: 0 }; order.push(k); }
      by[k].rows.push(r); by[k].cases += r.cases || 0;
    });
    order.sort();
    return order.map(function (k) {
      var g = by[k];
      g.rows.sort(function (a, b) { return (needsLook(b) - needsLook(a)) || (a.id < b.id ? -1 : 1); });
      return g;
    });
  }
  // 挂在哪个需求：标题和状态从需求列表里找（找不到就只给编号）
  function reqLabel(row, reqs) {
    var id = row.requirement || row.fix || '';
    if (!id) return { title: uiText('没有挂需求', "No requirement"), sub: '', going: false };
    var r = (reqs || []).filter(function (x) { return x.id === id; })[0];
    var going = row.in_progress === id || !!(r && (r.state === 'open' || r.state === 'draft'));
    return { title: r ? r.title : id, sub: id + (row.fix ? uiText(' · 修复', " · Fix") : '') + (going ? uiText(' · 进行中', " · In development") : r && r.state === 'done' ? uiText(' · 已完成', " · Completed") : r && r.state === 'dropped' ? uiText(' · 已取消', " · Cancelled") : ''), going: going };
  }
  // 最近一次运行的时间（测试页顶上）
  function lastRunAt(rows) {
    var at = ''; (rows || []).forEach(function (r) { var t = r.last_main && r.last_main.at; if (t && t > at) at = t; });
    return at;
  }

  // 上次全量（rf.py test list 的 full，见 backend/relayforge/testsets/store.py 的 full_status）：合并前的检查只跑一部分，剩下的靠全量
  function fullLine(full) {
    if (!full) return '';
    var last = full.last;
    if (!last) return uiText('主线上还没跑过全量。合并前的检查只跑一部分，点「整套跑一遍」把剩下的也跑了。', "No full run on the main line yet. The pre-merge check runs only part of the suite; select Run all to run the rest.");
    var bad = (last.failing || []).length;
    var text = uiText('上次全量：', "Last full run: ") + String(last.at || '').slice(5, 16) + uiText('，在 ', " on ") + String(last.commit || '').slice(0, 8)
      + (bad ? uiText('，没过 ', ", failing: ") + last.failing.slice(0, 5).join('、') : uiText('，都通过了', ", all passed"));
    if (full.stale) text += full.behind ? uiText('。之后又合进来 ', ". ") + full.behind + uiText(' 次，还没跑过全量。', " merges since then have not had a full run.") : uiText('。代码之后变过。', ". The code has changed since then.");
    else text += '。';
    return text;
  }

  // ---------- 需求面板 ----------
  // list：rf.py test list --req R --json；r：需求（state、stage）；rejected：这次打开应用以来你不同意过的改动（后端丢弃了，只在这里记着显示结果）
  function panel(list, r, rejected) {
    if (!r || !list || list.error) return { visible: false };
    var sets = (list.sets || []).filter(function (s) { return !s.skipped; });
    var pending = [], approved = [];
    sets.concat((list.sets || []).filter(function (s) { return s.skipped; })).forEach(function (s) {
      (s.pending_changes || []).forEach(function (c) { if (c.by === r.id) pending.push(Object.assign({ set: s.id, setTitle: s.title }, c)); });
      (s.approved_changes || []).forEach(function (c) { if (c.by === r.id) approved.push(Object.assign({ set: s.id, setTitle: s.title }, c)); });
    });
    var gone = (rejected || []).filter(function (c) { return c.by === r.id; });
    var totals = { sets: sets.length, cases: 0, pass: 0, fail: 0, broken: 0, unrun: 0 };
    sets.forEach(function (s) { var c = counts(s); totals.cases += s.cases || 0; ['pass', 'fail', 'broken', 'unrun'].forEach(function (k) { totals[k] += c[k]; }); });
    var done = r.state === 'done';
    var going = r.state === 'open';
    var visible = done ? sets.length > 0 : going && (sets.length > 0 || pending.length > 0 || approved.length > 0);
    var hot = [], calm = [];
    sets.forEach(function (s) { (bad(s) > 0 || (s.unseen || 0) > 0 ? hot : calm).push(s); });
    hot.sort(function (a, b) { return (bad(b) > 0) - (bad(a) > 0) || bad(b) - bad(a) || (a.id < b.id ? -1 : 1); });
    return {
      visible: visible, done: done, totals: totals, hot: hot, calm: calm, pending: pending, approved: approved, rejected: gone,
      calmCases: calm.reduce(function (n, s) { return n + (s.cases || 0); }, 0),
      head: totals.sets + uiText(' 个 · ', " · ") + totals.cases + uiText(' 条数据', " test cases") + (totals.fail + totals.broken ? ' · ' + (totals.fail + totals.broken) + uiText(' 条没通过', " unsuccessful") : ''),
      doneLine: uiText('测试集 ', "Test sets: ") + totals.sets + uiText(' 个、', " · ") + totals.cases + uiText(' 条：', " cases: ") + resultText(totals),
    };
  }
  // 全靠人工验收：这个需求一个测试集都没有，而测试会话把验收条件都归为「看界面」（或测试会话说可以验收了却没建测试集）
  function manualOnly(list, r) {
    if (!r || !list || list.error || r.state === 'dropped') return false;
    var sets = (list.sets || []).filter(function (s) { return !s.skipped; });
    var t = r.testing;
    if (sets.length || !t) return false;
    return !!t.all_ui || (t.rounds || []).some(function (x) { return x.verdict === 'ok'; });
  }
  var opText = function (c) { return c.op === 'delete' ? uiText('删掉这一条', "Remove this case") : uiText('改期望', "Change expected result"); };
  // 一条改动里要给你看的：原来的期望和改成什么（报错的写成「报错：…」）
  function changeSides(c) {
    var side = function (x) { if (!x) return uiText('（删掉这一条）', "(remove this case)"); return x.raises ? uiText('期望报错，含「', "Expected error containing “") + x.raises + '」' : json(x.expect); };
    return { old: side(c.old), now: c.op === 'delete' ? uiText('（删掉这一条）', "(remove this case)") : side(c.new), inputChanged: !!(c.new && c.old && JSON.stringify(c.new.input) !== JSON.stringify(c.old.input)) };
  }
  // 不同意时交回提出它的开发会话的话
  function rejectText(id, c) {
    return uiText('我不同意测试集 ', "I reject the change to test set ") + id + uiText(' 的改动 ', " (") + c.id + '（#' + c.case + '「' + (c.name || '') + '」，' + opText(c) + uiText('）：这是改坏了，期望保持原样。', "). The original result is still expected.")
      + uiText('请修代码让它按原来的期望通过，不要再提改这条数据；修好以后用 rf.py test run 重跑。', "Fix the code to meet the original expectation and rerun rf.py test run.");
  }

  // ---------- 查看器 ----------
  function caseStatus(c) { return c && c.result && STATUS[c.result.status] ? c.result.status : 'unrun'; }
  function caseNeeds(c) { var s = caseStatus(c); return s === 'fail' || s === 'broken' || !!c.unseen; }
  function viewerCounts(cases) {
    var t = { pass: 0, fail: 0, broken: 0, unrun: 0 };
    (cases || []).forEach(function (c) { t[caseStatus(c)]++; });
    t.total = (cases || []).length;
    t.need = (cases || []).filter(caseNeeds).length;
    t.unseen = (cases || []).filter(function (c) { return !!c.unseen; }).length;
    return t;
  }
  var RANK = { fail: 0, broken: 1, unrun: 2, pass: 3 };
  // 左边的列表：筛选（need 需要看的、fail 失败和跑不起来、all 全部）、搜索名称，按正常、边界、异常分组，组里失败的排前面
  function caseGroups(cases, f, q) {
    q = String(q || '').trim().toLowerCase();
    var show = function (c) {
      var s = caseStatus(c);
      var ok = f === 'all' || (f === 'need' && caseNeeds(c)) || (f === 'fail' && (s === 'fail' || s === 'broken'));
      return ok && (!q || String(c.name || '').toLowerCase().indexOf(q) >= 0 || ('#' + c.n) === q);
    };
    return CATS.concat(['']).map(function (cat) {
      var rows = (cases || []).filter(function (c) { return (CATS.indexOf(c.cat) >= 0 ? c.cat : '') === cat && show(c); });
      rows.sort(function (a, b) { return RANK[caseStatus(a)] - RANK[caseStatus(b)] || a.n - b.n; });
      return { cat: cat ? categoryName(cat) : uiText('还没分类', "Unclassified"), rows: rows };
    }).filter(function (g) { return g.rows.length; });
  }
  // 打开时默认的筛选和选中的那条：有需要看的就从需要看的开始，先选失败或跑不起来的那条
  function firstView(cases) {
    var need = (cases || []).filter(caseNeeds);
    var f = need.length ? 'need' : 'all';
    var rows = [];
    caseGroups(cases, f, '').forEach(function (g) { rows = rows.concat(g.rows); });
    var bad = rows.filter(function (c) { var s = caseStatus(c); return s === 'fail' || s === 'broken'; })[0];
    return { filter: f, n: bad ? bad.n : rows.length ? rows[0].n : null };
  }
  // 为什么失败：程序比对出来的差异，或者这一条抛的异常
  function whyFailed(c) {
    var r = c && c.result;
    if (!r) return '';
    if (r.status === 'broken') return r.error || uiText('测试代码没能运行。', "The test code couldn't run.");
    if (r.status !== 'fail') return '';
    return (r.diff && r.diff.length ? r.diff.map(humanDiff).join('\n') : '') || r.error || uiText('实际输出和期望输出不一样。', "Actual output differs from expected output.");
  }
  // 以前的结果里差异是「$.items[0].amount：期望 12，实际 13」这样的写法（现在由程序直接写成人话）：重跑之前先在这里翻译一下
  function pathWords(p) {
    var words = uiText('返回值', "Return value"), m, re = /\.([^.[\]]+)|\[(\d+)\]/g;
    while ((m = re.exec(p))) words += (/[A-Za-z0-9_-]$/.test(words) ? ' ' : '') + (m[2] != null ? uiText('的第 ', " at item ") + (Number(m[2]) + 1) + uiText(' 项', "") : uiText('的 ', " at ") + m[1]);
    return words;
  }
  function humanDiff(line) {
    var m = /^(\$[^：]*)：(.*)$/.exec(String(line || ''));
    if (!m) return line;
    var path = m[1], rest = m[2], parent = path.replace(/\.[^.[\]]+$/, ''), key = (/\.([^.[\]]+)$/.exec(path) || [])[1];
    var sp = function (w) { return /[A-Za-z0-9_-]$/.test(w) ? ' ' : ''; };
    if (rest === '期望有，实际没有' && key) return pathWords(parent) + sp(pathWords(parent)) + uiText('缺少字段 ', "Missing field ") + key;
    var extra = /^期望没有，实际有 (.*)$/.exec(rest);
    if (extra && key) return pathWords(parent) + sp(pathWords(parent)) + uiText('多出字段 ', "Unexpected field ") + key + uiText('（实际是 ', " (actual: ") + extra[1] + '）';
    if (/^期望 \d+ 项，实际 \d+ 项$/.test(rest)) return pathWords(path) + uiText('：数量不同，', ": Different sizes, ") + rest;
    return pathWords(path) + '：' + (/^期望 /.test(rest) ? uiText('值不同，', "Different value, ") : '') + rest;
  }
  // 这一条可以改的部分（输入、期望或期望报错）和保存时交给 rf.py test save 的样子
  function caseFields(c) {
    var out = { name: c.name, cat: c.cat || '正常', input: c.input };
    if (c.raises) out.raises = c.raises; else out.expect = c.expect;
    if (c.usecase) out.usecase = c.usecase; // 对接的测试用例（「需求编号#用例编号」）：整份保存时原样带回
    return out;
  }
  function payload(cases) { return { cases: (cases || []).map(function (c) { return Object.assign({ n: c.n }, caseFields(c)); }) }; }
  // 编辑框里的 JSON：读不懂时给出原因
  function parseJson(text, what) {
    try { return { value: JSON.parse(text) }; } catch (e) { return { error: what + uiText('不是合法的 JSON：', "Invalid JSON: ") + e.message }; }
  }

  // ---------- 确认完成前 ----------
  // ran：rf.py test run --req R --json；listed：rf.py test list --req R --json（运行以后读的）
  function doneCheck(ran, listed) {
    if (ran && ran.error) return { ok: false, error: uiText('没能运行测试集：', "Couldn't run test set: ") + ran.error, failing: [], pending: pendingOf(listed), totals: null };
    var failing = ((ran && ran.sets) || []).filter(function (s) { return s.result !== 'pass'; }).map(function (s) {
      return { id: s.id, title: s.title || '', fail: s.counts.fail || 0, broken: s.counts.broken || 0, result: s.result, origin: s.origin };
    });
    var pending = pendingOf(listed);
    return { ok: !failing.length && !pending.length, failing: failing, pending: pending, totals: (ran && ran.totals) || null, mustDecide: pending.length > 0 };
  }
  function pendingOf(listed) {
    var out = [];
    ((listed && listed.sets) || []).forEach(function (s) { (s.pending_changes || []).forEach(function (c) { if (!listed.scope || c.by === listed.scope) out.push(Object.assign({ set: s.id, setTitle: s.title }, c)); }); });
    return out;
  }
  function failingText(f) { return f.result === 'broken' ? uiText('跑不起来，', "Couldn't run. ") + (f.broken || uiText('全部', "All")) + uiText(' 条都没有执行', " cases were skipped") : f.fail + uiText(' 条失败', " failed") + (f.broken ? '，' + f.broken + uiText(' 条跑不起来', " couldn't run") : ''); }

  // ---------- 让 AI 改：交给谁 ----------
  // ctx：查看器是从哪里打开的（{ req } 需求面板；{} 测试页）。测试集是某个需求（修复）进行中新建的，只有它的会话能改，所以交给它；
  // 从进行中的需求打开的交给那个需求的开发会话；其余新开一个日常聊天（在项目目录里，按主线的数据）
  function aiTarget(set, ctx, reqs) {
    ctx = ctx || {};
    var owner = ctx.fix || ctx.req || set.in_progress || '';
    if (/^F-/.test(owner) && (ctx.fix === owner || set.in_progress === owner)) return { kind: 'fix', run: 'fix:' + owner, title: uiText('修复 ', "Fix session for ") + owner + uiText(' 的会话', ""), scope: { fix: owner } };
    var r = (reqs || []).filter(function (x) { return x.id === owner; })[0];
    if (r && r.state === 'open' && r.worktree) return { kind: 'req', run: r.id, title: '「' + r.title + uiText('」的开发会话', "” development session"), scope: { req: r.id } };
    if (set.in_progress && set.in_progress !== ctx.req) {
      var o = (reqs || []).filter(function (x) { return x.id === set.in_progress; })[0];
      if (o && o.state === 'open' && o.worktree) return { kind: 'req', run: o.id, title: '「' + o.title + uiText('」的开发会话', "” development session"), scope: { req: o.id } };
    }
    return { kind: 'chat', run: '', title: uiText('新的日常聊天', "New chat"), scope: {} };
  }

  return {
    CATS: CATS, categoryName: categoryName, STATUS: STATUS, FILTERS: FILTERS, AI_EXAMPLES: AI_EXAMPLES, esc: esc, json: json,
    counts: counts, isBroken: isBroken, segments: segments, barHtml: barHtml, pillsHtml: pillsHtml, resultText: resultText,
    needsLook: needsLook, pass: pass, filterCounts: filterCounts, groups: groups, reqLabel: reqLabel, lastRunAt: lastRunAt, fullLine: fullLine,
    panel: panel, changeSides: changeSides, opText: opText, rejectText: rejectText,
    caseStatus: caseStatus, caseNeeds: caseNeeds, viewerCounts: viewerCounts, caseGroups: caseGroups, firstView: firstView, whyFailed: whyFailed, humanDiff: humanDiff,
    caseFields: caseFields, payload: payload, parseJson: parseJson,
    doneCheck: doneCheck, manualOnly: manualOnly, failingText: failingText, aiTarget: aiTarget,
  };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = TestsModel;
