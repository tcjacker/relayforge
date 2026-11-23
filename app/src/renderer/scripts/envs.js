// 测试页的三块（测试集管理、人工测试、测试环境）怎么切换，测试环境页，以及各处的「测试环境」选择框。
// 数据由主进程 envs.js 调用 rf.py env 给出；编辑稿、改没改过、依赖表、JWT、建议怎么显示在 env-model.js。
var EM = EnvModel, eesc = EM.esc;
var EV = {
  tab: 'sets',     // 测试页现在是哪一块：sets | manual | env
  list: null,      // rf.py env list 的结果：选择框和左边的列表用
  cur: null,       // 正在看的那一套的编号
  data: null,      // 它的全部设置（rf.py env reveal，保密的值是明文，只在界面里）
  draft: null,     // 正在改的编辑稿
  user: 0,         // 测试用户里正在改第几个
  peek: {},        // 点了「显示」的保密框：路径 -> true
  error: '', info: '', busy: '',
  resolved: null,  // 预览：按已保存的设置这次请求会带上什么（打码）
  handed: null,    // 「让 AI 读项目，帮我填」开的聊天 { sessionId, title }
  fixing: null,    // 「让 AI 来处理」交给了谁 { env, what, title, run }
  fresh: false,    // 文件在你改的时候变了
};

// ---------- 测试页的三块 ----------
function envTabs() {
  return [['sets', uiText('测试集管理', "Test sets")], ['manual', uiText('人工测试', "Manual testing")], ['env', uiText('测试环境', "Test environments")]].map(function (x) {
    return '<button type="button" data-tstab="' + x[0] + '" aria-pressed="' + (EV.tab === x[0]) + '">' + x[1] + '</button>';
  }).join('');
}
function showTestsTab(name) {
  if (name !== EV.tab && EV.tab === 'env' && !envLeaveOk()) return;
  EV.tab = name;
  $('ts-tabs').innerHTML = envTabs();
  $('ts-pane-sets').hidden = name !== 'sets'; $('ts-pane-manual').hidden = name !== 'manual'; $('ts-pane-env').hidden = name !== 'env';
  $('ts-runall').hidden = name !== 'sets'; $('ts-envsel').hidden = name !== 'sets';
  $('ts-where').textContent = name === 'env' ? uiText('人工测试和人工验收启动服务时用这里的设置；测试集运行时带上正在用的那套的环境变量', "Service startup uses these settings. Test runs receive environment variables from the active environment.") : name === 'manual' ? '' : $('ts-where').dataset.sets || '';
  if (name === 'env') loadEnvPage();
  if (name === 'sets') renderTests();
  if (name === 'manual') benchShow($('ts-pane-manual'), 'main', true); // 人工测试：和需求里的人工验收同一套界面，对着项目主目录（bench.js）
}
// 离开测试环境页（换一块、换一套环境、去别的页）之前：有没保存的改动先问
function envLeaveOk() {
  if (!EM.isDirty(EV.draft, EV.data)) return true;
  if (!window.confirm('「' + (EV.data.name || '') + uiText('」改了还没保存（', "” has unsaved changes (") + EM.changedSections(EV.draft, EV.data).join('、') + uiText('）。\n\n不保存就离开？改的会丢掉。', ").\n\nLeave and discard your changes?"))) return false;
  EV.draft = EM.draftFrom(EV.data);
  return true;
}

// ---------- 选择框 ----------
function envSelect() { return EM.selectHtml(EV.list); }
function loadEnvList() {
  return window.shell.envsList().then(function (list) {
    if (list && !list.error) EV.list = list;
    paintEnvSelects();
    return list;
  });
}
// 三处选择框都用同一个设置：一处换了，处处跟着变
function paintEnvSelects() {
  if ($('ts-envsel')) $('ts-envsel').innerHTML = envSelect();
  if (typeof panelTests === 'function') { var box = $('req-tests'); if (box) { box.tsHtml = null; panelTests(); } }
  if (typeof renderViewer === 'function' && $('ts-viewer').open) renderViewer();
  if (EV.tab === 'env' && !$('tests').hidden && EV.data) renderEnvPage();
}
function envSelChanged(select) {
  var id = select.value;
  select.disabled = true;
  window.shell.envsAct({ action: 'use', id: id }).then(function (res) {
    if (res && res.error) window.alert(uiText('没能换测试环境：', "Couldn't switch test environment: ") + res.error);
    loadEnvList().then(function () { if (EV.data) reloadEnv(); });
  });
}
function envGoSettings() {
  if ($('ts-viewer').open) closeViewer();
  view('tests');
  EV.cur = (EV.list && EV.list.use) || EV.cur;
  showTestsTab('env');
}

// ---------- 测试环境页 ----------
function loadEnvPage() {
  loadEnvList().then(function (list) {
    if (!list || list.error) { EV.error = (list && list.error) || uiText('没能读到测试环境。', "Couldn't load test environments."); return renderEnvPage(); }
    var ids = list.envs.map(function (e) { return e.id; });
    if (!EV.cur || ids.indexOf(EV.cur) < 0) EV.cur = list.use || ids[0] || null;
    if (!EV.cur) { EV.data = null; EV.draft = null; return renderEnvPage(); }
    if (EV.data && EV.data.id === EV.cur && EM.isDirty(EV.draft, EV.data)) return renderEnvPage(); // 你改着的不覆盖
    reloadEnv();
  });
}
function reloadEnv(keepDraft) {
  var want = EV.cur;
  return window.shell.envsShow({ id: want }).then(function (d) {
    if (want !== EV.cur) return;
    if (d && d.error) { EV.error = d.error; return renderEnvPage(); }
    var dirty = EM.isDirty(EV.draft, EV.data) && EV.data && EV.data.id === d.id;
    if (dirty && keepDraft && JSON.stringify(EM.draftFrom(d)) !== JSON.stringify(EM.draftFrom(EV.data))) EV.fresh = true; // 设置本身在别处变了（不只是检查结果、正在用哪一套）
    EV.data = d;
    if (!(dirty && keepDraft)) { EV.draft = EM.draftFrom(d); EV.fresh = false; }
    if (EV.user >= EV.draft.users.length) EV.user = 0;
    var cur = d.users.findIndex(function (u) { return u.current; });
    if (EV.user === 0 && cur > 0 && !EV.userPicked) EV.user = cur;
    renderEnvPage();
    loadResolve();
  });
}
function loadResolve() {
  if (!EV.data) return;
  var u = EV.draft.users[EV.user];
  window.shell.envsResolve({ id: EV.data.id, user: u && u.id }).then(function (r) { EV.resolved = r && !r.error ? r : null; var box = $('ev-preview'); if (box) box.innerHTML = previewHtml(); });
}

function renderEnvPage() {
  var box = $('ts-pane-env');
  if (!box || EV.tab !== 'env') return;
  var list = (EV.list && EV.list.envs) || [];
  var h = '<div class="ev"><div class="ev-side">';
  h += list.map(function (e) {
    return '<button type="button" class="ev-card" data-envpick="' + eesc(e.id) + '" aria-current="' + (e.id === EV.cur) + '"><b>' + eesc(e.name) + (e.in_use ? uiText(' <span class="ts-pill g">正在用</span>', " <span class=\"ts-pill g\">Active</span>") : '') + '</b>'
      + '<span class="small muted">' + eesc(e.note || uiText('（没写说明）', "(no description)")) + '</span></button>';
  }).join('');
  h += uiText('<div class="ev-sidebtns"><button type="button" class="sm" data-ev="new">＋ 新建一个环境</button>', "<div class=\"ev-sidebtns\"><button type=\"button\" class=\"sm\" data-ev=\"new\">＋ New environment</button>") + (EV.data ? uiText('<button type="button" class="sm" data-ev="copy">复制这个</button>', "<button type=\"button\" class=\"sm\" data-ev=\"copy\">Duplicate</button>") : '') + '</div>'
    + uiText('<p class="small muted">环境设置存在项目里（.relayforge/envs/，进仓库）；标了「保密」的值只存在这台电脑上，不进仓库。换一台电脑要重新填一次。</p></div>', "<p class=\"small muted\">Settings are stored in .relayforge/envs/ in Git. Secret values stay on this computer and must be entered again on another device.</p></div>");
  h += '<div class="ev-main">' + (EV.data ? envMainHtml() : '<div class="ev-empty"><p>' + (EV.error ? eesc(EV.error) : list.length ? uiText('正在读…', "Loading…") : uiText('还没有测试环境。点左边「新建一个环境」，起个名字（比如：本机、测试服）。', "No test environments yet. Select New environment and name it, for example Local or Staging.")) + '</p></div>') + '</div></div>';
  var scroll = box.closest('#tests') ? box.closest('#tests').scrollTop : 0;
  box.innerHTML = h;
  if (box.closest('#tests')) box.closest('#tests').scrollTop = scroll;
}

function sec(title, sub) { return '<div class="ev-sec"><h3>' + title + '</h3>' + (sub ? '<span>' + sub + '</span>' : '') + '</div>'; }
function input(path, value, opts) {
  opts = opts || {};
  return '<input' + (opts.cls ? ' class="' + opts.cls + '"' : '') + ' data-f="' + path + '" value="' + eesc(value) + '"' + (opts.ph ? ' placeholder="' + eesc(opts.ph) + '"' : '') + (opts.label ? ' aria-label="' + eesc(opts.label) + '"' : '') + '>';
}
// 保密的值：密码框，可以点一下显示
function secretInput(path, value, label, area) {
  var shown = !!EV.peek[path];
  var field = area && shown ? '<textarea class="mono" data-f="' + path + '" rows="3" spellcheck="false" aria-label="' + eesc(label) + '">' + eesc(value) + '</textarea>'
    : '<input class="mono" type="' + (shown ? 'text' : 'password') + '" data-f="' + path + '" value="' + eesc(value) + '" placeholder="' + (value ? '' : uiText('还没填', "Not set")) + '" aria-label="' + eesc(label) + '" autocomplete="off">';
  return '<span class="ev-pw">' + field + '<button type="button" class="sm quiet" data-peek="' + path + '">' + (shown ? uiText('隐藏', "Hide") : uiText('显示', "Show")) + '</button></span>';
}
function secretBox(path, on) { return '<label class="ev-secret"><input type="checkbox" data-f="' + path + '"' + (on ? ' checked' : '') + uiText('> 保密</label>', "> Secret</label>") + EM.secretPill(on); }
function delBtn(kind, i, extra) { return '<button type="button" class="sm quiet ev-x" data-del="' + kind + '" data-i="' + i + '"' + (extra || '') + uiText(' aria-label="删掉这一行" title="删掉这一行">×</button>', " aria-label=\"Remove row\" title=\"Remove row\">×</button>"); }

function envMainHtml() {
  var d = EV.data, f = EV.draft, dirty = EM.isDirty(f, d);
  var h = '<div class="ev-head"><input class="ev-name" data-f="name" value="' + eesc(f.name) + uiText('" aria-label="名称" placeholder="名称">', "\" aria-label=\"Name\" placeholder=\"Name\">")
    + (d.in_use ? uiText('<span class="ts-pill g">正在用</span>', "<span class=\"ts-pill g\">Active</span>") : uiText('<button type="button" class="sm primary" data-ev="use">用这个环境</button>', "<button type=\"button\" class=\"sm primary\" data-ev=\"use\">Use this environment</button>"))
    + '<span class="sp"></span><button type="button" class="sm" data-ev="ai"' + (EV.busy === 'ai' ? ' disabled' : '') + '>' + (EV.busy === 'ai' ? uiText('正在开聊天…', "Opening chat…") : uiText('让 AI 读项目，帮我填', "Ask AI to inspect the project")) + '</button>'
    + uiText('<button type="button" class="sm quiet" data-ev="rm">删除这套</button><button type="button" class="sm primary" id="ev-save-top" data-ev="save"', "<button type=\"button\" class=\"sm quiet\" data-ev=\"rm\">Delete environment</button><button type=\"button\" class=\"sm primary\" id=\"ev-save-top\" data-ev=\"save\"") + (dirty && EV.busy !== 'save' ? '' : ' disabled') + uiText('>保存</button></div>', ">Save</button></div>")
    + '<input class="ev-note" data-f="note" value="' + eesc(f.note) + uiText('" placeholder="一句说明，比如：服务和数据库都在这台电脑上" aria-label="说明">', "\" placeholder=\"Example: services and database are local\" aria-label=\"Description\">");
  if (EV.error) h += '<p class="ts-err" role="alert">' + eesc(EV.error) + '</p>';
  if (EV.info) h += '<p class="ev-info" role="status">' + eesc(EV.info) + '</p>';
  if (EV.fresh) h += uiText('<p class="ts-err">设置文件刚刚在别处变了。你改的还没保存；要看最新的，点「放弃修改」。</p>', "<p class=\"ts-err\">This environment was edited elsewhere. Your changes are unsaved. Select Discard changes to load the latest version.</p>");
  if (EV.handed && EV.handed.env === d.id) h += uiText('<div class="ev-handed">已经开了一个聊天「', "<div class=\"ev-handed\">Opened a chat “") + eesc(EV.handed.title) + uiText('」请 AI 读项目。它交回的建议会出现在这里，等你逐条采纳或忽略；密钥它不填，也看不到。 <button type="button" class="link" data-ev="goto">去那个聊天看</button></div>', "” to inspect the project. Review the suggestions here when they arrive. Secret values stay hidden. <button type=\"button\" class=\"link\" data-ev=\"goto\">Open chat</button></div>");
  h += proposalHtml(dirty);

  // 怎么启动
  h += sec(uiText('怎么启动', "Startup services"), uiText('启动在需求的「人工验收」和这里的「人工测试」里做，第一次执行（或命令、目录改了以后）会给你看实际的命令', "Start services from Manual acceptance or Manual testing. Review new or changed commands and working directories before running them."));
  h += uiText('<table class="ev-t"><thead><tr><th style="width:110px">服务</th><th>启动命令</th><th style="width:90px">端口</th><th style="width:150px">工作子目录（可不填）</th><th style="width:30px"></th></tr></thead><tbody>', "<table class=\"ev-t\"><thead><tr><th style=\"width:110px\">Service</th><th>Startup command</th><th style=\"width:90px\">Port</th><th style=\"width:150px\">Working directory (optional)</th><th style=\"width:30px\"></th></tr></thead><tbody>")
    + (f.services.length ? f.services.map(function (s, i) {
      return '<tr><td>' + input('services.' + i + '.name', s.name, { ph: uiText('后端', "Backend"), label: uiText('服务名称', "Service name") }) + '</td><td>' + input('services.' + i + '.command', s.command, { cls: 'mono', ph: 'mvn spring-boot:run', label: uiText('启动命令', "Startup command") }) + '</td>'
        + '<td>' + input('services.' + i + '.port', s.port, { cls: 'mono', ph: '8080', label: uiText('端口', "Port") }) + '</td><td>' + input('services.' + i + '.dir', s.dir, { cls: 'mono', ph: uiText('比如 web', "e.g. web"), label: uiText('工作子目录', "Working directory") }) + '</td><td>' + delBtn('services', i) + '</td></tr>'
        + '<tr class="ev-acc"><td></td><td colspan="4"><label class="small ev-auto"><input type="checkbox" data-f="services.' + i + '.auto_restart"' + (s.auto_restart !== false ? ' checked' : '') + uiText('> 代码变了自动重启</label>', "> Restart when code changes</label>")
        + uiText('<span class="small muted"> 运行中的服务，启动后代码改过：开发会话一轮停下时、打开人工验收标签时自动重启（不在 AI 改代码的中途）；关掉就只标出「运行的不是最新的」</span>', "<span class=\"small muted\"> Restart after the development session finishes a turn or when you open Manual acceptance. Turning this off flags stale services for you to restart.</span>")
        + uiText('<div class="small muted">人工验收时额外带的环境变量（让验收用的实例不碰真实的数据和项目；值里可以用 {{req}} 需求编号、{{worktree}} 工作目录、{{scratch}} 这次验收的临时目录、{{project}} 项目主目录、{{project_copy}} 项目的副本）</div>', "<div class=\"small muted\">Acceptance-only variables isolate the instance from live projects or data. Use {{req}} for the requirement ID, {{worktree}} for its directory, {{scratch}} for temporary files, {{project}} for the main checkout, or {{project_copy}} for a copy.</div>")
        + (s.accept || []).map(function (a, j) {
          return '<div class="ev-accrow">' + input('services.' + i + '.accept.' + j + '.name', a.name, { cls: 'mono', ph: 'DATA_DIR', label: uiText('变量名', "Variable name") }) + '<span>=</span>'
            + input('services.' + i + '.accept.' + j + '.value', a.value, { cls: 'mono', ph: '{{scratch}}/data', label: uiText('值', "Value") }) + delBtn('services.' + i + '.accept', j) + '</div>';
        }).join('') + '<button type="button" class="sm quiet" data-add="services.' + i + uiText('.accept">＋ 验收时带一个变量</button></td></tr>', ".accept\">＋ Add acceptance variable</button></td></tr>");
    }).join('') : uiText('<tr><td colspan="5" class="ev-none">还没填。可以点「让 AI 读项目，帮我填」。</td></tr>', "<tr><td colspan=\"5\" class=\"ev-none\">No services yet. You can ask AI to inspect the project.</td></tr>")) + '</tbody></table>'
    + uiText('<div class="ev-acts"><button type="button" class="sm" data-add="services">＋ 加一个服务</button></div>', "<div class=\"ev-acts\"><button type=\"button\" class=\"sm\" data-add=\"services\">＋ Add service</button></div>");

  h += prepHtml(d, f, dirty);

  // 外部依赖
  var rows = EM.depRows(d, f), cnt = EM.depCounts(rows), cands = d.config_candidates || [];
  h += sec(uiText('外部依赖', "External dependencies"), uiText('这个项目要连哪些数据库和外部服务', "Databases and external services this project connects to"));
  h += uiText('<div class="ev-acts"><span class="small muted">用哪个配置文件</span><select class="mono" data-f="config" aria-label="用哪个配置文件"><option value=""', "<div class=\"ev-acts\"><span class=\"small muted\">Configuration file</span><select class=\"mono\" data-f=\"config\" aria-label=\"Configuration file\"><option value=\"\"") + (f.config ? '' : ' selected') + uiText('>（不用配置文件）</option>', ">No configuration file</option>")
    + cands.concat(f.config && cands.indexOf(f.config) < 0 ? [f.config] : []).map(function (c) { return '<option' + (c === f.config ? ' selected' : '') + '>' + eesc(c) + '</option>'; }).join('') + '</select>'
    + '<button type="button" class="sm" data-ev="scan"' + (EV.busy || !d.config ? ' disabled' : dirty && f.config !== d.config ? uiText(' disabled title="换了配置文件，先保存"', " disabled title=\"Save the new configuration file selection first\"") : '') + '>' + (EV.busy === 'scan' ? uiText('正在读…', "Reading…") : uiText('重新读配置', "Reread configuration")) + '</button>'
    + '<button type="button" class="sm" data-ev="check"' + (EV.busy || !rows.length ? ' disabled' : '') + '>' + (EV.busy === 'check' ? uiText('正在检查…', "Checking…") : uiText('检查能不能连上', "Check connectivity")) + '</button>'
    + (rows.length && cnt.unchecked < rows.length ? '<span class="small muted">' + cnt.ok + uiText(' 个能连上，', " reachable, ") + cnt.bad + uiText(' 个连不上，', " unreachable, ") + cnt.none + uiText(' 个没配</span>', " not configured</span>") : '')
    + (cnt.bad && !dirty ? uiText('<button type="button" class="sm ts-btn" data-handle="check">让 AI 来处理</button>', "<button type=\"button\" class=\"sm ts-btn\" data-handle=\"check\">Ask AI to investigate</button>") : '') + '</div>';
  if (EV.fixing && EV.fixing.env === d.id && EV.fixing.what === 'check') h += fixingHtml();
  h += '<p class="small muted ev-scan">' + eesc(EM.scanLine(d.scan)) + '</p>';
  if (d.scan && (d.scan.skipped || []).length) h += uiText('<details class="ev-skip"><summary class="small">跳过的写法（', "<details class=\"ev-skip\"><summary class=\"small\">Unsupported entries (") + d.scan.skipped.length + uiText(' 处）</summary><ul>', ")</summary><ul>") + d.scan.skipped.map(function (s) { return uiText('<li class="small">第 ', "<li class=\"small\">Line ") + s.line + uiText(' 行：', ": ") + eesc(s.why) + '</li>'; }).join('') + '</ul></details>';
  h += uiText('<table class="ev-t"><thead><tr><th style="width:190px">依赖</th><th style="width:90px">类型</th><th>地址</th><th style="width:100px">来自</th><th style="width:90px">状态</th><th style="width:60px"></th></tr></thead><tbody>', "<table class=\"ev-t\"><thead><tr><th style=\"width:190px\">Dependency</th><th style=\"width:90px\">Type</th><th>Address</th><th style=\"width:100px\">Source</th><th style=\"width:90px\">Status</th><th style=\"width:60px\"></th></tr></thead><tbody>")
    + (rows.length ? rows.map(function (r) {
      if (r.edit >= 0) {
        var i = r.edit;
        return '<tr class="mine"><td>' + input('deps.' + i + '.name', EV.draft.deps[i].name || r.name, { ph: uiText('支付服务', "Payment service"), label: uiText('依赖名称', "Dependency name") }) + '</td><td class="muted small">' + eesc(r.type || '—') + '</td>'
          + '<td>' + input('deps.' + i + '.address', r.address, { cls: 'mono', ph: 'http://localhost:9090', label: uiText('地址', "Address") }) + uiText('</td><td class="small">在这里改的', "</td><td class=\"small\">Manual") + (r.replaces ? uiText('<span class="sub">盖过配置文件</span>', "<span class=\"sub\">Overrides configuration</span>") : '') + '</td>'
          + '<td>' + (EM.isDirty(EV.draft, EV.data) && !r.check ? uiText('<span class="ts-pill">保存后再查</span>', "<span class=\"ts-pill\">Save before checking</span>") : EM.statusPill(r.check)) + '</td><td>' + delBtn('deps', i, uiText(' title="不再盖过配置文件"', " title=\"Remove override\"")) + '</td></tr>';
      }
      return '<tr><td>' + eesc(r.name) + '<span class="sub mono">' + eesc(r.key) + (r.line ? uiText(' · 第 ', " · Line ") + r.line + uiText(' 行', "") : '') + '</span></td><td class="muted">' + eesc(r.type) + '</td>'
        + '<td class="mono">' + eesc(r.address || uiText('（没配）', "(not configured)")) + (r.note ? '<span class="sub">' + eesc(r.note) + '</span>' : '') + uiText('</td><td class="small muted">配置文件</td><td>', "</td><td class=\"small muted\">Config file</td><td>") + EM.statusPill(r.check) + '</td>'
        + '<td><button type="button" class="sm quiet" data-override="' + eesc(r.key) + uiText('" title="只在这套环境里换掉这个地址，配置文件不动">改</button></td></tr>', "\" title=\"Override the address for this environment\">Edit</button></td></tr>");
    }).join('') : '<tr><td colspan="6" class="ev-none">' + (f.config ? uiText('这个配置文件里没认出数据库和外部服务的地址。', "No database or external service addresses were found in this file.") : uiText('还没选配置文件。', "Select a configuration file first.")) + '</td></tr>') + '</tbody></table>'
    + uiText('<div class="ev-acts"><button type="button" class="sm" data-add="deps">＋ 手动加一个</button></div>', "<div class=\"ev-acts\"><button type=\"button\" class=\"sm\" data-add=\"deps\">＋ Add manually</button></div>")
    + uiText('<p class="small muted">地址默认从你项目里的配置文件读，不用在这里再填一遍；配置文件照旧由你自己维护。检查只做 TCP 连接，不登录、不发请求、不写数据。想临时换掉某一项，点「改」，或者在下面的环境变量里覆盖。</p>', "<p class=\"small muted\">Addresses are read from your project's config file. The check opens a TCP connection only. To override an address in this environment, select Edit or set an environment variable.</p>");

  // 环境变量
  h += sec(uiText('环境变量', "Environment variables"), uiText('运行测试集、启动服务时额外带上的，会盖过配置文件里的同名项', "Passed to tests and services, overriding matching settings from the config file"));
  h += uiText('<table class="ev-t"><thead><tr><th style="width:200px">名称</th><th>值</th><th style="width:150px">保密</th><th style="width:30px"></th></tr></thead><tbody>', "<table class=\"ev-t\"><thead><tr><th style=\"width:200px\">Name</th><th>Value</th><th style=\"width:150px\">Secret</th><th style=\"width:30px\"></th></tr></thead><tbody>")
    + (f.vars.length ? f.vars.map(function (v, i) {
      return '<tr><td>' + input('vars.' + i + '.name', v.name, { cls: 'mono', ph: 'DB_PASSWORD', label: uiText('变量名', "Variable name") }) + '</td><td>' + (v.secret ? secretInput('vars.' + i + '.value', v.value, uiText('值', "Value")) : input('vars.' + i + '.value', v.value, { cls: 'mono', label: uiText('值', "Value") })) + '</td>'
        + '<td>' + secretBox('vars.' + i + '.secret', v.secret) + '</td><td>' + delBtn('vars', i) + '</td></tr>';
    }).join('') : uiText('<tr><td colspan="4" class="ev-none">还没有。</td></tr>', "<tr><td colspan=\"4\" class=\"ev-none\">No variables yet.</td></tr>")) + '</tbody></table>'
    + uiText('<div class="ev-acts"><button type="button" class="sm" data-add="vars">＋ 加一个</button></div>', "<div class=\"ev-acts\"><button type=\"button\" class=\"sm\" data-add=\"vars\">＋ Add variable</button></div>")
    + '<div class="ev-aisec"><label><input type="checkbox" data-aisec="1"' + (d.ai_secrets ? ' checked' : '') + (EV.busy ? ' disabled' : '') + uiText('> 允许 AI 的会话运行时带上保密值</label>', "> Allow Agent sessions to run with secrets</label>")
    + uiText('<p class="small muted">默认不勾：AI 的会话（需求的开发会话、修复会话、「让 AI 来处理」的聊天）里运行测试集和准备步骤时，只带上面不保密的变量；你在应用里点的运行才带保密值。', "<p class=\"small muted\">By default, Agent sessions run test sets and preparation steps with non-secret variables. Runs you start from the app can use secrets. ")
    + uiText('<b class="bad">勾上的风险：AI 写的代码（测试代码、准备步骤里的命令）能读到这些值</b>，它可能把值打印出来或写进文件。只对这台电脑、这套环境有效，随时可以取消。</p></div>', "<b class=\"bad\">If enabled, Agent-written test code and preparation commands can read, print, or write these values.</b> This setting applies only to this environment on this computer.</p></div>")
    + uiText('<p class="small muted">保密的值在测试结果和输出里会换成 ', "<p class=\"small muted\">Secrets are shown as ") + eesc(EM.MASK) + uiText('；只替换 3 个字以上的值（太短的替换会把正常内容也打码），所以 1、2 个字的保密值不会被打码。</p>', " in test output. Values shorter than three characters are not redacted.</p>");

  // 请求的通用设置
  var user = f.users[EV.user];
  h += sec(uiText('请求的通用设置', "Request defaults"), uiText('人工测试、人工验收里发的每个请求都会带上', "Included in requests made during manual testing and acceptance"));
  h += uiText('<table class="ev-t"><thead><tr><th style="width:110px">放在哪</th><th style="width:190px">名称</th><th>值</th><th style="width:150px">保密</th><th style="width:30px"></th></tr></thead><tbody>', "<table class=\"ev-t\"><thead><tr><th style=\"width:110px\">Location</th><th style=\"width:190px\">Name</th><th>Value</th><th style=\"width:150px\">Secret</th><th style=\"width:30px\"></th></tr></thead><tbody>")
    + (f.request.items.length ? f.request.items.map(function (r, i) {
      var refs = EM.refsOf(r.value, f, user);
      return '<tr><td><select data-f="request.items.' + i + uiText('.in" aria-label="放在哪"><option value="header"', ".in\" aria-label=\"Location\"><option value=\"header\"") + (r.in === 'header' ? ' selected' : '') + uiText('>请求头</option><option value="query"', ">Header</option><option value=\"query\"") + (r.in === 'query' ? ' selected' : '') + uiText('>地址参数</option></select></td>', ">Query parameter</option></select></td>")
        + '<td>' + input('request.items.' + i + '.name', r.name, { cls: 'mono', ph: 'Authorization', label: uiText('名称', "Name") }) + '</td>'
        + '<td>' + (r.secret ? secretInput('request.items.' + i + '.value', r.value, uiText('值', "Value")) : input('request.items.' + i + '.value', r.value, { cls: 'mono', ph: 'Bearer {{user.jwt}}', label: uiText('值', "Value") }))
        + '<span class="sub ev-refs" data-refs="' + i + '">' + refsText(refs) + '</span></td>'
        + '<td>' + secretBox('request.items.' + i + '.secret', r.secret) + '</td><td>' + delBtn('request.items', i) + '</td></tr>';
    }).join('') : uiText('<tr><td colspan="5" class="ev-none">还没有。</td></tr>', "<tr><td colspan=\"5\" class=\"ev-none\">No request defaults yet.</td></tr>")) + '</tbody></table>'
    + uiText('<div class="ev-acts"><button type="button" class="sm" data-add="request.items">＋ 加一个</button><span class="small muted">值里可以用 {{变量名}} 引用环境变量，用 {{user.名称}} 引用当前选的测试用户的值：换一个用户，这些请求头就跟着变</span></div>', "<div class=\"ev-acts\"><button type=\"button\" class=\"sm\" data-add=\"request.items\">＋ Add</button><span class=\"small muted\">Use {{VARIABLE}} for an environment variable or {{user.name}} for a field on the selected test user.</span></div>")
    + uiText('<details class="ev-preview"><summary class="small">预览：这次请求会带上什么（按已保存的设置）</summary><div id="ev-preview">', "<details class=\"ev-preview\"><summary class=\"small\">Preview request values using saved settings</summary><div id=\"ev-preview\">") + previewHtml() + '</div></details>';
  h += uiText('<div class="ev-io"><div><h4>发请求之前运行的脚本（JS）</h4><textarea class="mono" data-f="request.before" spellcheck="false" placeholder="// 例：没有 token 就先登录拿一个">', "<div class=\"ev-io\"><div><h4>Before request script (JS)</h4><textarea class=\"mono\" data-f=\"request.before\" spellcheck=\"false\" placeholder=\"// Example: sign in if no token is available\">") + eesc(f.request.before) + '</textarea></div>'
    + uiText('<div><h4>收到返回之后运行的脚本（JS）</h4><textarea class="mono" data-f="request.after" spellcheck="false" placeholder="// 例：登录过期了就清掉 token">', "<div><h4>After response script (JS)</h4><textarea class=\"mono\" data-f=\"request.after\" spellcheck=\"false\" placeholder=\"// Example: clear an expired token\">") + eesc(f.request.after) + '</textarea></div></div>'
    + uiText('<p class="small muted">脚本这一步只保存，还不会执行：下一步做「人工测试」时，发请求之前和收到返回之后才运行它们。</p>', "<p class=\"small muted\">These scripts are saved here. Manual testing will run them before requests and after responses.</p>");

  // 测试用户
  h += sec(uiText('测试用户', "Test users"), uiText('发请求时选「以谁的身份」；每个用户带着自己的一组值，请求头里用 {{user.名称}} 引用', "Select the identity to use for requests. Reference its fields with {{user.name}}."));
  h += '<div class="ev-users"><div>' + f.users.map(function (u, i) {
    var saved = (d.users || []).filter(function (x) { return x.id === u.id; })[0];
    return '<button type="button" class="ev-card" data-userpick="' + i + '" aria-current="' + (i === EV.user) + '"><b>' + eesc(u.name || uiText('（没起名）', "(unnamed)")) + (saved && saved.current ? uiText(' <span class="ts-pill b">当前</span>', " <span class=\"ts-pill b\">Current</span>") : '') + '</b><span class="small muted">' + eesc(u.note || '') + '</span></button>';
  }).join('') + uiText('<button type="button" class="sm" data-add="users">＋ 加一个用户</button></div>', "<button type=\"button\" class=\"sm\" data-add=\"users\">＋ Add user</button></div>");
  h += '<div class="ev-box">' + (user ? userHtml(user, EV.user, d) : uiText('<p class="muted">还没有测试用户。</p>', "<p class=\"muted\">No test users yet.</p>")) + '</div></div>';

  h += uiText('<div class="ev-warn"><b>提醒</b>：测试会真的读写这个环境连着的数据库。不要把正式环境的地址填在这里。</div>', "<div class=\"ev-warn\"><b>Test runs read and write the configured database.</b> Use a test database.</div>");
  h += '<div class="ev-bar' + (dirty ? ' on' : '') + '" id="ev-bar"' + (dirty ? '' : ' hidden') + '>' + barHtml(dirty) + '</div>';
  return h;
}
function barHtml(dirty) {
  return dirty ? uiText('<span>改了还没保存：', "<span>Unsaved changes: ") + eesc(EM.changedSections(EV.draft, EV.data).join('、')) + uiText('</span><span class="sp"></span><button type="button" class="sm quiet" data-ev="discard">放弃修改</button>', "</span><span class=\"sp\"></span><button type=\"button\" class=\"sm quiet\" data-ev=\"discard\">Discard changes</button>")
    + '<button type="button" class="sm primary" data-ev="save"' + (EV.busy === 'save' ? ' disabled' : '') + '>' + (EV.busy === 'save' ? uiText('正在保存…', "Saving…") : uiText('保存', "Save")) + '</button>'
    : '';
}
function refsText(refs) {
  if (!refs.refs.length) return '';
  return refs.missing.length ? uiText('<span class="bad">找不到：', "<span class=\"bad\">Missing: ") + eesc(refs.missing.map(function (x) { return '{{' + x + '}}'; }).join('、')) + '</span>' : uiText('引用了 ', "References ") + eesc(refs.refs.join('、'));
}
function previewHtml() {
  var r = EV.resolved;
  if (!r) return uiText('<p class="small muted">保存以后这里显示代进去的结果。</p>', "<p class=\"small muted\">Save to preview the substituted values.</p>");
  var rows = r.headers.concat(r.query);
  if (!rows.length) return uiText('<p class="small muted">还没有通用的请求头和地址参数。</p>', "<p class=\"small muted\">No default headers or query parameters.</p>");
  return uiText('<p class="small muted">以「', "<p class=\"small muted\">Using “") + eesc(r.user ? r.user.name : uiText('（没有测试用户）', "(no test user)")) + uiText('」的身份；保密的打码。</p><table class="ev-t"><tbody>', "”. Secrets are redacted.</p><table class=\"ev-t\"><tbody>") + rows.map(function (x) {
    return '<tr><td class="mono" style="width:190px">' + eesc(x.name) + '</td><td style="width:90px" class="small muted">' + eesc(x.where) + '</td><td class="mono">' + eesc(x.value)
      + (x.missing.length ? uiText(' <span class="ts-pill r">找不到 ', " <span class=\"ts-pill r\">Missing ") + eesc(x.missing.join('、')) + '</span>' : '') + '</td></tr>';
  }).join('') + '</tbody></table>';
}
function userHtml(u, i, d) {
  var saved = (d.users || []).filter(function (x) { return x.id === u.id; })[0];
  var h = '<div class="ev-uhead">' + input('users.' + i + '.name', u.name, { cls: 'ev-uname', ph: uiText('测试用户 A', "Test user A"), label: uiText('用户名称', "User name") }) + input('users.' + i + '.note', u.note, { ph: uiText('一句说明，比如：普通用户', "Example: standard user"), label: uiText('用户说明', "User description") })
    + (saved ? (saved.current ? uiText('<span class="ts-pill b">当前用户</span>', "<span class=\"ts-pill b\">Current user</span>") : uiText('<button type="button" class="sm" data-ev="curuser">设为当前用户</button>', "<button type=\"button\" class=\"sm\" data-ev=\"curuser\">Set as current</button>")) : uiText('<span class="small muted">保存后才能设为当前用户</span>', "<span class=\"small muted\">Save before setting as current</span>"))
    + '<button type="button" class="sm quiet" data-del="users" data-i="' + i + uiText('">删掉这个用户</button></div>', "\">Remove user</button></div>");
  h += uiText('<table class="ev-t"><thead><tr><th style="width:150px">名称（自己起）</th><th>值</th><th style="width:150px">保密</th><th style="width:30px"></th></tr></thead><tbody>', "<table class=\"ev-t\"><thead><tr><th style=\"width:150px\">Field name</th><th>Value</th><th style=\"width:150px\">Secret</th><th style=\"width:30px\"></th></tr></thead><tbody>") + u.fields.map(function (fd, j) {
    var p = 'users.' + i + '.fields.' + j, isJwt = fd.key === 'jwt' || EM.looksJwt(fd.value);
    var box = fd.secret ? secretInput(p + '.value', fd.value, uiText('值', "Value"), isJwt) : isJwt ? '<textarea class="mono" data-f="' + p + uiText('.value" rows="3" spellcheck="false" aria-label="值">', ".value\" rows=\"3\" spellcheck=\"false\" aria-label=\"Value\">") + eesc(fd.value) + '</textarea>' : input(p + '.value', fd.value, { cls: 'mono', label: uiText('值', "Value") });
    return '<tr><td>' + input(p + '.key', fd.key, { cls: 'mono', ph: 'uid', label: uiText('名称', "Name") }) + '</td><td>' + box + (isJwt ? '<div class="ev-jwt" data-jwt="' + p + '">' + jwtHtml(fd.value) + '</div>' : '') + '</td>'
      + '<td>' + secretBox(p + '.secret', fd.secret) + '</td><td>' + delBtn('users.' + i + '.fields', j) + '</td></tr>';
  }).join('') + '</tbody></table>';
  h += '<div class="ev-acts"><button type="button" class="sm" data-add="users.' + i + uiText('.fields">＋ 加一项</button><span class="small muted">名称随便起：jwt、uid、aid、talent_id、tenant_id 都行；「请求的通用设置」里用 {{user.名称}} 引用。</span></div>', ".fields\">＋ Add field</button><span class=\"small muted\">Use names such as jwt, uid, aid, talent_id, or tenant_id, then reference them as {{user.name}} in request defaults.</span></div>");
  return h;
}
function jwtHtml(value) {
  if (!String(value || '').trim()) return uiText('<span class="muted">粘贴 JWT 以后，这里显示解出来的内容和过期时间。</span>', "<span class=\"muted\">Paste a JWT to see its decoded payload and expiration.</span>");
  var s = EM.jwtSummary(EM.jwtInfo(value));
  if (!s) return uiText('<span class="muted">不像 JWT（应该是用点隔开的三段）。</span>', "<span class=\"muted\">A JWT must contain three parts separated by dots.</span>");
  if (!s.fields) return '<span class="bad">' + eesc(s.text) + '</span>';
  return uiText('解出来的内容：', "Decoded payload: ") + eesc(s.text || uiText('（载荷是空的）', "(empty payload)")) + ' · <span class="' + (s.state === 'bad' ? 'bad' : s.state === 'ok' ? 'good' : '') + '">' + eesc(s.exp.text) + uiText('</span> <span class="muted">（只解码，不验签）</span>', "</span> <span class=\"muted\">(signature not verified)</span>");
}
function proposalHtml(dirty) {
  var p = EV.data.proposal, items = EM.proposalItems(p);
  if (!items.length) return '';
  return uiText('<div class="ev-ai"><div class="ev-aihead"><b>AI 的建议</b><span class="small muted">', "<div class=\"ev-ai\"><div class=\"ev-aihead\"><b>AI suggestions</b><span class=\"small muted\">") + eesc(String(p.at || '').replace('T', ' ').slice(5, 16)) + (p.summary ? ' · ' + eesc(p.summary) : '') + '</span><span class="sp"></span>'
    + '<button type="button" class="sm" data-adopt="all"' + (dirty ? uiText(' disabled title="先保存或放弃你改的，再采纳"', " disabled title=\"Save or discard your changes first\"") : '') + uiText('>全部采纳</button><button type="button" class="sm quiet" data-ignore="all">全部忽略</button></div>', ">Apply all</button><button type=\"button\" class=\"sm quiet\" data-ignore=\"all\">Dismiss all</button></div>")
    + items.map(function (i) {
      return '<div class="ev-airow"><span class="ts-pill b">' + eesc(i.kind) + '</span><span class="mono">' + eesc(i.text) + '</span>' + (i.why ? '<span class="small muted">' + eesc(i.why) + '</span>' : '') + (i.note ? '<span class="small ev-ainote">' + eesc(i.note) + '</span>' : '') + '<span class="sp"></span>'
        + '<button type="button" class="sm primary" data-adopt="' + i.k + '"' + (dirty ? uiText(' disabled title="先保存或放弃你改的，再采纳"', " disabled title=\"Save or discard your changes first\"") : '') + uiText('>采纳</button><button type="button" class="sm quiet" data-ignore="', ">Apply</button><button type=\"button\" class=\"sm quiet\" data-ignore=\"") + i.k + uiText('">忽略</button></div>', "\">Dismiss</button></div>");
    }).join('') + uiText('<p class="small muted">采纳的直接写进这套环境的设置；密钥 AI 不填，也看不到，标了保密的变量由你自己填值。</p></div>', "<p class=\"small muted\">Applied suggestions update this environment. Enter secret values yourself.</p></div>");
}

// ---------- 准备步骤 ----------
function prepHtml(d, f, dirty) {
  var saved = {}; (d.prep || []).forEach(function (x) { saved[x.id] = x; });
  var h = sec(uiText('准备步骤', "Preparation steps"), uiText('运行测试集之前程序按顺序执行（装依赖、编译……）；第一次执行某条命令、或命令改了以后，先给你看命令和执行目录，你点同意才执行', "Run commands in order before tests, such as installing dependencies or compiling. Review and approve new or changed commands and directories."));
  h += uiText('<table class="ev-t ev-prep"><thead><tr><th style="width:26px">#</th><th style="width:130px">说明</th><th>命令</th><th style="width:110px">执行目录</th><th style="width:230px">什么时候执行</th><th style="width:70px">超时（秒）</th><th style="width:150px">状态</th><th style="width:30px"></th></tr></thead><tbody>', "<table class=\"ev-t ev-prep\"><thead><tr><th style=\"width:26px\">#</th><th style=\"width:130px\">Description</th><th>Command</th><th style=\"width:110px\">Working directory</th><th style=\"width:230px\">When to run</th><th style=\"width:70px\">Timeout (s)</th><th style=\"width:150px\">Status</th><th style=\"width:30px\"></th></tr></thead><tbody>")
    + (f.prep.length ? f.prep.map(function (x, i) {
      var sv = saved[x.id], same = sv && sv.command === x.command && (sv.dir || '') === x.dir, st = EM.prepState(same ? sv : null);
      var row = '<tr><td class="muted">' + (i + 1) + '</td><td>' + input('prep.' + i + '.note', x.note, { ph: uiText('装依赖', "Install dependencies"), label: uiText('说明', "Description") }) + '</td>'
        + '<td>' + input('prep.' + i + '.command', x.command, { cls: 'mono', ph: 'npm ci', label: uiText('命令', "Command") }) + '</td>'
        + '<td>' + input('prep.' + i + '.dir', x.dir, { cls: 'mono', ph: uiText('项目根目录', "Project root"), label: uiText('执行目录', "Working directory") }) + '</td>'
        + '<td><select data-f="prep.' + i + uiText('.when" aria-label="什么时候执行"><option value="always"', ".when\" aria-label=\"When to run\"><option value=\"always\"") + (x.when === 'always' ? ' selected' : '') + uiText('>每次运行前</option><option value="changed"', ">Before each run</option><option value=\"changed\"") + (x.when === 'changed' ? ' selected' : '') + uiText('>这些文件变了以后</option></select>', ">When selected files change</option></select>")
        + (x.when === 'changed' ? input('prep.' + i + '.watch', x.watch, { cls: 'mono ev-watch', ph: 'pom.xml, web/package-lock.json', label: uiText('看哪些文件', "Files to watch") }) : '') + '</td>'
        + '<td>' + input('prep.' + i + '.timeout', x.timeout, { cls: 'mono', ph: '600', label: uiText('超时', "Timeout") }) + '</td>'
        + '<td><span class="ts-pill ' + st.cls + '"' + (same && sv.last ? ' title="' + eesc(String(sv.last.at || '').replace('T', ' ')) + '"' : '') + '>' + eesc(st.text) + '</span>'
        + (same && !dirty ? ' <button type="button" class="sm" data-prep-run="' + eesc(x.id) + '"' + (EV.busy ? ' disabled' : '') + '>' + (EV.busy === 'prep:' + x.id ? uiText('正在执行…', "Running…") : uiText('执行一遍', "Run now")) + '</button>' : '') + '</td>'
        + '<td>' + delBtn('prep', i) + '</td></tr>';
      if (same && sv.last && !sv.last.ok) {
        row += '<tr class="ev-prepfail"><td></td><td colspan="7"><div class="small">' + eesc(String(sv.last.at || '').replace('T', ' ').slice(5, 16)) + uiText(' 在 <span class="mono">', " failed in <span class=\"mono\">") + eesc(sv.last.place || '') + uiText('</span> 执行失败，输出的最后部分：</div>', "</span>. Last output:</div>")
          + '<pre class="mono">' + eesc(sv.last.tail || uiText('（没有输出）', "(no output)")) + '</pre>'
          + (sv.last.withheld && sv.last.withheld.length ? uiText('<div class="small bad">这次没有带保密值（', "<div class=\"small bad\">Secrets were omitted (") + eesc(sv.last.withheld.join('、')) + uiText('）：在应用里运行，或在下面的环境变量里允许 AI 的会话带上保密值。</div>', "). Run from the app or enable secrets for Agent sessions in Environment variables.</div>") : '')
          + '<div class="ev-acts"><button type="button" class="sm ts-btn" data-handle="prep" data-step="' + eesc(x.id) + uiText('">让 AI 来处理</button></div>', "\">Ask AI to investigate</button></div>")
          + (EV.fixing && EV.fixing.env === d.id && EV.fixing.what === 'prep:' + x.id ? fixingHtml() : '') + '</td></tr>';
      }
      return row;
    }).join('') : uiText('<tr><td colspan="8" class="ev-none">还没有。比如：装依赖（npm ci，package-lock.json 变了才执行）、编译（mvn -q compile）。测试集跑不起来时，可以让 AI 试出来交回。</td></tr>', "<tr><td colspan=\"8\" class=\"ev-none\">No preparation steps yet. Examples: npm ci when package-lock.json changes, or mvn -q compile before every run.</td></tr>")) + '</tbody></table>'
    + uiText('<div class="ev-acts"><button type="button" class="sm" data-add="prep">＋ 加一步</button>', "<div class=\"ev-acts\"><button type=\"button\" class=\"sm\" data-add=\"prep\">＋ Add step</button>")
    + (d.prep && d.prep.length && !dirty ? '<button type="button" class="sm" data-prep-run="all"' + (EV.busy ? ' disabled' : '') + '>' + (EV.busy === 'prep:all' ? uiText('正在执行…', "Running…") : uiText('全部执行一遍', "Run all steps")) + '</button>' : '')
    + uiText('<span class="small muted">执行情况和输出只记在这台电脑上，不进仓库。命令里不要写保密值，用环境变量。</span></div>', "<span class=\"small muted\">Results and output stay local. Pass secrets through environment variables.</span></div>");
  return h;
}
function fixingHtml() {
  var x = EV.fixing;
  return uiText('<div class="ev-handed">已经交给', "<div class=\"ev-handed\">Sent to ") + eesc(x.title) + uiText('。它试出来的做法会出现在上面的「AI 的建议」里，等你采纳；处理完以后点「执行一遍」或回到测试集「再跑一次」。', ". Review its suggestions above, then run the step or test set again. ")
    + uiText(' <button type="button" class="link" data-ev="gofix">去那个会话看</button></div>', " <button type=\"button\" class=\"link\" data-ev=\"gofix\">Open session</button></div>");
}
// 执行一遍（一条或全部）：还没同意的先给你看命令
function envPrepRun(which) {
  var steps = (EV.data.prep || []).filter(function (x) { return which === 'all' || x.id === which; });
  var todo = steps.filter(function (x) { return !x.approved; });
  var go = function () {
    envBusy('prep:' + which, window.shell.envsPrepRun({ id: EV.cur, steps: which === 'all' ? [] : [which] }), function (res) {
      EV.info = res.error ? '' : uiText('执行完了：', "Finished: ") + res.ran.map(function (r) { return r.note + (r.ok ? uiText(' 成功', " succeeded") : uiText(' 失败', " failed")); }).join('，') + '。';
      if (res.error) EV.error = res.error.split('\n')[0];
      reloadEnv(true);
    });
  };
  if (!todo.length) return go();
  envApproveDialog({ id: EV.data.id, name: EV.data.name }, todo, go);
}
// 第一次执行前的确认：看过命令和执行目录，点同意（rf.py env approve，只认应用的标记）再执行。取消就什么都不做
// 测试集的构建命令（kind 'build'）也在这里同意：rf.py test approve-build，带上你看到的那条命令的指纹，命令在你看过以后变了就不算
function envApproveDialog(env, steps, then, cancel) {
  var d = $('ts-dlg');
  env = env || {};
  var preps = steps.filter(function (x) { return x.kind !== 'build'; }), builds = steps.filter(function (x) { return x.kind === 'build'; });
  d.innerHTML = EM.approvalHtml(steps, env.name) + uiText('<p class="ts-err" id="ts-dlg-err" hidden></p><div class="ts-row"><button type="button" class="quiet" data-x="no">不同意，不执行</button><button type="button" class="primary" data-x="yes">同意并执行</button></div>', "<p class=\"ts-err\" id=\"ts-dlg-err\" hidden></p><div class=\"ts-row\"><button type=\"button\" class=\"quiet\" data-x=\"no\">Decline</button><button type=\"button\" class=\"primary\" data-x=\"yes\">Approve and run</button></div>");
  d.onclick = function (e) {
    var b = e.target.closest('button'); if (!b) return;
    if (b.dataset.x === 'no') { d.close(); if (cancel) cancel(); return; }
    if (b.dataset.x !== 'yes') return;
    b.disabled = true;
    var first = preps.length ? window.shell.envsAct({ action: 'approve', id: env.id, steps: preps.map(function (x) { return x.id; }) }) : Promise.resolve({});
    builds.reduce(function (done, x) {
      return done.then(function (res) { return res && res.error ? res : window.shell.testsAct({ action: 'approve-build', id: x.set, print: x.print, dir: x.abs_dir }); });
    }, first).then(function (res) {
      b.disabled = false;
      if (!res || res.error) { $('ts-dlg-err').textContent = (res && res.error) || uiText('没能记下同意。', "Couldn't save approval."); $('ts-dlg-err').hidden = false; return; }
      d.close(); then();
    });
  };
  d.showModal();
}
// 「让 AI 来处理」：准备步骤失败（prep）、依赖连不上（check）
function envHandle(what, step) {
  envBusy('handle', window.shell.envsHandle({ kind: what, ref: EV.cur, step: step }), function (res) {
    if (res.kind === 'chat') tsChatInBackground(res.sessionId, res.title, res.say);
    EV.fixing = { env: EV.cur, what: what === 'prep' ? 'prep:' + step : 'check', title: res.kind === 'chat' ? uiText('新开的聊天「', "New chat “") + res.title + '」' : res.title, run: res.run };
    renderEnvPage();
  });
}

// ---------- 编辑 ----------
function evPath(path) { return path.split('.').map(function (k) { return /^\d+$/.test(k) ? Number(k) : k; }); }
function evSet(path, value) {
  var keys = evPath(path), o = EV.draft;
  for (var i = 0; i < keys.length - 1; i++) o = o[keys[i]];
  o[keys[keys.length - 1]] = value;
}
function evGet(path) { var o = EV.draft; evPath(path).forEach(function (k) { o = o == null ? o : o[k]; }); return o; }
function evInput(t) {
  if (!t.dataset.f || !EV.draft) return;
  evSet(t.dataset.f, t.type === 'checkbox' ? t.checked : t.value);
  // 保存条只在内容变了时重画：输入框失去焦点时也会来一次 change，这时重画会把你正要点的「保存」换掉，点击就落空了
  var bar = $('ev-bar'), dirty = EM.isDirty(EV.draft, EV.data), html = barHtml(dirty);
  if (bar && bar.evHtml !== html) { bar.className = 'ev-bar' + (dirty ? ' on' : ''); bar.hidden = !dirty; bar.innerHTML = html; bar.evHtml = html; }
  if ($('ev-save-top')) $('ev-save-top').disabled = !dirty || EV.busy === 'save';
  var m = t.dataset.f.match(/^(users\.\d+\.fields\.\d+)\.value$/);
  if (m) { var j = document.querySelector('[data-jwt="' + m[1] + '"]'); if (j) j.innerHTML = jwtHtml(t.value); }
  var r = t.dataset.f.match(/^request\.items\.(\d+)\.value$/);
  if (r) { var s = document.querySelector('[data-refs="' + r[1] + '"]'); if (s) s.innerHTML = refsText(EM.refsOf(t.value, EV.draft, EV.draft.users[EV.user])); }
}
function envClick(e) {
  var b = e.target.closest('button');
  if (!b) return;
  var ds = b.dataset;
  if (ds.tstab) return showTestsTab(ds.tstab);
  if (EV.tab !== 'env') return;
  if (ds.envpick) { if (ds.envpick === EV.cur || !envLeaveOk()) return; EV.cur = ds.envpick; EV.user = 0; EV.userPicked = false; EV.peek = {}; EV.error = ''; EV.info = ''; EV.handed = null; EV.data = null; renderEnvPage(); return reloadEnv(); }
  if (ds.userpick) { EV.user = Number(ds.userpick); EV.userPicked = true; renderEnvPage(); return loadResolve(); }
  if (ds.peek) { EV.peek[ds.peek] = !EV.peek[ds.peek]; return renderEnvPage(); }
  if (ds.add) { var arr = evGet(ds.add), kind = { services: 'service', vars: 'var', 'request.items': 'req', deps: 'dep', users: 'user', prep: 'prep' }[ds.add] || (/\.accept$/.test(ds.add) ? 'acc' : 'field'); arr.push(EM.blankRow(kind)); if (kind === 'user') EV.user = arr.length - 1; return renderEnvPage(); }
  if (ds.del) { evGet(ds.del).splice(Number(ds.i), 1); if (ds.del === 'users' && EV.user >= EV.draft.users.length) EV.user = Math.max(0, EV.draft.users.length - 1); return renderEnvPage(); }
  if (ds.override) { var base = EM.depRows(EV.data, EV.draft).filter(function (r) { return r.key === ds.override; })[0]; EV.draft.deps.push({ key: ds.override, name: '', type: '', address: base ? base.address : '' }); return renderEnvPage(); } // 名称和类型跟着配置文件，只换地址
  if (ds.adopt || ds.ignore) return envDecide(ds.adopt ? 'adopt' : 'ignore', ds.adopt || ds.ignore);
  if (ds.prepRun) return envPrepRun(ds.prepRun);
  if (ds.handle) return envHandle(ds.handle, ds.step);
  var v = ds.ev;
  if (v === 'new') return envNewDialog();
  if (v === 'copy') return envLeaveOk() && envBusy('copy', window.shell.envsAct({ action: 'copy', id: EV.cur }), function (res) { EV.cur = res.id; EV.data = null; EV.info = uiText('复制好了：「', "Duplicated “") + res.name + uiText('」。保密的值也复制了（都在这台电脑上）。改个名字再保存。', "” with its local secrets. Rename it before saving."); loadEnvPage(); });
  if (v === 'use') return envBusy('use', window.shell.envsAct({ action: 'use', id: EV.cur }), function () { loadEnvList().then(function () { reloadEnv(true); }); });
  if (v === 'rm') {
    if (!window.confirm(uiText('删除测试环境「', "Delete test environment “") + EV.data.name + uiText('」？\n\n设置文件移到 .relayforge/history/envs/，不会真的删掉；这台电脑上的保密值保留。', "”?\n\nIts settings move to .relayforge/history/envs/. Local secrets are kept."))) return;
    return envBusy('rm', window.shell.envsAct({ action: 'rm', id: EV.cur }), function () { EV.cur = null; EV.data = null; EV.draft = null; EV.info = ''; loadEnvPage(); });
  }
  if (v === 'save') return envSave();
  if (v === 'discard') { EV.draft = EM.draftFrom(EV.data); EV.fresh = false; EV.error = ''; if (EV.user >= EV.draft.users.length) EV.user = 0; return reloadEnv(); }
  if (v === 'scan') return envBusy('scan', window.shell.envsScan({ id: EV.cur }), function (res) { EV.info = res.scan && res.scan.error ? '' : uiText('读好了：认出 ', "Loaded configuration and found ") + res.deps.length + uiText(' 个依赖。', " dependencies."); reloadEnv(true); });
  if (v === 'check') return envBusy('check', window.shell.envsCheck({ id: EV.cur }), function (res) { EV.info = uiText('检查完了：', "Check complete: ") + res.counts.ok + uiText(' 个能连上，', " reachable, ") + res.counts.bad + uiText(' 个连不上，', " unreachable, ") + res.counts.none + uiText(' 个没配。', " not configured."); reloadEnv(true); });
  if (v === 'curuser') { var u = EV.draft.users[EV.user]; return envBusy('user', window.shell.envsAct({ action: 'user', id: EV.cur, user: u.id }), function () { reloadEnv(true); }); }
  if (v === 'ai') {
    return envBusy('ai', window.shell.envsAi({ id: EV.cur }), function (res) { EV.handed = { env: EV.cur, sessionId: res.sessionId, title: res.title }; tsChatInBackground(res.sessionId, res.title, res.say); renderEnvPage(); });
  }
  if (v === 'goto' && EV.handed) { if (!envLeaveOk()) return; view('chats'); select('session:' + EV.handed.sessionId); }
  if (v === 'gofix' && EV.fixing) { if (!envLeaveOk()) return; tsGoTo(EV.fixing.run); }
}
function envBusy(what, promise, then) {
  EV.busy = what; EV.error = ''; EV.info = ''; renderEnvPage();
  return promise.then(function (res) {
    EV.busy = '';
    if (!res || res.error) { EV.error = (res && res.error) || uiText('没能完成。', "Couldn't complete the operation."); renderEnvPage(); return; }
    then(res);
  });
}
function envSave() {
  var d = EV.draft;
  if (!d.name.trim()) { EV.error = uiText('给这套环境起个名字。', "Enter a name for this environment."); return renderEnvPage(); }
  envBusy('save', window.shell.envsSave({ id: EV.cur, payload: EM.payloadOf(d) }), function (res) {
    EV.data = res; EV.draft = EM.draftFrom(res); EV.fresh = false; EV.info = uiText('保存好了。保密的值只存在这台电脑上。', "Saved. Secret values stay on this computer.");
    renderEnvPage(); loadEnvList(); loadResolve();
  });
}
function envDecide(action, which) {
  var ks = which === 'all' ? [] : [Number(which)];
  envBusy(action, window.shell.envsAct({ action: action, id: EV.cur, ks: ks }), function (res) {
    EV.data = res; EV.draft = EM.draftFrom(res); EV.info = action === 'adopt' ? uiText('采纳了，已经写进设置。', "Applied to the environment.") : uiText('忽略了。', "Dismissed."); renderEnvPage(); loadResolve();
  });
}
// 新建：起个名字（Electron 里没有 prompt()，用测试页的小对话框）
function envNewDialog() {
  if (!envLeaveOk()) return;
  var d = $('ts-dlg');
  d.innerHTML = uiText('<h2>新建一个测试环境</h2><p>起个名字，比如：本机、测试服。建好以后再填启动命令、配置文件、环境变量和测试用户；也可以从现有的环境「复制这个」再改。</p>', "<h2>New test environment</h2><p>Name the environment, for example Local or Staging. Then configure its services, configuration file, variables, and test users.</p>")
    + uiText('<label>名称<input id="ev-new-name" type="text" placeholder="本机"></label><label>一句说明（可以不写）<input id="ev-new-note" type="text" placeholder="服务和数据库都在这台电脑上"></label>', "<label>Name<input id=\"ev-new-name\" type=\"text\" placeholder=\"Local\"></label><label>Description (optional)<input id=\"ev-new-note\" type=\"text\" placeholder=\"Services and database run locally\"></label>")
    + uiText('<p class="ts-err" id="ts-dlg-err" hidden></p><div class="ts-row"><button type="button" class="quiet" data-x="no">取消</button><button type="button" class="primary" data-x="yes">新建</button></div>', "<p class=\"ts-err\" id=\"ts-dlg-err\" hidden></p><div class=\"ts-row\"><button type=\"button\" class=\"quiet\" data-x=\"no\">Cancel</button><button type=\"button\" class=\"primary\" data-x=\"yes\">Create</button></div>");
  d.onclick = function (e) {
    var b = e.target.closest('button'); if (!b) return;
    if (b.dataset.x === 'no') return d.close();
    if (b.dataset.x !== 'yes') return;
    b.disabled = true;
    window.shell.envsAct({ action: 'new', name: $('ev-new-name').value, note: $('ev-new-note').value }).then(function (res) {
      b.disabled = false;
      if (!res || res.error) { $('ts-dlg-err').textContent = (res && res.error) || uiText('没能新建。', "Couldn't create the environment."); $('ts-dlg-err').hidden = false; return; }
      d.close(); EV.cur = res.id; EV.data = null; EV.info = uiText('建好了。', "Environment created."); loadEnvPage();
    });
  };
  d.showModal(); $('ev-new-name').focus();
}

// 绑定事件：由 main.js 按顺序调用
function wireEnvs() {
  $('ts-tabs').innerHTML = envTabs();
  var page = $('tests');
  page.addEventListener('click', envClick);
  page.addEventListener('input', function (e) { if (e.target.closest('#ts-pane-env')) evInput(e.target); });
  page.addEventListener('change', function (e) {
    if (e.target.dataset.envuse) return envSelChanged(e.target);
    if (e.target.dataset.aisec) { // 不进编辑稿：点了就生效（只记在这台电脑上）
      var on = e.target.checked;
      if (on && !window.confirm(uiText('允许 AI 的会话运行测试集和准备步骤时带上「', "Let Agent sessions use secrets from “") + EV.data.name + uiText('」的保密值？\n\nAI 写的代码（测试代码、准备步骤里的命令）能读到这些值，可能把它们打印出来或写进文件。只对这台电脑有效，随时可以取消。', "” when running tests and preparation steps?\n\nAgent-written code can read and may print or save these values. This setting applies only on this computer and can be turned off."))) { e.target.checked = false; return; }
      return envBusy('aisec', window.shell.envsAct({ action: 'ai-secrets', id: EV.cur, on: on }), function (res) { EV.info = res.ai_secrets ? uiText('已允许：AI 的会话运行时带上保密值。', "Agent sessions can now run with secrets.") : uiText('已取消：AI 的会话运行时不带保密值。', "Agent sessions will run without secrets."); reloadEnv(true); });
    }
    if (!e.target.closest('#ts-pane-env')) return;
    evInput(e.target);
    if (e.target.type === 'checkbox' || e.target.tagName === 'SELECT') renderEnvPage(); // 保密切换成密码框、换了放在哪
  });
  // 各处的选择框（测试页、查看器、需求面板）：换了就是换当前环境；「设置」跳到测试环境页
  document.addEventListener('change', function (e) { if (e.target.dataset && e.target.dataset.envuse && !e.target.closest('#tests')) envSelChanged(e.target); });
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('[data-envgo]'); if (b) { e.preventDefault(); e.stopPropagation(); envGoSettings(); } }, true);
  window.shell.onEnvsChanged(function () {
    loadEnvList();
    if (EV.tab === 'env' && !$('tests').hidden && EV.cur) reloadEnv(true);
  });
  loadEnvList();
}
