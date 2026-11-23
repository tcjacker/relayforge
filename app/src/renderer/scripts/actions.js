// 确认立项、验收：右边面板里的几个按钮。
// 绑定事件、开始加载：由 main.js 按顺序调用
function wireActions() {
  // 确认立项：只有你能过这道门；过了以后关掉立项会话，同一个需求开出新的开发会话
  $('start').addEventListener('click', function () {
    var id = current, r = byId(id);
    if (!r || !r.gate.ok) return;
    // 还有验收条件没有用例、也没标「只能人工看」：在确认框里列出来提示，不拦
    var ask = CasesModel.startDialog(r, levelName(r));
    // 按历史预测可能撞上的进行中需求（面板上已经问过一次的直接用）：列在确认框里，只提示，不拦
    var hit = typeof predictions !== 'undefined' && predictions[id] && ConflictsModel.predictLines(predictions[id].data);
    if (hit && hit.length) ask = { title: ask.title, warn: ask.warn, lines: ask.lines.concat(hit.map(function (n) { return n.text; }), [uiText('可以先设前置需求或拆分；也可以照常立项，到时候再解决冲突。', "You can set a prerequisite or split the requirement now, or approve it and resolve conflicts at merge time.")]) };
    if (typeof askStage === 'function') return askStage(ask.title, ask.lines, ask.warn ? uiText('仍然确认立项', "Approve anyway") : uiText('确认立项', "Approve and start development"), 'primary', function () { startNow(id); });
    if (window.confirm(CasesModel.startAsk(r, levelName(r)))) startNow(id);
  });
  function startNow(id) {
    $('start').disabled = true; $('gate-error').hidden = true;
    window.shell.startReq(id).then(function (made) {
      if (made.error) { $('start').disabled = false; $('gate-error').textContent = made.error; $('gate-error').hidden = false; return; }
      if (terms[id]) { terms[id].term.dispose(); terms[id].box.remove(); delete terms[id]; }
      delete states[id];
      reqs = reqs.map(function (x) { return x.id === id ? made : x; });
      if (current === id) select(id); // 重新打开这个需求：程序看到立项已过，开出开发会话
    });
  }

  // 取消这个需求、手动改状态：不在这里，在左侧列表和看板卡片的右击菜单里（stage-menu.js）


  // 验收：测试环境里配了服务的，「去人工验收」由程序启动（不经过模型）；没配的，让 Claude Code 跑起来，你自己操作；确认由你点
  $('run-it').addEventListener('click', function () {
    var id = current, r = byId(id);
    if ($('run-it').dataset.bench && r) { openTab(r, 'accept'); benchStart(id, null); return; } // 配了服务：切到人工验收标签，程序启动（bench.js）
    paste(uiText('这个需求我要验收了。把程序跑起来，告诉我怎么打开、按哪几步操作能逐条检查验收条件。不要改代码；如果现在跑不起来，告诉我原因。', "I'm ready to accept this requirement. Start the app and show me how to check each acceptance criterion. Leave the code unchanged. If the app can't start, explain why."));
    setTimeout(function () { window.shell.input(id, '\r'); }, 200);
  });
  // 确认完成：有测试集的需求先跑一遍（tests.js 的 testsBeforeDone），有失败只提示、有没确认的改动要先处理，然后照旧问你
  $('done').addEventListener('click', function () {
    var r = byId(current);
    if (!r) return;
    testsBeforeDone(r, function () { confirmDone(r.id, $('done')); }, function (on) { $('done').disabled = on; $('done').textContent = on ? uiText('正在跑测试集…', "Running test sets…") : uiText('我试过了，确认完成', "I tested it. Mark complete"); });
  });
  // 交给测试、跳过测试直接验收：和右键菜单「修改状态 → 测试中 / 待验收」是同一个动作（rf.py req set，程序校验是应用点的）
  $('to-test-btn').addEventListener('click', function () { var r = byId(current); if (r) changeStage(r, 'test'); });
  $('skip-test-btn').addEventListener('click', function () { var r = byId(current); if (r) changeStage(r, 'skip'); });
  $('testing-skip').addEventListener('click', function () { var r = byId(current); if (r) changeStage(r, 'skip'); });
  $('testing-open').addEventListener('click', function () { var r = byId(current); if (r) openTab(r, 'test'); });
  $('resume-merge').addEventListener('click', function () { confirmDone(current, $('resume-merge')); }); // 接着合并：测试集上次已经跑过，不再跑
  function confirmDone(id, button) {
    var r = byId(id); button = button || $('done');
    if (!r) return;
    var checks = r.sections['验收条件'] || [];
    var ask = uiText('确认「', "Mark “") + r.title + uiText('」已经完成？\n\n', "” complete?\n\n") + (checks.length ? uiText('验收条件：\n', "Acceptance criteria:\n") + checks.map(function (c) { return '· ' + c; }).join('\n') : uiText('注意：这个需求还没有写验收条件。', "This requirement has no acceptance criteria yet.")) + uiText('\n\n确认后，这个需求的改动会合并进项目目录现在的分支，它的会话和工作目录会收掉。有冲突时会回到它的会话里让 AI 解决，再自动合并；别的需求正在合并时会排队。', "\n\nRelayForge will merge into the current branch and close this requirement's sessions and worktree. Conflicts go to its session for resolution before retrying. The merge waits if another is running.");
    if (!window.confirm(button.id === 'resume-merge' ? uiText('继续合并「', "Resume merging “") + r.title + '」？' : ask)) return;
    button.disabled = true; $('accept-error').hidden = true;
    window.shell.doneReq(id).then(function (made) {
      button.disabled = false;
      if (made.error) { $('accept-error').textContent = made.error; $('accept-error').hidden = false; $('accept-error').scrollIntoView({ block: 'nearest' }); return; }
      afterDone(id, made);
    });
  }
}
