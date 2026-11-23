// 新建需求：写说明、附图，并选择立项、开发和测试的会话配置（测试默认「跟开发一样」）。
var dlg = $('new-dlg'), drafts = [], firstImages = {}, firstSay = {}; // firstSay：问题转成的需求，立项会话的第一句话里多交代一句
var sessionPickers;
var needsSessionDefaults = true;
var newDefaultAgent = 'claude';

function updateNewModelSummary() {
  if (!sessionPickers || needsSessionDefaults) return;
  var count = Object.entries(sessionPickers).filter(function ([phase, picker]) {
    var choice = picker.choice();
    return phase === 'testing' ? !RequirementSessions.isFollow(choice) : choice.cli !== newDefaultAgent || !!choice.model;
  }).length;
  $('new-model-summary').textContent = count ? uiText('已自定义 ', "Customized ") + count + uiText(' 项', " settings") : uiText('使用默认设置', "Use defaults");
}

var FOLLOW = 'follow';
// follows：多一个「跟开发一样」的选项（测试会话用）：选了它就不单独选模型，选择的结果是 { follow: true }，不是开发会话的值的一份复制。
function sessionPicker(cli, model, follows) {
  var options = Object.entries(RequirementSessions.catalog).map(([value, option]) => new Option(option.label, value));
  var saved;
  cli.replaceChildren(...(follows ? [new Option(uiText('跟开发一样', "Same as development"), FOLLOW)] : []), ...options);
  function refreshModels() {
    var following = !!follows && cli.value === FOLLOW;
    model.replaceChildren(...(following ? [new Option(uiText('跟开发一样', "Same as development"), '')] : RequirementSessions.optionsFor(cli.value).map(([value, label]) => new Option(label, value))));
    model.disabled = following;
  }
  cli.addEventListener('change', refreshModels);
  refreshModels();
  function restore(value) {
    if (Array.from(model.options).some((option) => option.value === value)) model.value = value;
    else if (value && saved?.cli === cli.value && saved.model === value) {
      model.add(new Option(value + uiText('（当前列表中不可选）', " (unavailable)"), value));
      model.value = value;
    }
  }
  return {
    reset(agent) { saved = undefined; cli.value = follows ? FOLLOW : agent; refreshModels(); },
    set(choice) {
      saved = choice;
      cli.value = RequirementSessions.isFollow(choice) ? FOLLOW : choice.cli;
      refreshModels();
      if (choice?.model) restore(choice.model);
    },
    refresh() {
      var value = model.value;
      refreshModels();
      restore(value);
    },
    choice() { return follows && cli.value === FOLLOW ? { follow: true } : { cli: cli.value, model: model.value }; },
  };
}

function applyRequirementCatalogs(catalogs) {
  RequirementSessions.setCatalogs(catalogs);
  Object.values(sessionPickers || {}).forEach((picker) => picker.refresh());
  updateNewModelSummary();
}

function disableSessionPickers(disabled) {
  dlg.querySelectorAll('.req-session-phase').forEach(function (fieldset) { fieldset.disabled = disabled; });
  $('new-ok').disabled = disabled;
}
function thumbs() {
  $('new-images').replaceChildren.apply($('new-images'), drafts.map(function (p, i) {
    var li = el('li'), img = el('img'), x = el('button', '', '×');
    img.src = encodeURI('file://' + p).replace(/#/g, '%23'); img.alt = ''; img.title = p;
    x.type = 'button'; x.title = uiText('去掉这张图', "Remove image"); x.addEventListener('click', function () { drafts.splice(i, 1); thumbs(); $('new-title').focus(); });
    li.appendChild(img); li.appendChild(x); return li;
  }));
}
function addDrafts(paths) { paths.filter(Boolean).forEach(function (p) { if (drafts.indexOf(p) < 0) drafts.push(p); }); thumbs(); $('new-title').focus(); }
function openNew() {
  if (tab !== 'reqs') view('reqs');
  $('new-error').hidden = true;
  $('new-model-settings').open = false;
  dlg.showModal(); $('new-title').focus();
  if (needsSessionDefaults) {
    needsSessionDefaults = false;
    disableSessionPickers(true);
    window.shell.getSettings().catch(function () { return {}; }).then(function (settings) {
      if (settings.catalogs) applyRequirementCatalogs(settings.catalogs);
      newDefaultAgent = Object.hasOwn(RequirementSessions.catalog, settings.agent) ? settings.agent : 'claude';
      Object.values(sessionPickers).forEach(function (picker) { picker.reset(newDefaultAgent); });
      updateNewModelSummary();
      $('new-testing-note').hidden = true;
      disableSessionPickers(false);
    });
  }
  window.shell.discoverCliModels().then(applyRequirementCatalogs).catch(function () {});
}

// 需求最上面只读的「会话用的 CLI 和模型」：进行中、测试中、待验收才显示。建好以后不能改，只能新建时选。
function sessionChoicesPanel(r, stage) {
  var box = $('session-choices');
  var show = !!(r && !r.loose && r.state === 'open' && ['build', 'test', 'check'].indexOf(stage) >= 0);
  box.hidden = !show;
  if (!show) return;
  window.shell.getSettings().catch(function () { return {}; }).then(function (settings) {
    var agent = Object.hasOwn(RequirementSessions.catalog, settings.agent) ? settings.agent : 'claude';
    var lines = RequirementSessions.choiceLines(r.session_choices, agent);
    box.textContent = '';
    var title = document.createElement('p'); title.textContent = uiText('会话用的 CLI 和模型', "Session CLI and model"); title.className = 'session-choices-title'; box.appendChild(title);
    [[uiText('开发会话', "Development session"), lines.development], [uiText('测试会话', "Testing session"), lines.testing]].forEach(function (x) {
      var p = document.createElement('p'), b = document.createElement('b');
      b.textContent = x[0]; p.appendChild(b); p.appendChild(document.createTextNode(' ' + x[1])); box.appendChild(p);
    });
  });
}

// 绑定事件、开始加载：由 main.js 按顺序调用
function wireNewReq() {
  RequirementSessions.setCatalogs({});
  sessionPickers = {
    intake: sessionPicker($('new-intake-cli'), $('new-intake-model')),
    development: sessionPicker($('new-development-cli'), $('new-development-model')),
    testing: sessionPicker($('new-testing-cli'), $('new-testing-model'), true),
  };
  $('new-model-settings').addEventListener('change', updateNewModelSummary);
  $('new-testing-cli').addEventListener('change', function () { $('new-testing-note').hidden = $('new-testing-cli').value !== 'codex'; });
  $('new-testing-note').textContent = RequirementSessions.CODEX_TESTING_NOTE;
  window.shell.getSettings().then(function (settings) {
    if (settings.catalogs) applyRequirementCatalogs(settings.catalogs);
  }).catch(function () {});
  $('new-req').addEventListener('click', openNew);
  $('new-cancel').addEventListener('click', function () { dlg.close(); });
  $('new-pick').addEventListener('click', function () { window.shell.pickImages().then(addDrafts); });
  $('new-title').addEventListener('keydown', function (e) { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); $('new').requestSubmit(); } });
  dlg.addEventListener('dragenter', function (e) { if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') >= 0) dlg.classList.add('over'); });
  dlg.addEventListener('dragleave', function (e) { if (!dlg.contains(e.relatedTarget)) dlg.classList.remove('over'); });
  dlg.addEventListener('drop', function (e) { e.preventDefault(); e.stopPropagation(); dlg.classList.remove('over'); toPaths(Array.prototype.slice.call(e.dataTransfer.files)).then(addDrafts); });
  $('new').addEventListener('submit', function (e) {
    e.preventDefault();
    var input = $('new-title'), title = input.value.trim();
    if (!title || $('new-ok').disabled) { input.focus(); return; }
    var choices = { intake: sessionPickers.intake.choice(), development: sessionPickers.development.choice(), testing: sessionPickers.testing.choice() };
    input.disabled = true; disableSessionPickers(true); $('new-error').hidden = true;
    window.shell.newReq(title, choices).catch(function (error) { return { error: error.message }; }).then(function (made) {
      input.disabled = false; disableSessionPickers(false);
      if (made.error) { $('new-error').textContent = made.error; $('new-error').hidden = false; input.focus(); return; }
      if (drafts.length) firstImages[made.id] = drafts.slice();
      input.value = ''; drafts = []; needsSessionDefaults = true; thumbs(); dlg.close();
      reqs = [made].concat(reqs.filter(function (r) { return r.id !== made.id; }));
      select(made.id);
    });
  });
}
