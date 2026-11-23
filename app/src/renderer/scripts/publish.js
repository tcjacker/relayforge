// 发布：顶栏的同步状态和发布对话框。数据由主进程（publish.js）从 rf.py publish status 读来，
// 显示什么由 publish-model.js 算；这里只管画出来、绑按钮。推送只有点了对话框里的「发布」才会发生。
function wirePublish() {
  var dlg = $('publish-dlg'), st = null, working = null, remote = '', acked = false, error = '', info = '', past = [], pullDiverged = false;

  function renderIndicator() {
    var b = $('publish'), ind = PublishModel.indicator(st);
    var local = $('publish-pending'), pending = PublishModel.pendingText(st);
    local.textContent = pending;
    local.hidden = !pending;
    b.hidden = ind.kind === 'none' && !working;
    b.textContent = working === uiText('发布', "Publish") ? uiText('正在发布…', "Publishing…") : ind.text;
    b.title = ind.title;
    b.className = 'pubind ' + ind.kind + (working ? ' working' : '');
  }

  function list(title, rows) {
    if (!rows.length) return null;
    var box = el('div'), ul = el('ul');
    box.appendChild(el('h2', '', title));
    rows.forEach(function (r) { var li = el('li'); r(li); ul.appendChild(li); });
    box.appendChild(ul);
    return box;
  }

  function renderDialog() {
    var m = PublishModel.dialog(st, remote, acked, { pullDiverged: pullDiverged }), box = $('publish-carry'), notes = $('publish-notes');
    box.replaceChildren(); notes.replaceChildren();
    var none = !m.error && st && !(st.remotes || []).length;
    $('publish-target').textContent = m.error ? '' : (m.target ? uiText('项目主目录：', "Main checkout: ") + m.target : none ? uiText('没有远端，没有地方可以推送', "No remote to push to") : uiText('还不知道要推送到哪里', "Push destination unknown"));
    var pending = PublishModel.pendingText(st);
    $('publish-local-count').textContent = pending;
    $('publish-local').hidden = !pending;
    $('publish-deploy').textContent = m.error || none ? '' : m.deploy;
    $('publish-create').textContent = m.create || ''; $('publish-create').hidden = !m.create;
    var pick = $('publish-remotes'), sel = $('publish-remote');
    pick.hidden = !m.remotes;
    if (m.remotes) {
      sel.replaceChildren.apply(sel, [new Option(uiText('请选一个……', "Select a remote…"), '')].concat(m.remotes.map(function (r) { return new Option(r.name + '  ' + r.url, r.name); })));
      sel.value = remote;
    }
    if (m.error) { notes.appendChild(el('p', 'error', m.error)); }
    else {
      var ind = PublishModel.indicator(st);
      if (ind.kind === 'count' || m.empty) box.appendChild(el('p', '', ind.text));
      var parts = [
        list(uiText('包含的需求', "Included requirements"), m.requirements.map(function (r) { return function (li) { li.textContent = r.id + ' ' + r.title; }; })),
        list(uiText('包含的问题（已解决）', "Included resolved issues"), m.issues.map(function (i) { return function (li) { li.textContent = i.num + ' ' + i.title; }; })),
      ];
      parts.forEach(function (p) { if (p) box.appendChild(p); });
      if (m.factsText) box.appendChild(el('p', 'pub-line', m.factsText));
      if (m.othersTotal) {
        var d = el('details'), ul = el('ul');
        d.appendChild(el('summary', '', m.othersText));
        m.others.forEach(function (o) { var li = el('li'), c = el('code', '', o.hash + ' '); li.appendChild(c); li.appendChild(document.createTextNode(o.subject)); ul.appendChild(li); });
        d.appendChild(ul); box.appendChild(d);
      }
      if (m.empty) box.appendChild(el('p', 'pub-line', m.empty));
      if (!m.requirements.length && !m.issues.length && !m.othersTotal && !m.factsText && !m.empty && m.target) box.appendChild(el('p', 'pub-line', uiText('没有要发布的提交。', "No commits to publish.")));
      if (!none) box.appendChild(el('p', 'pub-line', m.recordText));
      m.blockers.forEach(function (b) { notes.appendChild(el('div', 'pub-block', uiText('现在不能发布：', "Can't publish: ") + b.text)); });
      if (m.divergedText) notes.appendChild(el('div', 'pub-block', m.divergedText));
      if (m.merging) notes.appendChild(el('div', 'pub-block', uiText('现在不能发布：', "Can't publish: ") + m.merging.text));
      m.warnings.forEach(function (w) {
        var div = el('div', 'pub-warn', w.text);
        if (w.paths && w.paths.length) { var ul = el('ul'); w.paths.forEach(function (p) { ul.appendChild(el('li', '', p)); }); if (w.count > w.paths.length) ul.appendChild(el('li', '', uiText('…等共 ', "… ") + w.count + uiText(' 处', " total"))); div.appendChild(ul); }
        notes.appendChild(div);
      });
      if (m.fetchError) notes.appendChild(el('div', 'pub-warn', uiText('检查远端没有成功：', "Couldn't check remote: ") + m.fetchError.error + (m.fetchError.detail ? uiText('（git 说：', " (Git: ") + m.fetchError.detail + '）' : '')));
      if (m.lastFetch) notes.appendChild(el('p', 'muted', uiText('上次检查远端：', "Last remote check: ") + m.lastFetch + uiText('。远端的新提交只算到那时为止。', ". Remote commit counts reflect that check.")));
    }
    $('publish-ack-row').hidden = !m.ack; $('publish-ack').checked = acked; $('publish-ack-text').textContent = m.ack || '';
    $('publish-error').textContent = error; $('publish-error').hidden = !error;
    $('publish-working').textContent = working ? uiText('正在', "Running ") + working + uiText('…（网络慢时可能要一会儿，请不要关闭）', "… This may take a while on a slow connection.") : info; $('publish-working').hidden = !working && !info;
    $('publish-go').textContent = working === uiText('发布', "Publish") ? uiText('正在发布…', "Publishing…") : m.primary || uiText('发布', "Publish");
    $('publish-go').disabled = !!working || !m.can.publish;
    $('publish-check').disabled = !!working || !m.can.check;
    $('publish-pull').hidden = !m.pull; $('publish-pull').textContent = m.pull || ''; $('publish-pull').disabled = !!working || !m.can.pull;
    $('publish-ai').hidden = !m.aiMerge; $('publish-ai').textContent = m.aiMerge || ''; $('publish-ai').disabled = !!working;
    $('publish-back').hidden = !(m.merging && m.merging.back); $('publish-back').textContent = m.merging ? m.merging.back : ''; $('publish-back').disabled = !!working;
    $('publish-cancel').disabled = !!working; sel.disabled = !!working;
  }

  function renderPast() {
    $('publish-past-count').textContent = past.length ? '（' + past.length + '）' : '';
    var ul = $('publish-past-list');
    ul.replaceChildren();
    if (!past.length) { ul.appendChild(el('li', 'muted', uiText('还没有发布过。', "No publications yet."))); return; }
    past.forEach(function (r) {
      var li = el('li'), what = [];
      if (r.requirements.length) what.push(uiText('需求：', "Requirements: ") + r.requirements.map(function (x) { return x.title || x.id; }).join('、'));
      if (r.issues.length) what.push(uiText('问题：', "Issues: ") + r.issues.map(function (x) { return x.num + ' ' + x.title; }).join('、'));
      li.appendChild(el('span', '', r.created.slice(0, 16) + '  ' + r.branch + ' → ' + r.remote + '/' + r.target + (r.published ? '' : uiText('（没推成）', " (push failed)"))));
      li.appendChild(el('small', '', what.join('；') || (uiText('共 ', "") + r.commits + uiText(' 个提交，没有带需求或问题', " commits without a requirement or issue"))));
      ul.appendChild(li);
    });
  }

  function failText(r) { return r.error + (r.detail ? uiText('\ngit 说：', "\nGit: ") + r.detail : ''); }
  function reload(extra) {
    return window.shell.publishStatus(Object.assign({ remote: remote || undefined }, extra || {})).then(function (r) {
      if (r && r.busy) return;
      if (r && r.error && !r.branch) { error = r.error; } else if (r) { st = r; }
      renderIndicator(); renderDialog();
    });
  }

  window.shell.onPublish(function (s) { st = s; renderIndicator(); if (dlg.open) renderDialog(); });
  window.shell.onPublishBusy(function (b) { working = b.working || null; renderIndicator(); if (dlg.open) renderDialog(); });
  window.shell.publishCurrent().then(function (s) { if (s) { st = s; renderIndicator(); } });
  window.addEventListener('focus', function () { window.shell.publishCurrent(); }); // 回到应用时重读一次（很便宜，不抓取远端）

  $('publish').addEventListener('click', function () {
    error = ''; info = ''; acked = false; remote = ''; pullDiverged = false;
    renderDialog(); dlg.showModal();
    reload();
    window.shell.publishList().then(function (rows) { past = rows || []; renderPast(); });
  });
  $('publish-cancel').addEventListener('click', function () { if (!working) dlg.close(); });
  dlg.addEventListener('cancel', function (e) { if (working) e.preventDefault(); });
  $('publish-ack').addEventListener('change', function () { acked = $('publish-ack').checked; renderDialog(); });
  $('publish-remote').addEventListener('change', function () { remote = $('publish-remote').value; acked = false; error = ''; if (remote) reload(); else renderDialog(); });
  $('publish-check').addEventListener('click', function () { if (working) return; error = ''; info = ''; pullDiverged = false; reload({ fetch: true }); });
  $('publish-ai').addEventListener('click', function () {
    if (working) return;
    error = ''; info = '';
    window.shell.publishAiMerge().then(function (r) {
      if (r && r.busy) return;
      if (!r || r.error) { error = (r && r.error) || uiText('没能开始。', "Couldn't start."); if (r && r.status) st = r.status; renderDialog(); return; }
      dlg.close();
      startChat(r.sessionId, r.title, r.say);
    });
  });
  $('publish-back').addEventListener('click', function () {
    var chat = st && st.merge_chat && st.merge_chat.id;
    if (!chat || working) return;
    dlg.close();
    if (tab !== 'chats') view('chats');
    select(chat);
  });
  $('publish-pull').addEventListener('click', function () {
    if (working) return;
    error = ''; info = '';
    window.shell.publishPull().then(function (r) {
      if (r && r.busy) return;
      if (r && r.error) { error = failText(r); pullDiverged = r.code === 'diverged'; } else { info = r.message || ''; pullDiverged = false; }
      renderDialog();
    });
  });
  $('publish-go').addEventListener('click', function () {
    if (working || $('publish-go').disabled) return; // 正在做的时候不能再点
    error = ''; info = '';
    window.shell.publishRun({ remote: remote || undefined }).then(function (r) {
      if (r && r.busy) return;
      if (r && r.error) { error = failText(r) + (r.record ? uiText('\n发布记录已经准备好了，下次发布会接着用它，不会重复。', "\nThe publication record is ready and will be reused on the next attempt.") : ''); renderDialog(); return; }
      dlg.close(); // 成功：主进程已经发了通知和新的同步状态
    });
  });
}
