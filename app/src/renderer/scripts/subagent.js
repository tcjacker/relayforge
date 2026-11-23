// 设置与单需求弹窗共用安装/登录状态和模型选项。
function subagentAutomaticLabel() {
  return uiText(SubagentModel.automatic, 'Choose by task complexity');
}

function subagentChoices(prefix) {
  var cli = $(prefix + '-cli'), model = $(prefix + '-model'), status = $(prefix + '-status');
  var modelLabel = document.querySelector('label[for="' + model.id + '"]');
  var catalog = {}, auth = {}, choice, defaults, development, disabled = false, changed = false;
  var isGlobal = prefix === 'settings-subagent', generation = 0;

  function loadModels(key) {
    if (auth[key].state !== 'in' || Object.hasOwn(catalog, key)) return;
    var round = generation;
    window.shell.refreshCliModels(key).then(function (result) {
      if (round !== generation || !result.catalogs) return;
      catalog = { ...catalog, ...result.catalogs };
      render();
    }).catch(function () {});
  }

  function option(parent, name, value, unavailable) {
    var item = el('option', '', name);
    item.value = value;
    item.disabled = !!unavailable;
    parent.appendChild(item);
  }

  function render() {
    var usable = SubagentModel.available(auth);
    var inherited = isGlobal ? null : SubagentModel.effective(defaults, {}, development);
    var actual = isGlobal ? choice.cli : choice.cli || inherited.cli;
    var ready = usable.includes(actual) || (isGlobal && actual === 'follow' && usable.length > 0);
    var checking = Object.values(auth).some(function (entry) { return entry.state === 'checking'; });
    cli.replaceChildren();
    var follow = uiText('跟随开发会话的 CLI', 'Follow the development session CLI');
    if (!isGlobal) option(cli, uiText('沿用设置（{0}）', 'Use settings ({0})', SubagentModel.labels[inherited.cli]),
      '', !usable.includes(inherited.cli));
    if (isGlobal && usable.length) option(cli, follow, 'follow');
    usable.forEach(function (key) { option(cli, SubagentModel.labels[key], key); });
    if (choice.cli && !ready) {
      option(cli, SubagentModel.labels[choice.cli] ? uiText('已保存的 CLI 当前不可用', 'Saved CLI is unavailable') : follow,
        choice.cli, true);
    }
    cli.value = choice.cli;
    cli.disabled = disabled || !usable.length;
    model.replaceChildren();
    if (inherited && actual === inherited.cli) {
      option(model, uiText('沿用设置（{0}）', 'Use settings ({0})', inherited.model || subagentAutomaticLabel()), 'inherit');
    }
    option(model, subagentAutomaticLabel(), '');
    (catalog[actual] || []).forEach(function (entry) { option(model, entry[1], entry[0]); });
    var modelChoice = choice.model === null ? 'inherit' : choice.model;
    if (modelChoice === 'inherit' && inherited && actual !== inherited.cli) modelChoice = '';
    if (modelChoice && !Array.from(model.options).some(function (entry) { return entry.value === modelChoice; })) {
      option(model, uiText('{0}（已选）', '{0} (selected)', modelChoice), modelChoice, true);
    }
    model.value = modelChoice;
    model.disabled = disabled || !ready;
    if (modelLabel) modelLabel.classList.toggle('muted', model.disabled);
    status.textContent = !usable.length ? (checking ?
      uiText('正在检查安装和登录状态。', 'Checking installation and sign-in status.') :
      uiText('先安装并登录 Claude Code 或 Codex，即可配置 subagent。', 'Install and sign in to Claude Code or Codex to configure subagents.'))
      : !ready ? (checking ? uiText('正在检查已选 CLI。', 'Checking the selected CLI.') :
        uiText('{0} 当前不可用，请登录或改选。', '{0} is unavailable. Sign in or select another CLI.', SubagentModel.labels[actual])) : '';
    status.hidden = !status.textContent;
    return ready;
  }

  cli.addEventListener('change', function () {
    var inherited = isGlobal ? null : SubagentModel.effective(defaults, {}, development);
    choice = { cli: cli.value, model: inherited && (!cli.value || cli.value === inherited.cli) ? null : '' };
    changed = true;
    render();
  });
  model.addEventListener('change', function () {
    choice.model = model.value === 'inherit' ? null : model.value;
    changed = true;
  });
  return {
    reset: function (value, globalChoice, agent, catalogs) {
      generation++;
      choice = { ...value }; defaults = globalChoice; development = agent; catalog = catalogs || {}; changed = false;
      auth = {};
      Object.keys(SubagentModel.labels).forEach(function (key) { auth[key] = { state: 'checking' }; });
      render();
    },
    status: function (key, value) {
      auth[key] = value;
      if (choice) { loadModels(key); return render(); }
    },
    catalogs: function (value) { catalog = { ...catalog, ...value }; if (choice) return render(); },
    lock: function (value) { disabled = value; if (choice) return render(); },
    value: function () { return choice; },
    changed: function () { return changed; },
    ready: render,
  };
}

function showRequirementSubagent(req) {
  var card = $('req-subagent');
  if (!card) return;
  card.hidden = !req?.subagent;
  card.dataset.requirement = req?.id || '';
  if (!req?.subagent) return;
  var view = req.subagent, result = view.effective;
  var sources = { 立项对话: 'Planning chat', 右侧设置: 'Requirement settings', 跟随开发会话: 'Development session', 设置默认: 'Defaults' };
  var source = uiText('（{0}）', ' ({0})', uiText(result.source, sources[result.source] || result.source));
  var authorized = view.authorized ? uiText('对话已授权', 'Authorized in chat') : uiText('对话未授权', 'Awaiting chat authorization');
  $('req-subagent-summary').textContent = SubagentModel.labels[result.cli] + ' · ' + (result.model || subagentAutomaticLabel()) +
    source + ' · ' + authorized;
}

function wireRequirementSubagent() {
  var dialog = $('req-subagent-dlg'), save = $('req-subagent-save'), error = $('req-subagent-error');
  var inputs = subagentChoices('req-subagent'), opened = 0, target, busy;
  function fail(message) { error.textContent = message || ''; error.hidden = !message; }
  function update() { save.disabled = busy || !inputs.ready(); }
  $('req-subagent-edit').onclick = async function () {
    var req = byId($('req-subagent').dataset.requirement);
    if (!req?.subagent) return;
    var round = ++opened, view = req.subagent;
    target = req.id;
    fail(''); busy = false;
    inputs.reset({ cli: view.own.cli || '', model: view.own.model ?? null }, view.defaults, view.development, {});
    update();
    dialog.showModal();
    Object.keys(SubagentModel.labels).forEach(async function (cli) {
      var status = await window.shell.cliAuthStatus(cli).catch(function () { return { state: 'out' }; });
      if (round !== opened || !dialog.open) return;
      inputs.status(cli, status);
      update();
    });
    var settings = await window.shell.getSettings().catch(function () { return {}; });
    if (round === opened && dialog.open) { inputs.catalogs(settings.catalogs); update(); }
  };
  $('req-subagent-cli').addEventListener('change', update);
  $('req-subagent-cancel').onclick = function () { if (!busy) dialog.close(); };
  dialog.addEventListener('cancel', function (event) { if (busy) event.preventDefault(); });
  $('req-subagent-form').onsubmit = async function (event) {
    event.preventDefault();
    if (save.disabled) return;
    busy = true; fail(''); inputs.lock(true); update();
    try {
      var result = await window.shell.saveRequirementSubagent(target, inputs.value());
      if (result.error) throw new Error(result.error);
      if ($('req-subagent').dataset.requirement === target) showRequirementSubagent({ id: target, subagent: result });
      dialog.close();
    } catch (e) { fail(e.message); } finally { busy = false; inputs.lock(false); update(); }
  };
}
