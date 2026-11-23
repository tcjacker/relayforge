function wireSettings() {
  var dialog = $('settings-dlg'), form = $('settings-form'), busy = false, updateAvailable = false;
  var subagent = subagentChoices('settings-subagent');
  var check = $('settings-cli-check'), refresh = $('settings-cli-models');
  // 左边的分类：每次打开设置都回到「通用」（R-20261123-0745）
  var tabs = [$('settings-tab-general'), $('settings-tab-agents')];
  function showTab(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute('aria-controls')).hidden = !on;
    });
    $('settings-body').scrollTop = 0;
    if (focus) tab.focus();
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener('click', function () { showTab(tab); });
    tab.addEventListener('keydown', function (event) {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      event.preventDefault();
      showTab(tabs[(i + (event.key === 'ArrowDown' ? 1 : tabs.length - 1)) % tabs.length], true);
    });
  });
  // 输入框在隐藏的分类里时浏览器自带的校验聚焦不到它，所以自己校验：不合格就跳回「通用」并标出
  var droppedInputs = [$('dropped-days'), $('dropped-max')];
  function droppedValid(input) {
    var n = Number(input.value);
    return input.value.trim() !== '' && Number.isInteger(n) && n >= Number(input.min) && n <= Number(input.max);
  }
  droppedInputs.forEach(function (input) {
    input.addEventListener('input', function () { input.classList.toggle('invalid', !droppedValid(input)); error(''); });
  });
  function error(message) { $('settings-error').textContent = message || ''; $('settings-error').hidden = !message; }
  function status(message, path) {
    var line = $('settings-cli-status');
    line.textContent = message || '';
    line.hidden = !message;
    line.title = path || '';
  }
  function setUpdate(value) { updateAvailable = value; check.textContent = value ? uiText('更新 CLI', "Update CLI") : uiText('检查 CLI 更新', "Check for CLI updates"); }
  function lock(value) {
    busy = value;
    subagent.lock(value);
    [$('language-options'), $('agent-options'), $('settings-monitor'), $('approval-options'), $('dropped-options'), $('settings-save'), $('settings-cancel')].forEach(function (el) { el.disabled = value; });
    check.disabled = refresh.disabled = value || !form.elements.agent.value;
  }
  // 每个 CLI 一行右边的登录状态（R-20261121-1f0c）。opened 每次打开设置加一，关掉以后回来的旧结果不再显示。
  var auth = {}, opened = 0;
  function renderAuth(agent) {
    var row = form.querySelector('[data-agent="' + agent + '"]');
    var box = row.querySelector('[data-auth]'), sub = row.querySelector('[data-auth-sub]');
    var v = CliAuthModel.view(auth[agent], agent), esc = HtmlText.esc, html = '';
    if (v.tag && v.tag.kind === 'checking') html += '<span class="auth-spin" aria-hidden="true"></span><span class="muted">' + esc(v.tag.text) + '</span>';
    else if (v.tag) html += '<span class="auth-tag ' + esc(v.tag.kind) + '"><i></i>' + esc(v.tag.text) + '</span>';
    if (v.button) {
      html += '<button type="button" class="' + (v.button.action === 'login' ? 'login' : '') + '" data-auth-action="' +
        esc(v.button.action) + '">' + esc(v.button.text) + '</button>';
    }
    box.innerHTML = html;
    sub.textContent = v.sub.text;
    sub.classList.toggle('err', v.sub.error);
  }
  function setAuth(agent, entry) { auth[agent] = entry; renderAuth(agent); subagent.status(agent, entry); }
  function checkAuth(agent) {
    var round = opened;
    setAuth(agent, { state: 'checking' });
    window.shell.cliAuthStatus(agent).catch(function (e) { return { error: e.message }; }).then(function (result) {
      if (round === opened) setAuth(agent, CliAuthModel.fromStatus(result));
    });
  }
  async function login(agent) {
    var waiting = { state: 'waiting' }, round = opened;
    setAuth(agent, waiting);
    var result = await window.shell.loginCli(agent).catch(function (e) { return { error: e.message }; });
    if (round === opened && auth[agent] === waiting) setAuth(agent, CliAuthModel.fromLogin(result)); // 中途取消或重新点过就不管这次的结果
  }
  $('agent-options').addEventListener('click', function (event) {
    var button = event.target.closest('[data-auth-action]');
    if (!button) return;
    event.preventDefault(); // 按钮在 label 里，不要顺带选中这一行
    var agent = button.closest('[data-agent]').dataset.agent;
    if (button.dataset.authAction === 'login') login(agent);
    else { window.shell.cancelCliLogin(agent); setAuth(agent, { state: 'out' }); }
  });
  // 关掉设置就停掉还在等授权的登录，再打开时按实际情况重新查
  dialog.addEventListener('close', function () {
    opened++;
    Object.keys(auth).forEach(function (agent) { if (auth[agent].state === 'waiting') window.shell.cancelCliLogin(agent); });
  });
  $('settings').addEventListener('click', async function () {
    error(''); status(''); setUpdate(false); lock(true); showTab(tabs[0]);
    droppedInputs.forEach(function (input) { input.classList.remove('invalid'); });
    dialog.showModal();
    opened++;
    ['claude', 'codex'].forEach(checkAuth);
    try {
      var result = await window.shell.getSettings();
      if (result.error) throw new Error(result.error);
      subagent.reset(result.subagent || SubagentModel.defaults, null, null, result.catalogs);
      Object.keys(auth).forEach(function (agent) { subagent.status(agent, auth[agent]); });
      form.elements.agent.value = result.agent;
      form.elements.language.value = result.language;
      $('settings-monitor').checked = result.monitor === true;
      form.elements.headlessApproval.value = result.headlessApproval || 'auto';
      var dropped = result.droppedBranches || {};
      $('dropped-days').value = dropped.keepDays || 30;
      $('dropped-max').value = dropped.keepMax || 20;
    } catch (e) { error(e.message); } finally { lock(false); }
  });
  $('agent-options').addEventListener('change', function () { error(''); status(''); setUpdate(false); lock(false); });
  check.addEventListener('click', async function () {
    if (busy) return;
    var agent = form.elements.agent.value, updating = updateAvailable;
    error(''); status(updating ? uiText('正在更新 CLI…', "Updating CLI…") : uiText('正在检查 CLI 版本…', "Checking CLI version…")); lock(true);
    try {
      var result = await (updating ? window.shell.updateCli(agent) : window.shell.checkCli(agent));
      if (result.error) throw new Error(result.error);
      setUpdate(result.available);
      if (result.desktop) status(uiText('当前版本 ', "Current version ") + result.current + uiText('，由 ', ", provided by the ") + result.desktop + uiText(' 桌面应用提供，请在该应用里检查更新。', " desktop app. Check for updates there."), result.path);
      else if (result.available) status(uiText('当前版本 ', "Current version ") + result.current + uiText('，可更新到 ', ". Update available: ") + result.latest + '。', result.path);
      else if (updating) {
        status(uiText('当前版本 ', "Current version ") + result.current + uiText('。正在刷新模型列表…', ". Refreshing the model list…"));
        try { await refreshModels(agent, uiText('当前版本 ', "Current version ") + result.current + '。'); }
        catch (e) { status(uiText('CLI 已更新到 ', "CLI updated to ") + result.current + '。'); error(e.message); }
      } else status(uiText('CLI 已是最新版本（', "CLI is up to date (") + result.current + '）。', result.path);
    } catch (e) { status(''); error(e.message); } finally { lock(false); }
  });
  async function refreshModels(agent, prefix) {
    var result = await window.shell.refreshCliModels(agent);
    if (result.error) throw new Error(result.error);
    applyRequirementCatalogs(result.catalogs);
    subagent.catalogs(result.catalogs);
    status(prefix + uiText('已刷新模型列表，可在需求中选择（', "Model list refreshed. Available for requirements (") + result.count + uiText(' 个模型）。', " models)."));
  }
  refresh.addEventListener('click', async function () {
    if (busy) return;
    var agent = form.elements.agent.value;
    error(''); status(uiText('正在刷新模型列表…', "Refreshing the model list…")); lock(true);
    try { await refreshModels(agent, ''); }
    catch (e) { status(''); error(e.message); } finally { lock(false); }
  });
  $('settings-cancel').addEventListener('click', function () { dialog.close(); });
  dialog.addEventListener('cancel', function (event) { if (busy) event.preventDefault(); });
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (busy) return;
    var agent = form.elements.agent.value;
    error('');
    var bad = droppedInputs.filter(function (input) { return !droppedValid(input); });
    droppedInputs.forEach(function (input) { input.classList.toggle('invalid', bad.indexOf(input) >= 0); });
    if (bad.length) {
      showTab(tabs[0]);
      bad[0].focus();
      error(uiText('请先改好「通用」里标红的数值（保留天数 1–3650，最多保留个数 1–1000）。', "Fix the highlighted values in General first (days 1–3650, branches 1–1000)."));
      return;
    }
    lock(true);
    try {
      var result = await window.shell.saveSettings({ agent: agent, language: form.elements.language.value, monitor: $('settings-monitor').checked, headlessApproval: form.elements.headlessApproval.value || 'auto',
        droppedBranches: { keepDays: Number($('dropped-days').value), keepMax: Number($('dropped-max').value) },
        ...(subagent.changed() ? { subagent: subagent.value() } : {}) });
      if (result.error) throw new Error(result.error);
      dialog.close();
      if (result.language !== document.documentElement.lang) {
        var url = new URL(location.href);
        url.searchParams.set('language', result.language);
        location.replace(url.href);
      }
    } catch (e) { error(e.message); } finally { lock(false); }
  });
}
