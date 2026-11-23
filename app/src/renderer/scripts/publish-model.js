// 发布：顶栏的提示文字、发布对话框要显示什么，都由 rf.py publish status --json 的结果算出来。
// 这里只有纯函数，不碰页面：界面（publish.js）和测试都用它。普通脚本，也能被 node 用 require 读进来。
var PublishModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var DEPLOY_NOTE = uiText('推送出去以后，有的服务会自动开始部署。看清楚下面的清单再点发布。', "Some services deploy automatically after a push. Review the list before publishing.");

  // 顶栏的小提示：{ text, kind: count 有数 | hint 只是提示 | none 不显示, title }
  function indicator(st) {
    if (!st || st.error) return { text: '', kind: 'none', title: '' };
    if (st.detached) return { text: uiText('不在任何分支上', "Detached HEAD"), kind: 'hint', title: uiText('项目目录现在不在任何分支上（detached HEAD）。点开看看原因。', "The project is in detached HEAD state. Open for details.") };
    if (st.state === 'no-remote' || !(st.remotes || []).length) return { text: uiText('还没有远端', "No remote"), kind: 'hint', title: uiText('这个项目还没有远端，没有地方可以发布。点开看看原因。', "This project has no publishing remote. Open for details.") };
    if (st.ahead > 0 && st.upstream) {
      var un = st.unpublished || {};
      var text = uiText('待发布到远端：', "Ready to publish: ") + (un.requirements || []).length + uiText(' 个需求', " requirements");
      if (un.others_total) text += ' · ' + un.others_total + uiText(' 个其他提交', " other commits");
      return { text: text, kind: 'count', title: uiText('当前分支有 ', "The current branch has ") + st.ahead + uiText(' 个提交尚未推到 ', " commits to push to ") + st.upstream.ref + uiText('。点开看清单。', ". Open to review them.") + (st.behind > 0 ? uiText('远端也有 ', "The remote also has ") + st.behind + uiText(' 个新提交。', " new commits.") : '') };
    }
    if (!st.upstream) return { text: uiText('还没设置上游分支', "No upstream branch"), kind: 'hint', title: uiText('当前分支还没有设置上游。点开看看发布时会推到哪里。', "This branch has no upstream. Open to see the push destination.") };
    if (st.behind > 0) return { text: uiText('远端有 ', "Remote has ") + st.behind + uiText(' 个新提交', " new commits"), kind: 'hint', title: uiText('到上次检查远端为止，远端比本地多 ', "At the last check, the remote was ahead by ") + st.behind + uiText(' 个提交。点开可以先拉取。', " commits. Open to pull them first.") };
    if (st.target && st.target.creates) return { text: uiText('远端分支不存在', "Remote branch missing"), kind: 'hint', title: uiText('上次检查时远端没有 ', "The remote did not have ") + st.upstream.ref + uiText('。点开查看详情。', " at the last check. Open for details.") };
    if (st.state === 'synced') return { text: uiText('已同步到远端', "Up to date with remote"), kind: 'hint', title: uiText('项目主目录：', "Main checkout: ") + st.branch + ' → ' + st.upstream.ref + uiText('。点开查看详情。', ". Open for details.") };
    return { text: '', kind: 'none', title: '' };
  }

  function pendingText(st) {
    return st && st.pending_requirements > 0 ? uiText('待合入本地：', "Pending local merge: ") + st.pending_requirements + uiText(' 个需求', " requirements") : '';
  }

  var plural = function (n, unit) { return n + ' ' + unit; };

  // 发布对话框：{ target, deploy, create, ack, remotes, requirements, issues, facts, others, warnings, blockers, empty, can: { publish, pull, check }, primary }
  // chosenRemote：没有上游而有好几个远端时，用户选的那个；acked：用户勾了「我知道会新建远端分支」
  // extra.pullDiverged：刚才点「先拉取」，因为两边分叉做不了快进
  function dialog(st, chosenRemote, acked, extra) {
    if (!st || st.error) return { error: (st && st.error) || uiText('读不到发布的状态。', "Couldn't read publish status."), can: { publish: false, pull: false, check: false }, blockers: [], warnings: [], requirements: [], issues: [], others: [], remotes: null };
    var un = st.unpublished || { requirements: [], issues: [], facts: 0, others: [], others_total: 0, total: 0 };
    var target = st.target;
    var needsPick = !!st.needs_remote && !chosenRemote;
    var shown = target ? { remote: target.remote, branch: target.branch } : (chosenRemote && st.branch ? { remote: chosenRemote, branch: st.branch } : null);
    var creates = !!(target && target.creates);
    var sets = !!(target && target.sets_upstream);
    var blockers = (st.blockers || []).filter(function (b) { return !(b.code === 'pick-remote' && chosenRemote); });
    var m = {
      target: shown && st.branch ? st.branch + ' → ' + shown.remote + '/' + shown.branch : '',
      deploy: DEPLOY_NOTE,
      create: creates ? uiText('远端 ', "The remote ") + shown.remote + uiText(' 上还没有 ', " does not have a branch named ") + shown.branch + uiText(' 分支。发布会新建它，并把它设为当前分支的上游。', ". Publishing creates it and sets it as the upstream.")
        : sets ? uiText('当前分支还没有设置上游。发布会推送到 ', "The current branch has no upstream. Publishing will push to ") + shown.remote + '/' + shown.branch + uiText('，并把它设为上游。', " and set it as the upstream.") : '',
      ack: sets ? uiText('我知道会推送到 ', "I agree to push to ") + shown.remote + '/' + shown.branch + (creates ? uiText('，并在远端新建这个分支', " and create this branch on the remote") : '') : '',
      remotes: st.needs_remote ? (st.remotes || []).map(function (r) { return { name: r.name, url: r.url }; }) : null,
      requirements: un.requirements || [], issues: un.issues || [],
      facts: un.facts || 0, factsText: un.facts ? uiText('程序自己提交的事实记录 ', "Automated fact commits: ") + plural(un.facts, uiText('个', "")) + uiText('（需求、问题、会话进展等，不含代码）', " (requirements, issues, session progress, and other project records)") : '',
      others: un.others || [], othersTotal: un.others_total || 0,
      othersText: un.others_total ? uiText('其他提交 ', "Other commits: ") + plural(un.others_total, uiText('个', "")) + (un.others_total > (un.others || []).length ? uiText('（只列出前 ', " (first ") + un.others.length + uiText(' 个）', " shown)") : '') : '',
      recordText: uiText('推送前，程序会先把攒着的事实提交，再写一份发布记录一起推送。', "Before pushing, RelayForge commits pending facts and writes a publication record."),
      warnings: st.warnings || [], blockers: blockers,
      empty: st.state === 'synced' && !!target && !target.creates && !sets ? uiText('没有需要发布的提交。', "No commits to publish.") : '',
      fetchError: st.fetch && !st.fetch.ok ? st.fetch : null,
      lastFetch: st.last_fetch || '',
    };
    var hardBlock = blockers.length > 0;
    if (st.busy) m.blockers = blockers.filter(function (b) { return b.code !== 'busy'; }); // 下面的 merging 说得更具体，不重复
    var ready = !!shown && !hardBlock && (st.ahead > 0 || sets) && !needsPick && (!sets || !!acked);
    m.can = { publish: ready, pull: !!st.can_pull, check: !!shown && !st.detached };
    m.primary = creates ? uiText('新建远端分支并发布', "Create remote branch and publish") : uiText('发布', "Publish");
    m.pull = st.behind > 0 && !st.ahead ? uiText('先拉取（', "Pull first (") + st.behind + uiText(' 个新提交）', " new commits)") : '';
    m.diverged = st.behind > 0 && st.ahead > 0;
    // 分叉了（或拉取因为分叉做不了快进）：可以让 AI 在新聊天里合并。合并已经开始了没做完时不再开新的：说明情况，知道是哪个聊天就能回去
    var unfinished = !!st.busy;
    m.aiMerge = !unfinished && !!st.upstream && !st.detached && (m.diverged || !!(extra && extra.pullDiverged)) ? uiText('让 AI 来合并', "Ask AI to merge") : '';
    m.divergedText = m.aiMerge ? uiText('两边都有对方没有的提交。程序不会自己合并；点「让 AI 来合并」，会新开一个聊天，请 AI 把远端的合并进来、解决冲突并提交（它不会推送）。弄好以后回到这里，点「检查远端」，再发布。', "Both branches have unique commits. Select Ask AI to merge to start a chat that merges remote changes, resolves conflicts, and commits locally. Then check the remote again and publish.") : '';
    m.merging = unfinished ? {
      kind: st.busy,
      text: st.busy === 'merge' ? uiText('项目目录里有一次合并还没做完（可能停在冲突上）。让做这件事的聊天做完并提交，之后回到这里点「检查远端」。', "A local merge is in progress. Finish it in the session that started it, then return here and check the remote.")
        : uiText('项目目录里有一次', "A local ") + (st.busy === 'rebase' ? uiText('变基', "rebase") : uiText('拣选或回退', "cherry-pick or revert")) + uiText('还没做完。先把它做完，再回到这里点「检查远端」。', " is in progress. Finish it before checking the remote again."),
      back: st.merge_chat && st.merge_chat.id ? uiText('回到那个聊天', "Return to that chat") : '', chat: st.merge_chat && st.merge_chat.id ? st.merge_chat.id : '',
    } : null;
    return m;
  }

  // 防止连点：一次只做一件事。run(fn) 在忙的时候直接回答「正在……」，不再开始第二次。
  function gate() {
    var working = null;
    return {
      working: function () { return working; },
      run: function (name, fn) {
        if (working) return Promise.resolve({ error: uiText('正在', "Running ") + working + uiText('，等它做完再点。', ". Wait until it finishes."), busy: true });
        working = name;
        return Promise.resolve().then(fn).then(function (v) { working = null; return v; }, function (e) { working = null; throw e; });
      },
    };
  }

  // 发布成功后的一句话
  function doneText(made) {
    var parts = [];
    if ((made.requirements || []).length) parts.push(uiText('需求 ', "Requirements: ") + made.requirements.length + uiText(' 个', ""));
    if ((made.issues || []).length) parts.push(uiText('问题 ', "Issues: ") + made.issues.length + uiText(' 个', ""));
    return uiText('已发布 ', "Published ") + made.branch + ' → ' + made.remote + '/' + made.target + '（' + made.commits + uiText(' 个提交', " commits") + (parts.length ? uiText('，带着', " · ") + parts.join('、') : '') + '）。';
  }

  return { indicator: indicator, pendingText: pendingText, dialog: dialog, gate: gate, doneText: doneText, DEPLOY_NOTE: DEPLOY_NOTE };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = PublishModel;
