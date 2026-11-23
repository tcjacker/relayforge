// 项目名称旁的分支选择，交互见 DS-20261119-1522。
function wireBranches() {
  var picker = $('branch-picker'), toggle = $('branch-toggle'), panel = $('branch-panel'), search = $('branch-search'),
    listBox = $('branch-list'), remoteBox = $('remote-list'), remoteName = $('remote-local-name'),
    refresh = $('remote-refresh'), remoteStatus = $('remote-status'), go = $('branch-switch'),
    error = $('branch-error'), confirm = $('branch-confirm');
  var data = null, selected = null, pendingSwitch = null;
  var switching = false, refreshing = false, opening = false, readVersion = 0, refreshStatus = '';

  function showError(text) { error.textContent = text || ''; error.hidden = !text; }

  function label(name) {
    $('branch-name').textContent = name || uiText('（不在分支上）', "(detached HEAD)");
    toggle.title = name ? uiText('项目主目录的分支：', "Main checkout branch: ") + name : uiText('项目主目录现在不在任何分支上', "The project checkout is in detached HEAD state");
    picker.hidden = false;
  }

  function visibleTarget() {
    if (!selected) return null;
    var matches = selected.remote ? BranchModel.remotes(data, search.value) : BranchModel.rows(data, search.value);
    return matches.find(function (row) { return row.name === selected.name; }) || null;
  }

  function renderAction() {
    var target = visibleTarget();
    var action = BranchModel.plan(data, target, remoteName.value);
    $('remote-plan').hidden = !(action && action.text);
    $('remote-plan-text').textContent = action && action.text || '';
    $('remote-name-label').hidden = !(action && action.needsName);
    toggle.disabled = switching;
    go.textContent = switching ? uiText('正在切换…', "Switching…") : action && action.creating ? uiText('创建并切换', "Create and switch") : uiText('切换分支', "Switch branch");
    go.disabled = opening || switching || refreshing || !action || !action.local || action.local === data.current;
    refresh.disabled = opening || switching || refreshing || !data || !data.has_remotes;
    remoteStatus.textContent = refreshing ? uiText('正在刷新远端分支…', "Refreshing remote branches…")
      : refreshStatus || (data && !data.has_remotes ? uiText('项目还没有远端', "This project has no remote") : '');
    remoteStatus.dataset.error = String(!refreshing && refreshStatus.startsWith(uiText('刷新失败', "Refresh failed")));
    remoteStatus.hidden = !remoteStatus.textContent;
  }

  function renderRows(container, branches, remote) {
    container.replaceChildren();
    branches.forEach(function (b) {
      var row = el('button', 'branch-row' + (b.blocked ? ' blocked' : ''));
      row.type = 'button';
      row.title = b.blocked ? b.name + '\n' + b.blocked : b.name;
      row.setAttribute('aria-pressed', String(!!selected && selected.remote === remote && selected.name === b.name));
      if (b.current) row.setAttribute('aria-current', 'true');
      row.appendChild(toggle.querySelector('svg').cloneNode(true));
      var label = el('span', 'branch-label');
      label.appendChild(el('span', 'branch-name', b.name));
      var count = BranchModel.countText(b);
      if (count) label.appendChild(el('span', 'branch-count', count));
      row.appendChild(label);
      if (b.current) row.appendChild(el('span', 'tag', uiText('✓ 当前', "✓ Current")));
      row.addEventListener('click', function () {
        if (!selected || selected.remote !== remote || selected.name !== b.name) remoteName.value = '';
        selected = { remote: remote, name: b.name };
        showError('');
        picker.querySelectorAll('.branch-row[aria-pressed="true"]').forEach(function (item) {
          item.setAttribute('aria-pressed', 'false');
        });
        row.setAttribute('aria-pressed', 'true');
        renderAction();
        row.focus();
      });
      if (!remote) row.addEventListener('dblclick', attempt);
      container.appendChild(row);
    });
  }

  function render() {
    var locals = BranchModel.rows(data, search.value), remotes = BranchModel.remotes(data, search.value);
    renderRows(listBox, locals, false);
    renderRows(remoteBox, remotes, true);
    $('branch-local-group').hidden = !locals.length;
    $('branch-empty').hidden = opening || !data || !!data.error || locals.length + remotes.length > 0;
    var footer = $('branch-remote');
    footer.textContent = BranchModel.footer(data);
    footer.dataset.pending = String(!!(data && data.pending > 0));
    renderAction();
  }

  async function load() {
    var version = ++readVersion;
    try {
      var result = await window.shell.branchList();
      if (version !== readVersion || panel.hidden) return;
      if (!result || result.error) {
        showError(uiText('读不到分支列表：', "Couldn't load branches: ") + BranchModel.failure(result));
      } else {
        data = result;
        label(data.current);
        if (!selected && data.current) selected = { remote: false, name: data.current };
      }
    } catch (e) {
      if (version === readVersion && !panel.hidden) showError(uiText('读不到分支列表：', "Couldn't load branches: ") + e.message);
    } finally {
      if (version === readVersion && !panel.hidden) {
        opening = false;
        render();
      }
    }
  }

  function open() {
    selected = null;
    search.value = '';
    remoteName.value = '';
    showError('');
    opening = true;
    panel.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    panel.querySelector('.branch-groups').scrollTop = 0;
    render();
    search.focus();
    load();
  }

  function close(restore) {
    readVersion++;
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    if (restore) toggle.focus();
  }

  function attempt() {
    if (go.disabled) return;
    var decision = BranchModel.decide(data, visibleTarget(), remoteName.value);
    if (decision.kind === 'blocked') { showError(decision.text); return; }
    if (decision.kind === 'confirm') {
      pendingSwitch = decision.request;
      $('branch-confirm-title').textContent = decision.creating ? uiText('创建并切换分支？', "Create and switch branches?") : uiText('切换分支？', "Switch branches?");
      $('branch-confirm-message').textContent = decision.text;
      confirm.showModal();
    } else if (decision.kind === 'go') {
      doSwitch(decision.request);
    }
  }

  async function doSwitch(request) {
    if (switching || !request) return;
    switching = true;
    readVersion++;
    showError('');
    renderAction();
    var destination = null;
    try {
      var out = await window.shell.branchSwitch(request);
      if (!out || !out.ok) {
        showError(BranchModel.failure(out));
        if (out && ['stale', 'missing', 'name-taken'].includes(out.code)) load();
      } else {
        if (out.list && !out.list.error) data = out.list;
        destination = out.to;
      }
    } catch (e) {
      showError(uiText('切换没有成功：', "Couldn't switch branches: ") + e.message);
    } finally {
      switching = false;
      renderAction();
      if (destination) {
        label(destination);
        $('branch-announcement').textContent = uiText('已切换到 ', "Switched to ") + destination;
        close(true);
      }
    }
  }

  refresh.addEventListener('click', async function () {
    refreshing = true;
    readVersion++;
    renderAction();
    try {
      var result = await window.shell.branchRefresh();
      var issue = result && (result.refresh_error || result.error);
      if (result && !issue) data = result;
      refreshStatus = issue ? uiText('刷新失败，保留上次的远端分支。\n', "Refresh failed. Showing the previous remote branches.\n") + issue : uiText('远端分支已刷新', "Remote branches refreshed");
    } catch (e) {
      refreshStatus = uiText('刷新失败，保留上次的远端分支。\n', "Refresh failed. Showing the previous remote branches.\n") + e.message;
    } finally {
      refreshing = false;
      render();
    }
  });

  toggle.addEventListener('click', function () { if (panel.hidden) open(); else close(true); });
  search.addEventListener('input', function () { showError(''); render(); });
  remoteName.addEventListener('input', function () { showError(''); renderAction(); });
  go.addEventListener('click', attempt);
  function cancelConfirmation() { pendingSwitch = null; confirm.close(); go.focus(); }
  $('branch-confirm-cancel').addEventListener('click', cancelConfirmation);
  $('branch-confirm-go').addEventListener('click', function () {
    var request = pendingSwitch;
    pendingSwitch = null;
    confirm.close();
    doSwitch(request);
  });
  confirm.addEventListener('cancel', function (e) { e.preventDefault(); cancelConfirmation(); });
  picker.addEventListener('keydown', function (e) {
    if (panel.hidden || confirm.open) return;
    if (e.key === 'Escape') { e.preventDefault(); close(true); return; }
    if (e.key === 'Enter' && (document.activeElement === search || document.activeElement === remoteName)) {
      e.preventDefault();
      attempt();
      return;
    }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    var rows = [...listBox.children, ...remoteBox.children], at = rows.indexOf(document.activeElement);
    if (at < 0 && document.activeElement !== search) return;
    e.preventDefault();
    var next = e.key === 'ArrowDown' ? at + 1 : at <= 0 ? rows.length - 1 : at - 1;
    (rows[next] || search).focus();
  });
  document.addEventListener('pointerdown', function (e) {
    if (!panel.hidden && !confirm.open && !picker.contains(e.target)) close(false);
  });
  picker.addEventListener('focusout', function (e) {
    if (!panel.hidden && !confirm.open && e.relatedTarget && !picker.contains(e.relatedTarget)) close(false);
  });

  window.shell.onPublish(function (st) {
    if (!st || st.error || switching) return;
    label(st.branch);
    if (!panel.hidden && !refreshing && !confirm.open) load();
  });
  window.shell.publishCurrent().then(function (st) { if (st && !st.error) label(st.branch); });
}
