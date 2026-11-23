// Model shared by the branch picker and its checks.
var BranchModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  function matches(branches, query) {
    var q = String(query || '').trim().toLowerCase();
    return (branches || []).filter(function (b) { return b.name.toLowerCase().includes(q); });
  }

  function rows(list, query) { return matches(list && list.branches, query); }
  function remotes(list, query) { return matches(list && list.remotes, query); }
  function current(list) { return ((list && list.branches) || []).find(function (b) { return b.current; }); }
  function countText(b) { return b && b.pending > 0 ? '（' + b.pending + uiText(' 个待发布）', " to publish)") : ''; }

  function footer(list) {
    if (!list || list.error) return '';
    if (list.detached) return uiText('项目目录现在不在任何分支上', "The project checkout is in detached HEAD state");
    var now = current(list) || list;
    if (!now.upstream) return uiText('当前分支未关联远端分支', "The current branch has no upstream");
    return uiText('待发布到远端：', "Ready to publish: ") + (now.pending || 0) + uiText(' 个需求', " requirements");
  }

  function nameTaken(list, name) {
    return list.branches.some(function (b) {
      return b.name === name || b.name.startsWith(name + '/') || name.startsWith(b.name + '/');
    });
  }

  function plan(list, target, typedName) {
    if (!target) return null;
    if (!target.short_name) return { local: target.name, blocked: target.blocked };
    if (target.tracking) {
      var local = list.branches.find(function (b) { return b.name === target.tracking; });
      return {
        local: target.tracking,
        blocked: local && local.blocked,
        text: (target.tracking === list.current ? uiText('当前分支 ', "Current branch: ") : uiText('切换到本地分支 ', "Switch to local branch ")) + target.tracking + uiText('，跟踪 ', ", tracking ") + target.name + '。'
      };
    }
    var needsName = target.name_taken;
    var name = needsName ? String(typedName || '').trim() : target.short_name;
    var valid = name && !nameTaken(list, name);
    return {
      local: valid ? name : null,
      creating: true,
      needsName: needsName,
      text: valid ? uiText('创建本地分支 ', "Create local branch ") + name + uiText('，跟踪 ', ", tracking ") + target.name + '。'
        : uiText('本地名称 ', "The local name ") + target.short_name + uiText(' 已占用。请为跟踪 ', " is taken. Enter a different name for the branch tracking ") + target.name + uiText(' 的新分支输入可用名称。', ".")
    };
  }

  function decide(list, target, typedName) {
    var action = plan(list, target, typedName);
    if (!action || !action.local || action.local === list.current) return { kind: 'none' };
    if (action.blocked) return { kind: 'blocked', text: action.blocked };
    if (list.busy) return { kind: 'blocked', text: uiText('项目目录里有一次', "The project checkout has a ") + (list.busy === 'rebase' ? uiText('变基', "rebase") : list.busy === 'merge' ? uiText('合并', "merge") : uiText('拣选或回退', "cherry-pick or revert")) + uiText('还没做完，先把它做完再切换。', " in progress. Finish it before switching.") };
    var now = current(list) || list;
    var request = { name: target.name, remote: !!target.short_name, current: list.current, pending: now.pending || 0 };
    if (action.creating) request.localName = action.local;
    if (target.tracking) request.tracking = target.tracking;
    if (!request.pending) return { kind: 'go', request: request };
    var destination = action.creating ? uiText('创建本地分支 ', "create a local branch ") + action.local + uiText('、跟踪 ', " tracking ") + target.name + uiText(' 并切换过去', " and switch to it")
      : uiText('切换到 ', "switch to ") + action.local + (target.tracking ? uiText('（跟踪 ', " (tracking ") + target.name + '）' : '');
    return {
      kind: 'confirm',
      creating: action.creating,
      request: request,
      text: uiText('当前分支 ', "Current branch ") + list.current + uiText(' 还有 ', " has ") + request.pending + uiText(' 个需求未发布到 ', " requirements to publish to ") + now.upstream + uiText('。确定', ". Do you want to ") + destination + uiText('吗？', "?")
    };
  }

  function failure(out) {
    return (out && out.error || uiText('切换没有成功。', "Couldn't switch branches.")) + (out && out.detail ? uiText('（git 说：', " (Git: ") + out.detail + '）' : '');
  }

  return { rows: rows, remotes: remotes, countText: countText, footer: footer, plan: plan, decide: decide, failure: failure };
})();
if (typeof module !== 'undefined') module.exports = BranchModel;
