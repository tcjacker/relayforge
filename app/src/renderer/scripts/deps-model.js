// 前置需求的 model 层：不碰 DOM，测试（tests/test_app_depends.cjs）直接在 Node 里调用。
// 数据来自 rf.py req list：r.depends_on（前置需求：id、title、state、stage、done、steps、finished）、r.waiting（还没完成的前置需求编号）、
// r.dropped_deps（已取消的）、r.dependents（在等它的需求：id、title、state、ready）、r.moves（不能去哪一栏、为什么，程序算好的）、r.start_sync。
var DepsModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var ORDER_WORDS = { after: uiText('等当前需求完成后再开发', "Start development after the current requirement is complete"), before: uiText('当前需求等它', "Prerequisite of the current requirement"), none: uiText('互不依赖', "Independent") };
  var STAGE_WORDS = { talk: uiText('立项中', "Planning"), build: uiText('进行中', "In development"), test: uiText('测试中', "Testing"), check: uiText('待验收', "Ready for acceptance"), done: uiText('已完成', "Completed"), dropped: uiText('已取消', "Cancelled") };
  var live = function (r) { return r && (r.state === 'draft' || r.state === 'open'); };
  var deps = function (r) { return (r && r.depends_on) || []; };
  var names = function (list) { return list.map(function (d) { return '《' + d.title + '》'; }).join('、'); };

  // 还没完成的前置需求（含已取消的）
  function waitingOn(r) {
    var ids = (r && r.waiting) || [];
    return deps(r).filter(function (d) { return ids.indexOf(d.id) >= 0; });
  }
  // 前置需求都完成了、还在立项中：可以开始了（只是提醒，立项还是你点）
  function ready(r) { return !!(r && r.state === 'draft' && deps(r).length && !waitingOn(r).length); }

  // 看板卡片上的几行：{ cls, text, title }。cls 对应样式：dep-wait 在等、dep-bad 前置已取消、dep-ready 可以开始了、dep-hold 有需求在等它
  function cardNotes(r) {
    if (!live(r)) return [];
    var out = [], waiting = waitingOn(r), dropped = waiting.filter(function (d) { return d.state === 'dropped'; });
    if (dropped.length) out.push({ cls: 'dep-bad', text: uiText('✕ 前置需求', "✕ Prerequisite ") + names(dropped) + uiText('已取消', " was cancelled"), title: uiText('去掉这条依赖，或者取消这个需求', "Remove this dependency or cancel this requirement") });
    else if (waiting.length === 1) out.push({ cls: 'dep-wait', text: uiText('⏳ 等', "⏳ Waiting for ") + names(waiting), title: names(waiting) + uiText('确认完成后才能', " to complete before ") + (r.state === 'draft' ? uiText('开始开发', "starting development") : uiText('确认完成', "marking complete")) });
    else if (waiting.length) out.push({ cls: 'dep-wait', text: uiText('⏳ 等 ', "⏳ Waiting for ") + waiting.length + uiText(' 个需求', " requirements"), title: uiText('要等', "Waiting for ") + names(waiting) + uiText('确认完成', " to complete") });
    else if (ready(r)) out.push({ cls: 'dep-ready', text: uiText('● 可以开始了', "● Ready to start"), title: uiText('前置需求', " Prerequisites: ") + names(deps(r)) + uiText('都已确认完成', " · All completed") });
    var holding = ((r && r.dependents) || []).filter(function (x) { return live(x); });
    if (holding.length) out.push({ cls: 'dep-hold', text: uiText('⇢ 有 ', "⇢ ") + holding.length + uiText(' 个需求在等它', " dependent requirements"), title: names(holding) });
    return out;
  }
  // 左侧列表第二行开头的说法；没有依赖时空字符串
  function listNote(r) {
    if (!live(r)) return '';
    var waiting = waitingOn(r);
    if (waiting.some(function (d) { return d.state === 'dropped'; })) return uiText('前置需求已取消', "Prerequisite cancelled");
    if (waiting.length === 1) return uiText('等', "Waiting for ") + names(waiting);
    if (waiting.length) return uiText('等 ', "Waiting for ") + waiting.length + uiText(' 个需求', " requirements");
    return ready(r) ? uiText('可以开始了', "Ready to start") : '';
  }
  // 鼠标停在卡片上：高亮它的前置需求（pre）和在等它的需求（post）
  function related(r) {
    return { pre: deps(r).map(function (d) { return d.id; }), post: ((r && r.dependents) || []).map(function (d) { return d.id; }) };
  }

  // 「＋ 添加前置需求」的列表：每个别的需求能不能选、不能选为什么。能选的排在前面
  function reaches(byId, from, target, seen) {
    seen = seen || {};
    return deps(byId[from]).some(function (d) {
      if (d.id === target) return true;
      if (seen[d.id]) return false;
      seen[d.id] = true;
      return reaches(byId, d.id, target, seen);
    });
  }
  function pickerOptions(r, reqs, query) {
    var byId = {}; (reqs || []).forEach(function (x) { byId[x.id] = x; });
    var mine = deps(r).map(function (d) { return d.id; }), q = String(query || '').trim().toLowerCase();
    var out = (reqs || []).filter(function (x) { return !q || String(x.title).toLowerCase().indexOf(q) >= 0 || x.id.toLowerCase().indexOf(q) >= 0; }).map(function (x) {
      var why = x.id === r.id ? uiText('就是这个需求自己', "This is the current requirement") : x.state === 'done' ? uiText('已完成：不用等', "Already completed") : x.state === 'dropped' ? uiText('已取消：等不到它完成', "Cancelled and cannot complete")
        : mine.indexOf(x.id) >= 0 ? uiText('已经是前置需求', "Already a prerequisite") : reaches(byId, x.id, r.id) ? uiText('它在等这个需求，选了会互相等待', "This requirement depends on the current one. Selecting it would create a cycle.") : '';
      var cycle = !!why && why.indexOf(uiText('互相等待', "Circular dependency")) >= 0; // 出路：不设这条，把要用到它的部分拆出去（splitAway）
      return { id: x.id, title: x.title, stage: STAGE_WORDS[x.stage] || '', why: why, cycle: cycle };
    });
    return out.filter(function (o) { return !o.why; }).concat(out.filter(function (o) { return o.why; }));
  }

  // 「把要等它的部分拆出去」交给这个需求的会话的话（会话照着提拆分建议，你在右侧同意）。只交代现状和要达到的目标，
  // 怎么分、和这个需求谁先谁后、还要等谁、要用到什么都由 AI 判断，拆不开就直说。
  // 已经是前置需求的（cycle 为假）：目标是这个需求不再等它，拆出去的那一项必须用 takes 接过这条依赖；
  // 设了会互相等待的（cycle 为真）：不设这条，拆出去的那一项必须用 waits 等它
  function splitAway(d, cycle) {
    var name = '《' + d.title + '》';
    var head = cycle
      ? uiText('我想让这个需求等', "I want this requirement to wait for ") + name + uiText('，但', ", but ") + name + uiText('已经在等这个需求（直接或间接），设了会互相等待。请看这个需求里哪些部分要用到', " already depends on it directly or indirectly. Identify the parts of this requirement that need ") + name +
        uiText('，把这些部分拆成新需求，由它来等', " and split them into a new requirement that waits for ") + name + uiText('：拆出去的那一项要在 waits 里写 ["', ". Set waits to [\"") + d.id + uiText('"]，这个需求不设这条前置需求。', "\"] on the split item. Leave this dependency off the current requirement.")
      : name + uiText('还没完成，这个需求在等它', " is incomplete, and the current requirement depends on it") + (d.need ? uiText('（要用到它的', " (it needs ") + d.need + '）' : '') + uiText('。我想让这个需求能做的部分先做：请看哪些部分真的要用到', ". Start the independent work first. Identify the parts that need ") + name +
        uiText('，把这些部分拆成新需求，由它来等', " and split them into a new requirement that waits for ") + name + uiText('，这个需求就不再等它。要做到这一点，拆出去的那一项要在 takes 里写 ["', ". Set takes to [\"") + d.id + uiText('"]，不写的话这个需求还是要等。', "\"] on the split item so that the current requirement no longer waits.");
    return head + uiText('其余的先后（拆出去的和这个需求谁先谁后、还要不要等别的需求）由你判断，每条先后都在 needs 里写清要用到对方的什么，', "Decide the rest of the ordering. For each dependency, describe what it needs from the other requirement in needs. ") +
      uiText('并在正文画出先后图、说明理由。如果整个需求都离不开', "Draw a dependency diagram and explain your reasoning. If the whole requirement depends on ") + name + uiText('、拆不开，直接告诉我，不要硬拆。', " and cannot be split, say so.") +
      uiText('先说明打算怎么分、留下的部分还能不能单独验收，再附上拆分建议，等我在右侧同意；验收条件怎么挪，同意以后再说。', "Explain the proposed split and whether the remaining work can be accepted independently. Attach the proposal for my approval in the side panel. Review how to move acceptance criteria after approval.");
  }

  // 立项按钮下面的说明：在等前置需求时用程序算好的原因（r.moves），否则空字符串（由验收条件那边的说明管）
  function startBlock(r) { return waitingOn(r).length ? (r.moves && r.moves.build) || '' : ''; }
  // 「我试过了，确认完成」被前置需求拦住的原因
  function doneBlock(r) { return r && r.state === 'open' && waitingOn(r).length ? (r.moves && r.moves.done) || '' : ''; }
  // 立项时把主线同步进分支的结果：{ cls, text }，没有同步过时 null
  function syncNote(r) {
    var s = r && r.start_sync;
    if (!s) return null;
    var done = deps(r).filter(function (d) { return d.state === 'done'; });
    if (!s.ok) return { cls: 'dep-wait', text: uiText('开始开发时把 ', "Syncing ") + s.base + uiText(' 同步进这个需求的分支有冲突（', " into this requirement at the start of development caused a conflict (") + s.files.join('、') + uiText('），已撤回，交给开发会话处理。', "). The sync was rolled back and sent to the development session.") };
    return { cls: 'dep-ok', text: uiText('✓ 开始开发时已把 ', "✓ Synced the latest ") + s.base + uiText(' 的最新内容同步进这个需求的分支', " into this requirement when development started") + (done.length ? uiText('，包含', ", including ") + names(done) : '') + '（' + s.commits + uiText(' 个提交）。', " commits).") };
  }
  // 前置需求一行右边的状态：已完成 / 进行中 2/4 / 已取消……
  function depState(d) {
    var word = STAGE_WORDS[d.stage] || STAGE_WORDS[d.state] || '';
    return d.state === 'open' && d.steps ? word + ' ' + d.done + '/' + d.steps : word;
  }
  // 左侧列表的文件夹：通过前置需求连在一起（直接或间接）的需求放进同一个文件夹，没有依赖的不进文件夹。
  // 每个文件夹 { key, title, ids }：ids 按先后排（前置需求在前，同一层按创建时间），title 是大家最终都在等的那个需求
  // （组里没有前置需求的那个；有好几个时取最早创建的），key 用它的编号，展开收起按它记。
  function groups(reqs) {
    var byId = {}, links = {};
    (reqs || []).forEach(function (r) { byId[r.id] = r; links[r.id] = []; });
    (reqs || []).forEach(function (r) {
      deps(r).forEach(function (d) { if (byId[d.id]) { links[r.id].push(d.id); links[d.id].push(r.id); } });
    });
    var seen = {}, out = [];
    var older = function (a, b) { return String(byId[a].created).localeCompare(String(byId[b].created)) || a.localeCompare(b); };
    (reqs || []).forEach(function (r) {
      if (seen[r.id] || !links[r.id].length) return;
      var part = [], stack = [r.id];
      seen[r.id] = true;
      while (stack.length) {
        var id = stack.pop(); part.push(id);
        links[id].forEach(function (x) { if (!seen[x]) { seen[x] = true; stack.push(x); } });
      }
      var inside = {}; part.forEach(function (id) { inside[id] = true; });
      var needs = {}; part.forEach(function (id) { needs[id] = deps(byId[id]).filter(function (d) { return inside[d.id]; }).map(function (d) { return d.id; }); });
      var ordered = [], placed = {}, left = part.slice().sort(older);
      while (left.length) { // 先后：前置需求都排过了的才排；万一有环（程序不允许，旧数据可能有），剩下的按创建时间放后面
        var next = left.filter(function (id) { return needs[id].every(function (d) { return placed[d]; }); });
        if (!next.length) next = left.slice();
        next.forEach(function (id) { placed[id] = true; ordered.push(id); });
        left = left.filter(function (id) { return !placed[id]; });
      }
      var roots = part.filter(function (id) { return !needs[id].length; }).sort(older);
      var root = roots[0] || ordered[0];
      out.push({ key: root, title: byId[root].title, ids: ordered });
    });
    return out;
  }
  return { ORDER_WORDS: ORDER_WORDS, groups: groups, waitingOn: waitingOn, ready: ready, cardNotes: cardNotes, listNote: listNote, related: related,
    pickerOptions: pickerOptions, splitAway: splitAway, startBlock: startBlock, doneBlock: doneBlock, syncNote: syncNote, depState: depState };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = DepsModel;
