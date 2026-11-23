// 合并：点了「确认完成」以后，需求可能马上合并好了，也可能在排队、或者 AI 正在它的开发会话里先把目标分支合并进来。
// 队伍由主进程 merge.js 管，这里只显示，并在冲突交给 AI 时把这个需求的会话打开、把话替你说进去。

// 合并好了：会话和工作目录已经收掉，界面上的终端也收掉
function merged(made) {
  var id = made.id, known = byId(id);
  if (known && known.state === 'done' && !merging[id] && !terms[id]) return; // 按钮的回答和主进程的通知都会走到这里，只处理一次
  delete merging[id];
  if (terms[id]) { terms[id].term.dispose(); terms[id].box.remove(); delete terms[id]; }
  delete states[id];
  if (made.state) reqs = reqs.map(function (x) { return x.id === id ? made : x; });
  if (current === id) select(made.state && visibleReq(made) ? id : null); else list();
  panel();
  if (made.title) say('「' + (made.title.length > 16 ? made.title.slice(0, 16) + '…' : made.title) + uiText('」已经合并，完成了。', "” was merged and completed."));
}

// 「确认完成」（按钮或右击菜单）的结果：合并好了、排上队了、或者有冲突交给了 AI
function afterDone(id, result) {
  if (!result.merging) return merged(result);
  merging[id] = Object.assign({ ahead: 0, base: '', files: [], note: '' }, merging[id], result.merging);
  list(); panel();
  var m = merging[id];
  if (m.phase === 'waiting') say(uiText('已排队：', "Queued: ") + (result.merging.holder ? result.merging.holder + uiText(' 正在合并，', " is merging. ") : '') + uiText('轮到它会自动合并。', " will merge when its turn comes."));
}

function wireMerge() {
  window.shell.onMerge(function (items) {
    merging = {};
    items.forEach(function (x) { merging[x.id] = x; });
    list(); panel();
  });
  window.shell.onMergeFinished(merged);
  // 要先在开发会话里合并：主进程已经把话交给 AI（会话开着经收件箱，没开着在后台跑一轮），这里只提示一下
  window.shell.onMergeResolve(function (p) {
    var r = byId(p.id);
    say((r ? '「' + (r.title.length > 16 ? r.title.slice(0, 16) + '…' : r.title) + '」' : p.id) + uiText('：先在它的开发会话里合并目标分支，已经交给 AI', ": The development session will merge the target branch first. AI is working ")
      + (p.headless ? uiText('在后台做', "in the background") : '') + uiText('，弄好会自动合并回去。', ", and RelayForge will merge when it finishes."));
  });
  $('merge-retry').addEventListener('click', function () {
    var id = current;
    $('merge-retry').disabled = true;
    retryMerge(id, uiText('开发会话', "Development session")).then(function (result) {
      $('merge-retry').disabled = false;
      if (result && result.error) { $('accept-error').textContent = result.error; $('accept-error').hidden = false; return; }
      if (result && !result.merging && result.id) merged(result);
    });
  });
  $('merge-skip').addEventListener('click', function () {
    var id = current, r = byId(id), m = merging[id] || {};
    var bad = m.gate && m.gate.failing ? m.gate.failing.map(function (f) { return f.id + ' ' + f.title; }).join('\n') : '';
    if (!window.confirm(uiText('跳过合并前的检查，直接合并「', "Skip the pre-merge check and merge “") + (r ? r.title : id) + uiText('」？\n\n合并后的代码上这些测试集没过：\n', "”?\n\nThese test sets failed on the merged code:\n") + bad
      + uiText('\n\n完成记录里会写明你跳过了检查。', "\n\nThe completion record will say the check was skipped."))) return;
    $('merge-skip').disabled = true;
    window.shell.retryMerge(id, true, true).then(function (result) {
      $('merge-skip').disabled = false;
      if (result && result.error) { $('accept-error').textContent = result.error; $('accept-error').hidden = false; return; }
      if (result && !result.merging && result.id) merged(result);
    });
  });
  $('merge-abort').addEventListener('click', function () {
    var id = current, r = byId(id);
    if (!window.confirm(uiText('中止「', "Abort the merge for “") + (r ? r.title : id) + uiText('」的合并？\n\n它退出合并队伍，回到待验收；分支上 AI 已经做的改动不动。之后可以再点「确认完成」。', "”?\n\nThe requirement leaves the queue and returns to acceptance. Changes made by AI stay on the branch. You can mark it complete again later."))) return;
    window.shell.abortMerge(id);
  });
  window.shell.listMerge();
}
