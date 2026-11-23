// 需求之间的冲突的 model 层：不碰 DOM，测试（tests/test_app_conflicts.cjs）直接在 Node 里调用。
// 数据来自主进程推来的 rf.py conflicts（app/src/main/conflicts.js）：
//   { base: 合并目标分支, items: [{ id, kind: req|fix, title, branch, committed: [文件], uncommitted: [文件], base_conflicts: [文件] | null }],
//     pairs: [{ a, b, files: [两边都改了的文件], conflicts: [两个分支合并会冲突的文件] }] }
// 只给提示，不改依赖、不改代码：怎么处理由用户决定（设前置需求、先做完一个，或者让开发会话现在就同步主分支）。
var ConflictsModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var files = function (list, n) {
    n = n || 3;
    return list.slice(0, n).join('、') + (list.length > n ? uiText(' 等 ', " and ") + list.length + uiText(' 个文件', " more files") : '');
  };
  function itemOf(data, id) {
    return ((data && data.items) || []).filter(function (i) { return i.id === id; })[0] || null;
  }
  function nameOf(data, id) {
    var i = itemOf(data, id);
    return i ? (i.kind === 'fix' ? uiText('「修复 ', "“Fix ") + i.id + '」' : '《' + i.title + '》') : id;
  }
  // 和这个需求有关的几条：和合并目标会冲突、和别的需求会冲突、和别的需求改到同一处（不冲突）
  function about(data, id) {
    var out = [], me = itemOf(data, id);
    if (!me) return out;
    if (me.base_conflicts && me.base_conflicts.length) {
      out.push({ cls: 'bad', other: null, files: me.base_conflicts,
        text: uiText('和 ', " and ") + (data.base || uiText('主分支', "the main branch")) + uiText(' 合并会冲突：', " will conflict when merged: ") + files(me.base_conflicts),
        hint: data.base + uiText(' 上已经有人改了同样的地方。让开发会话现在就把 ', " already changes the same lines. Ask the development session to merge ") + data.base + uiText(' 合并进来、解决冲突，比等到确认完成时再处理容易。', " now and resolve the conflicts before acceptance.") });
    }
    // 改到了「要改的功能点」以外的功能点的代码（知识保鲜，backend/relayforge/facts/fresh.py 的 outside_scope）：只提示
    (me.outside || []).forEach(function (o) {
      out.push({ cls: 'warn', other: null, files: o.files, scope: o.feature,
        text: uiText('改到了 ', 'Changes code of ') + o.feature + uiText(' 的代码：', ': ') + files(o.files),
        hint: uiText('它不在这个需求「要改的功能点」里。确实要改的话，把它加进要改的功能点，完成时它的描述会一起更新；不该改就让开发会话改回去。', 'It is not in this requirement’s list of features to change. If the change is intended, add it to the list so its description is updated on completion; otherwise ask the development session to revert it.') });
    });
    ((data && data.pairs) || []).forEach(function (p) {
      if (p.a !== id && p.b !== id) return;
      var other = p.a === id ? p.b : p.a;
      if (p.conflicts && p.conflicts.length) {
        out.push({ cls: 'bad', other: other, files: p.conflicts,
          text: uiText('和', " and ") + nameOf(data, other) + uiText('会冲突：', " will conflict: ") + files(p.conflicts),
          hint: uiText('两个分支改了同一处的不同内容，后合并的那个要解决冲突。可以让其中一个等另一个（设成前置需求），或者先把其中一个做完合并。', "Both branches change the same lines differently. The second merge will need conflict resolution. Set one requirement as a prerequisite, or finish and merge one first.") });
      } else {
        out.push({ cls: 'warn', other: other, files: p.files,
          text: uiText('和', " and ") + nameOf(data, other) + uiText('都在改：', " both touch: ") + files(p.files),
          hint: uiText('现在还能自动合并（或者还没提交，算不出来），继续改下去可能会撞上。', "These changes can currently merge automatically, or are still uncommitted. Further changes may conflict.") });
      }
    });
    return out;
  }
  // 看板卡片上：会冲突的放前面，最多两条
  function cardNotes(data, id) {
    return about(data, id).sort(function (x, y) { return (x.cls === 'bad' ? 0 : 1) - (y.cls === 'bad' ? 0 : 1); }).slice(0, 2)
      .map(function (n) { return { cls: n.cls, text: (n.cls === 'bad' ? '⚠ ' : '') + n.text, title: n.files.join('\n') }; });
  }
  // 需求面板上：每条一行，带全部文件和怎么办
  function panelLines(data, id) {
    return about(data, id).map(function (n) { return { cls: n.cls, text: n.text, files: n.files, hint: n.hint, other: n.other }; });
  }
  // 这个需求自己改了多少（面板标题旁的小字）
  function summary(data, id) {
    var me = itemOf(data, id);
    if (!me) return '';
    return uiText('改了 ', "Changed ") + me.committed.length + uiText(' 个文件', " files") + (me.uncommitted.length ? uiText('，另有 ', ", plus ") + me.uncommitted.length + uiText(' 个没提交', " uncommitted files") : '');
  }
  // 立项时按历史预测（rf.py conflicts --predict）：{ features, history, predicted: [{ path, count, from }], against: [{ id, title, state, features, files }] }
  // 面板上和确认立项的问话里用：可能撞上的每条一行，带理由和怎么办；最后一行是预计会动到的文件
  function predictLines(pred) {
    if (!pred) return [];
    var out = (pred.against || []).map(function (a) {
      var why = [];
      if (a.features && a.features.length) why.push(uiText('同样要改 ', "Also plans to change ") + a.features.join('、'));
      if (a.files && a.files.length) why.push(uiText('已经在改预计会动到的 ', "Already changing files expected to be touched: ") + files(a.files));
      return { cls: 'warn', text: uiText('可能撞上《', "May overlap with “") + a.title + '》（' + (a.state === 'draft' ? uiText('立项中', "Planning") : uiText('进行中', "In development")) + '）：' + why.join('；'),
        hint: uiText('可以把它设成这个需求的前置需求，或者在立项里把会撞上的部分拆出去；也可以不管，到时候再解决冲突。', "Set this as a prerequisite, split out the overlapping work during planning, or resolve any conflict at merge time."), other: a.id, files: a.files || [] };
    });
    return out;
  }
  function predictSummary(pred) {
    if (!pred) return '';
    if (!(pred.history || []).length) return uiText('没有改过同样功能点的历史需求，预测不了会动到哪些文件', "No past requirements changed the same features, so there is no file estimate.");
    var paths = (pred.predicted || []).map(function (p) { return p.path; });
    return uiText('按 ', "Based on ") + pred.history.length + uiText(' 个改过同样功能点的需求推测，', " requirements that changed the same features, ") + (paths.length ? uiText('可能会动到 ', "likely files: ") + files(paths, 4) : uiText('没有现在还在的文件', "none of those files still exist"));
  }
  return { about: about, cardNotes: cardNotes, panelLines: panelLines, summary: summary, nameOf: nameOf, predictLines: predictLines, predictSummary: predictSummary };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = ConflictsModel;
