// 测试用例（立项会话用业务的话写、挂在验收条件下面；测试会话对接成测试数据）：需求面板里按条显示、结果、待确认的用例改动、
// 确认立项和确认完成前的提示。数据来自 rf.py req list（需求的 testing：checks、cases、case_changes、gate）和 rf.py test list --req 的 usecases。
// 这里只有纯函数（界面和测试都用），不碰页面。普通脚本，也能被 node 用 require 读进来。
var CasesModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var STATUS = { pass: uiText('都通过', "All passed"), fail: uiText('有失败', "Has failures"), broken: uiText('跑不起来', "Couldn't run"), unrun: uiText('对接了还没跑', "Linked, not run"), unlinked: uiText('还没对接', "Unlinked") };
  var PILL = { pass: 'g', fail: 'r', broken: 'r', unrun: 'a', unlinked: '' };
  var MARK = { pass: '✓', fail: '✗', broken: '!', unrun: '?', unlinked: '○' };
  var KIND = { data: uiText('能用数据验证', "Runnable test"), ui: uiText('只能人工看', "Manual check") };
  var OPS = { edit: uiText('修改', "Edit"), delete: uiText('删除', "Delete"), add: uiText('新增', "Add"), classify: uiText('归类', "Classify") };
  var CATS = ['正常', '边界', '异常'];
  var SOURCE = { intake: uiText('立项写的', "From planning"), test: uiText('测试补充的', "Added during testing"), user: uiText('你加的', "Added by you") };

  // 每条验收条件和它下面的用例；有对接结果时（report = test list --req 的 usecases）带上结果和对接的数据
  function conditions(r, report) {
    var t = (r && r.testing) || {}, by = (report && report.by_case) || {};
    return (t.checks || []).map(function (cond) {
      var cases = (t.cases || []).filter(function (c) { return c.live && c.check === cond.key; }).map(function (c) {
        var got = by[c.n];
        return Object.assign({}, c, { status: got ? got.status : null, links: got ? got.links || [] : [] });
      });
      return { n: cond.n, text: cond.text, key: cond.key, kind: cond.kind || null, why: cond.why || '', cases: cases,
        uncovered: !cases.length && cond.kind !== 'ui' };
    });
  }
  // 一组用例的结果：各几条
  function counts(cases) {
    var out = { pass: 0, fail: 0, broken: 0, unrun: 0, unlinked: 0, total: 0, supplements: 0 };
    (cases || []).forEach(function (c) { out[c.status || 'unlinked'] += 1; out.total += 1; if (c.source === 'test') out.supplements += 1; });
    return out;
  }
  // 一条验收条件下面那一行的摘要：「2 条用例 · 1 都通过 · 1 有失败」；withResults 为假（立项中）只说几条
  function condLine(cond, withResults) {
    if (cond.kind === 'ui' && !cond.cases.length) return uiText('只能人工看', "Manual check") + (cond.why ? '：' + cond.why : '');
    if (!cond.cases.length) return uiText('还没有用例，也没标「只能人工看」', "No cases or manual classification");
    var c = counts(cond.cases);
    var parts = [cond.cases.length + uiText(' 条用例', " test cases")];
    if (withResults) ['fail', 'broken', 'unrun', 'unlinked', 'pass'].forEach(function (k) { if (c[k]) parts.push(c[k] + uiText(' 条', ' ') + STATUS[k]); });
    if (c.supplements) parts.push(uiText('其中 ', "Includes ") + c.supplements + uiText(' 条测试补充的', " added during testing"));
    return parts.join(' · ');
  }
  function caseText(c) { return uiText('前提：', "Given: ") + c.given + uiText('；操作：', ". When: ") + c.when + uiText('；预期：', ". Then: ") + c.then; }
  // 一条用例改动的两边，写成人看的话
  function sideText(op, v) {
    if (!v) return op === 'delete' ? uiText('（删掉）', "(removed)") : uiText('（没有）', "(none)");
    if (op === 'classify') return (KIND[v.kind] || v.kind) + (v.why ? '：' + v.why : '');
    return '「' + v.name + '」〔' + v.cat + '〕' + caseText(v);
  }
  function changeView(c) {
    return { k: c.k, target: c.target, op: c.op, opText: OPS[c.op] || c.op, by: c.by, why: c.why, state: c.state, at: c.at,
      old: c.op === 'add' ? uiText('（还没有这条）', "(no previous case)") : sideText(c.op, c.old), now: c.op === 'delete' ? uiText('（删掉这条用例）', "(delete this case)") : sideText(c.op, c.new), actor: c.actor };
  }
  function changes(r) { return ((r && r.testing && r.testing.case_changes) || []).map(changeView); }
  function pending(r) { return changes(r).filter(function (c) { return c.state === 'pending'; }); }
  // 确认立项的问话：没有用例、也没标「只能人工看」的几条，提示但不拦。startDialog 给应用里的确认框（标题和几段话），startAsk 是同样的话连成一段
  function startDialog(r, levelName) {
    var checks = (r.sections && r.sections['验收条件']) || [], gaps = (r.gate && r.gate.uncovered) || [];
    var cases = ((r.testing && r.testing.cases) || []).filter(function (c) { return c.live; }).length;
    var lines = [uiText('清晰度：', "Clarity: ") + levelName + uiText('。测试用例 ', ". Test cases: ") + cases + uiText(' 条。', "."), uiText('验收条件：\n', "Acceptance criteria:\n") + checks.map(function (c) { return '· ' + c; }).join('\n')];
    if (gaps.length) lines.push(uiText('注意：还有 ', "Note: ") + gaps.length + uiText(' 条验收条件没有测试用例、也没标「只能人工看」：\n', " criteria have no test case or manual classification:\n") + gaps.map(function (g) { return uiText('· 第 ', "· Criterion ") + g.n + uiText(' 条：', ": ") + g.text; }).join('\n')
      + uiText('\n可以先立项（不拦），开发和测试会照着现有的用例做；也可以回去让立项会话补上。', "\nYou can approve now or return to the planning session to add cases."));
    lines.push(uiText('确认后，立项会话会关掉，这个需求会开一个新的会话开始写代码。', "Approval closes planning and opens a new development session."));
    return { title: uiText('确认「', "Approve “") + r.title + uiText('」立项，开始开发？', "” and start development?"), lines: lines, warn: gaps.length > 0 };
  }
  function startAsk(r, levelName) { var d = startDialog(r, levelName); return d.title + '\n\n' + d.lines.join('\n\n'); }
  // 面板上「确认立项」下面的那句提示
  function gateNote(r) {
    if (!r.gate) return '';
    if (!r.gate.ok) return r.gate.missing;
    var gaps = r.gate.uncovered || [];
    return gaps.length ? uiText('还有 ', "There are ") + gaps.length + uiText(' 条验收条件没有测试用例、也没标「只能人工看」（第 ', " criteria with no test case or manual classification (") + gaps.map(function (g) { return g.n; }).join('、') + uiText(' 条）。可以先立项，不拦。', "). You can still approve the requirement.")
      : uiText('其他没聊完的可以先跳过。确认后关掉这个立项会话，开一个新的会话开始开发。', "You can defer the remaining questions. Approval closes planning and opens a new development session.");
  }
  // 用例编号写成测试数据里存的样子
  function ref(r, n) { return r.id + '#' + n; }
  // 编辑框交给 rf.py req case-save 的 JSON
  function savePayload(op, form) {
    var out = { op: op };
    ['case', 'check'].forEach(function (k) { if (form[k] != null && form[k] !== '') out[k] = Number(form[k]); });
    ['name', 'cat', 'given', 'when', 'then', 'kind', 'why'].forEach(function (k) { if (form[k] != null && String(form[k]).trim() !== '') out[k] = String(form[k]).trim(); });
    return out;
  }
  function checkForm(form) {
    var miss = [['name', uiText('名称', "Name")], ['given', uiText('前提', "Given")], ['when', uiText('操作', "When")], ['then', uiText('预期', "Then")]].filter(function (x) { return !String(form[x[0]] || '').trim(); });
    return miss.length ? uiText('还要写：', "Still needed: ") + miss.map(function (x) { return x[1]; }).join('、') + uiText('（用业务的话；没有特别的前提就写「无特别前提」）。', ". Use business language. If there are no special prerequisites, say so.") : '';
  }
  // 「不同意」以后交回提出它的会话的那句话（主进程也有一份同样的说法）
  function rejectText(c) {
    return uiText('【用例改动 ', "[Test case change ") + c.k + uiText(' 我没有同意】你提的「', " declined] ") + c.target + '」' + (OPS[c.op] || '') + uiText('（理由：', " (reason: ") + c.why + uiText('）我没有同意，用例保持原样。', ") was declined. Keep the original case.");
  }

  return { STATUS: STATUS, PILL: PILL, MARK: MARK, KIND: KIND, OPS: OPS, CATS: CATS, SOURCE: SOURCE, conditions: conditions, counts: counts, condLine: condLine,
    caseText: caseText, changeView: changeView, changes: changes, pending: pending, startAsk: startAsk, startDialog: startDialog, gateNote: gateNote, ref: ref, savePayload: savePayload,
    checkForm: checkForm, rejectText: rejectText };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = CasesModel;
