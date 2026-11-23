// 人工验收（需求下的「人工验收」标签）和人工测试（测试页的一块）：同一套界面，前者对着需求的工作目录，后者对着项目主目录（主线）。
// 上面是服务的状态（启动、停止、重启、打开），下面是日志。进程由主进程 services.js 直接管，不经过模型；这里只显示和转按钮。
// 启动前要你同意的命令（启动命令、准备步骤）弹出和准备步骤一样的确认框，同意走 rf.py env approve（只认应用的标记）。
var BM = BenchModel, besc = BM.esc;
var BENCH = {
  plan: {},    // 范围 -> rf.py env plan 的结果（打码的）
  run: {},     // 范围 -> 主进程给的服务状态 { services: [...] }
  logs: {},    // 范围 -> 日志行（已打码）
  histOpen: {}, // 范围|服务 -> 启动记录展开了
  filter: {},  // 范围 -> 只看哪个服务（空的是全部）
  copy: {},    // 范围 -> 项目副本：reuse 沿用上次的 | fresh 重新复制
  busy: {}, error: {}, info: {},
  fixing: {},  // 范围|服务 -> 交给了谁（「让 AI 来处理」）
  box: {},     // 范围 -> 现在显示它的容器
  envs: null,  // rf.py env list：「跑起来让我验收」要不要换成「去人工验收」
  said: '',    // 人工测试「发现不对」正在写的话
};
var benchScopeOf = function (box) { return Object.keys(BENCH.box).filter(function (k) { return BENCH.box[k] === box; })[0]; };

// 打开一个范围：先用缓存画，再从主进程读状态和计划
function benchShow(box, scope, free) {
  Object.keys(BENCH.box).forEach(function (k) { if (BENCH.box[k] === box && k !== scope) delete BENCH.box[k]; });
  BENCH.box[scope] = box; box.dataset.free = free ? '1' : '';
  benchRender(scope);
  var loaded = benchLoad(scope);
  // 打开人工验收（或人工测试）：运行的不是最新代码、又开着自动重启的服务，现在就重启（主进程判断）
  window.shell.svcFresh({ scope: scope }).then(function (res) { if (res && res.results && res.results.length) return benchLoad(scope); }).catch(function () {});
  return loaded;
}
function benchLoad(scope) {
  return Promise.all([window.shell.svcView({ scope: scope }), window.shell.svcPlan({ scope: scope })]).then(function (got) {
    var v = got[0] || {}, p = got[1] || {};
    if (!v.error && (v.rev || 0) >= ((BENCH.run[scope] || {}).rev || 0)) { BENCH.run[scope] = v; BENCH.logs[scope] = v.logs || []; } // 回答比手里的旧：不用
    BENCH.plan[scope] = p;
    benchRender(scope);
  });
}

function benchRender(scope) {
  var box = BENCH.box[scope];
  if (!box) return;
  var free = box.dataset.free === '1', p = BENCH.plan[scope], run = (BENCH.run[scope] || {}).services || [];
  var h = '<div class="bn-top"><b>' + (free ? uiText('人工测试 · 对着主线（项目主目录）', "Manual testing · Main checkout") : uiText('人工验收 · 用这个需求的代码', "Manual acceptance · Requirement checkout")) + '</b>';
  if (p && p.place) h += uiText('<span class="small muted mono" title="代码目录">', "<span class=\"small muted mono\" title=\"Code directory\">") + besc(p.place) + '</span>';
  h += '<span class="spacer"></span>';
  if (p && p.env) h += uiText('<span class="small muted">测试环境「', "<span class=\"small muted\">Test environment “") + besc(p.env.name) + uiText('」</span><button type="button" class="link small" data-envgo="1">设置</button>', "”</span><button type=\"button\" class=\"link small\" data-envgo=\"1\">Settings</button>");
  h += '</div>';
  if (!p) { box.innerHTML = h + uiText('<p class="muted bn-empty">正在读测试环境…</p>', "<p class=\"muted bn-empty\">Loading test environment…</p>"); return; }
  if (p.error) { box.innerHTML = h + '<p class="bn-err">' + besc(p.error) + '</p>'; return; }
  var rows = BM.rows(p, run, (BENCH.run[scope] || {}).histories || p.histories);
  if (!p.env || !rows.length) {
    box.innerHTML = h + '<div class="bn-empty"><p>' + besc(p.why || uiText('测试环境里还没有服务。', "This test environment has no services.")) + uiText('</p><p class="small muted">「怎么启动」里每个服务写名称、启动命令、端口（没有就空着，比如桌面应用）、子目录；', "</p><p class=\"small muted\">Set a name, startup command, optional port, and working directory for each service. ")
      + (free ? '' : uiText('人工验收时程序在这个需求的工作目录里执行它，', "During acceptance, RelayForge runs services from the requirement's worktree. ")) + uiText('还可以写「验收时额外带的环境变量」，让验收用的实例不碰真实的数据。</p>', "Add acceptance environment variables to isolate the test instance from real data.</p>")
      + uiText('<div class="bn-acts"><button type="button" data-envgo="1">去测试环境里配</button>', "<div class=\"bn-acts\"><button type=\"button\" data-envgo=\"1\">Configure test environment</button>") + (p.env ? uiText('<button type="button" data-bn="aifill">让 AI 读项目，帮我填</button>', "<button type=\"button\" data-bn=\"aifill\">Ask AI to inspect the project</button>") : '') + '</div></div>';
    return;
  }
  var any = rows.some(function (r) { return BM.live(r.run); }), busy = BENCH.busy[scope];
  h += '<div class="bn-acts">';
  h += '<button type="button" class="primary" data-bn="startall"' + (busy ? ' disabled' : '') + '>' + (busy === 'start' ? uiText('正在启动…', "Starting…") : uiText('全部启动', "Start all")) + '</button>';
  h += '<button type="button" data-bn="stopall"' + (any && !busy ? '' : ' disabled') + uiText('>全部停止</button>', ">Stop all</button>");
  if (BM.usesCopy(p)) {
    var info = p.copy && p.copy.info, mode = BENCH.copy[scope] || 'reuse';
    h += uiText('<span class="bn-copy small"><span class="muted">项目副本：</span><label><input type="radio" name="bn-copy-', "<span class=\"bn-copy small\"><span class=\"muted\">Project copy:</span><label><input type=\"radio\" name=\"bn-copy-") + besc(scope) + '" value="reuse"' + (mode === 'reuse' ? ' checked' : '') + ' data-bncopy="1">'
      + (info ? uiText('沿用上次的（', "Reuse last copy (") + besc(String(info.at || '').replace('T', ' ').slice(5, 16)) + uiText(' 复制）', ")") : uiText('还没有，启动时复制', "No copy yet. Create one at startup.")) + '</label>'
      + '<label><input type="radio" name="bn-copy-' + besc(scope) + '" value="fresh"' + (mode === 'fresh' ? ' checked' : '') + uiText(' data-bncopy="1">重新复制</label></span>', " data-bncopy=\"1\">Make a fresh copy</label></span>");
  }
  h += '</div>';
  if (BENCH.error[scope]) h += '<p class="bn-err" role="alert">' + besc(BENCH.error[scope]) + '</p>';
  if (BENCH.info[scope]) h += '<p class="bn-info" role="status">' + besc(BENCH.info[scope]) + '</p>';
  if ((p.withheld || []).length) h += uiText('<p class="small muted">保密的环境变量（', "<p class=\"small muted\">Secret environment variables (") + besc(p.withheld.join('、')) + uiText('）在你点「启动」时才带上，这里看不到值。</p>', ") are supplied only when you start the services. Their values are hidden here.</p>");
  // 服务
  h += '<div class="bn-svcs">' + rows.map(function (r) {
    var st = BM.statusOf(r.run), pl = r.plan || {}, live = BM.live(r.run), name = besc(r.name);
    var s = '<div class="bn-svc ' + (r.run ? r.run.status : 'idle') + '"><div class="bn-row"><b>' + name + '</b><span class="ts-pill ' + st.cls + '">' + besc(st.text) + '</span>'
      + '<span class="mono small muted bn-cmd" title="' + besc(pl.cwd || (r.run && r.run.cwd) || '') + '">' + besc(pl.command || (r.run && r.run.command) || '') + '</span><span class="spacer"></span>';
    s += live ? '<button type="button" class="sm" data-bn="stop" data-svc="' + name + '"' + (r.run.status === 'stopping' ? ' disabled' : '') + uiText('>停止</button><button type="button" class="sm" data-bn="restart" data-svc="', ">Stop</button><button type=\"button\" class=\"sm\" data-bn=\"restart\" data-svc=\"") + name + '"' + (busy ? ' disabled' : '') + uiText('>重启</button>', ">Restart</button>")
      : '<button type="button" class="sm" data-bn="start" data-svc="' + name + '"' + (busy || pl.problem || !r.plan ? ' disabled' : '') + uiText('>启动</button>', ">Start</button>");
    if (r.run && r.run.status === 'running' && (pl.port || r.run.port)) s += '<button type="button" class="sm" data-bn="open" data-port="' + besc(pl.port || r.run.port) + uiText('" title="用系统浏览器打开 http://localhost:', "\" title=\"Open in your browser: http://localhost:") + besc(pl.port || r.run.port) + uiText('">打开</button>', "\">Open</button>");
    s += '</div>';
    if (BM.isStale(r.run)) {
      s += uiText('<div class="bn-stale" role="status"><b>启动后代码改过，运行的不是最新的</b><span class="small">启动时是 <span class="mono">', "<div class=\"bn-stale\" role=\"status\"><b>Code changed since startup</b><span class=\"small\">Started from <span class=\"mono\">") + besc(r.run.head || uiText('（不知道）', "(unknown)")) + '</span>' + (r.run.dirty ? uiText('（当时就有未提交的改动）', " (had uncommitted changes)") : '') + '。' + besc(BM.staleNote(r.run, pl)) + '</span>'
        + '<button type="button" class="sm" data-bn="restart" data-svc="' + name + '"' + (busy ? ' disabled' : '') + uiText('>重启</button></div>', ">Restart</button></div>");
    }
    var lines = BM.planLines(pl);
    s += uiText('<div class="small muted bn-where">在 <span class="mono">', "<div class=\"small muted bn-where\">Directory: <span class=\"mono\">") + besc(pl.cwd || (r.run && r.run.cwd) || '') + '</span>' + (pl.port ? uiText(' · 端口 ', " · Port ") + besc(pl.port) + uiText('（连上才算运行中）', " (ready when reachable)") : uiText(' · 没配端口，进程活着就算运行中', " · No port check. Ready while the process is running.")) + (pl.approved === false ? uiText(' · 启动命令还没经你同意，第一次启动时会问', " · Review and approve the startup command the first time you run it.") : '') + '</div>';
    if (lines.length) s += '<dl class="bn-plan">' + lines.map(function (x) { return '<dt>' + besc(x.k) + '</dt><dd class="' + (x.mono ? 'mono' : '') + (x.missing ? ' bad' : '') + '">' + besc(x.v) + '</dd>'; }).join('') + '</dl>';
    if (pl.problem) s += '<p class="bn-err small">' + besc(pl.problem) + '</p>';
    if (r.run && r.run.status === 'failed') {
      var fx = BENCH.fixing[scope + '|' + r.name];
      s += '<div class="bn-fail"><span>' + besc(r.run.reason || uiText('启动失败。', "Startup failed.")) + '</span>' + (fx ? uiText('<span class="small">已经交给', "<span class=\"small\">Sent to ") + besc(fx) + uiText('：它试出来的做法会作为建议出现在测试环境里，等你采纳以后再点「启动」。</span>', ". Review the suggested fix in the test environment, then start the services again.</span>")
        : '<button type="button" class="sm" data-bn="handle" data-svc="' + name + uiText('">让 AI 来处理</button>', "\">Ask AI to investigate</button>")) + '</div>';
    }
    var hkey = scope + '|' + r.name, open = !!BENCH.histOpen[hkey], hl = r.hist || [];
    s += '<div class="bn-hist"><button type="button" class="link small" data-bn="hist" data-svc="' + name + '" aria-expanded="' + open + '">' + (open ? '▾' : '▸') + uiText(' 启动记录', " startup history") + (hl.length ? '（' + hl.length + uiText(' 条）', ")") : '') + '</button>'
      + (open ? '' : '<span class="small muted">' + (hl.length ? besc(BM.histLast(hl)) : uiText('还没有记录', "No history yet")) + '</span>');
    if (open) s += hl.length ? '<ol class="bn-hl">' + hl.slice().reverse().map(function (x) { return '<li class="small mono">' + besc(BM.histLine(x)) + '</li>'; }).join('') + '</ol>' : uiText('<p class="small muted">还没有记录。</p>', "<p class=\"small muted\">No history yet.</p>");
    s += '</div>';
    return s + '</div>';
  }).join('') + '</div>';
  // 日志
  var f = BENCH.filter[scope] || '', names = rows.map(function (r) { return r.name; });
  h += uiText('<div class="bn-log"><div class="bn-lh"><b>日志</b><span class="small muted">标准输出和标准错误按时间排在一起，保密值打了码；同时写到本机的日志文件</span><span class="spacer"></span>', "<div class=\"bn-log\"><div class=\"bn-lh\"><b>Logs</b><span class=\"small muted\">Chronological output with secrets redacted. A copy is saved locally.</span><span class=\"spacer\"></span>")
    + uiText('<select class="sm" data-bnfilter="1" aria-label="只看哪个服务"><option value="">全部服务</option>', "<select class=\"sm\" data-bnfilter=\"1\" aria-label=\"Filter by service\"><option value=\"\">All services</option>") + names.concat([uiText('准备', "Prepare")]).map(function (n) { return '<option value="' + besc(n) + '"' + (n === f ? ' selected' : '') + '>' + besc(n) + '</option>'; }).join('') + '</select>'
    + uiText('<button type="button" class="sm quiet" data-bn="clear">清空</button></div><div class="bn-ll" title="', "<button type=\"button\" class=\"sm quiet\" data-bn=\"clear\">Clear</button></div><div class=\"bn-ll\" title=\"") + besc(p.log || '') + '">'
    + BM.filter(BENCH.logs[scope], f).slice(-600).map(BM.lineHtml).join('') + '</div></div>';
  if (free) {
    h += uiText('<div class="bn-issue"><b>发现不对</b><p class="small muted">写一句哪里不对，程序连同当时的日志（最后几十行，保密值打码）记成一个问题，在「问题」页里接着处理。</p>', "<div class=\"bn-issue\"><b>Report a problem</b><p class=\"small muted\">Describe the problem. RelayForge will attach the latest log lines with secrets redacted. Follow up on the Issues page.</p>")
      + uiText('<textarea data-bnsay="1" rows="2" placeholder="一句话说哪里不对">', "<textarea data-bnsay=\"1\" rows=\"2\" placeholder=\"Describe the problem in one sentence\">") + besc(BENCH.said) + '</textarea><div class="bn-acts"><button type="button" class="primary sm" data-bn="issue"' + (busy === 'issue' ? ' disabled' : '') + uiText('>连同日志记成问题</button></div></div>', ">Report issue with logs</button></div></div>");
  }
  box.innerHTML = h;
  var ll = box.querySelector('.bn-ll'); if (ll) ll.scrollTop = ll.scrollHeight;
}

// 启动（全部，或一个）：有没同意的命令先问你；同意以后再启动一次
function benchStart(scope, names, action) {
  BENCH.busy[scope] = 'start'; BENCH.error[scope] = ''; BENCH.info[scope] = ''; benchRender(scope);
  var copy = BENCH.copy[scope] || 'reuse';
  // 重启：主进程先停（不单独记一条停止）再启动，记成一条「重启」
  return (action === uiText('重启', "Restart") ? window.shell.svcRestart({ scope: scope, name: names[0], copy: copy }) : window.shell.svcStart({ scope: scope, names: names || null, copy: copy })).then(function (res) {
    BENCH.busy[scope] = '';
    res = res || {};
    if (res.plan) BENCH.plan[scope] = Object.assign({}, res.plan, { running: undefined });
    if (res.needs) { benchRender(scope); return benchAsk(scope, res.needs, res.plan, function () { return benchStart(scope, names, action); }); }
    if (res.error) BENCH.error[scope] = res.error;
    else if (res.problems && res.problems.length) BENCH.error[scope] = res.problems.join('\n');
    if (res.ok && BENCH.copy[scope] === 'fresh') BENCH.copy[scope] = 'reuse'; // 重新复制只这一次
    return benchLoad(scope);
  });
}
function benchAsk(scope, needs, plan, then) {
  var d = $('ts-dlg');
  d.innerHTML = BM.needsHtml(needs, plan) + uiText('<p class="ts-err" id="ts-dlg-err" hidden></p><div class="ts-row"><button type="button" class="quiet" data-x="no">不同意，不启动</button><button type="button" class="primary" data-x="yes">同意并启动</button></div>', "<p class=\"ts-err\" id=\"ts-dlg-err\" hidden></p><div class=\"ts-row\"><button type=\"button\" class=\"quiet\" data-x=\"no\">Decline</button><button type=\"button\" class=\"primary\" data-x=\"yes\">Approve and start</button></div>");
  d.onclick = function (e) {
    var b = e.target.closest('button'); if (!b) return;
    if (b.dataset.x === 'no') { d.close(); BENCH.info[scope] = uiText('没有启动：你没有同意。', "Startup cancelled because approval was declined."); benchRender(scope); return; }
    if (b.dataset.x !== 'yes') return;
    b.disabled = true;
    window.shell.envsAct({ action: 'approve', id: plan.env.id, steps: needs.prep || [], services: needs.services || [] }).then(function (res) {
      b.disabled = false;
      if (!res || res.error) { $('ts-dlg-err').textContent = (res && res.error) || uiText('没能记下同意。', "Couldn't save approval."); $('ts-dlg-err').hidden = false; return; }
      d.close(); then();
    });
  };
  d.showModal();
}
function benchClick(e) {
  var b = e.target.closest('[data-bn]'); if (!b) return;
  var box = e.target.closest('[data-bench]'), scope = box && benchScopeOf(box);
  if (!scope) return;
  var act = b.dataset.bn, name = b.dataset.svc, p = BENCH.plan[scope] || {};
  if (act === 'startall') return benchStart(scope, null);
  if (act === 'start') return benchStart(scope, [name]);
  if (act === 'stopall' || act === 'stop') { return window.shell.svcStop({ scope: scope, name: act === 'stop' ? name : null }).then(function () { return benchLoad(scope); }); }
  if (act === 'hist') { var hk = scope + '|' + name; BENCH.histOpen[hk] = !BENCH.histOpen[hk]; return benchRender(scope); }
  if (act === 'restart') return benchStart(scope, [name], uiText('重启', "Restart"));
  if (act === 'open') return window.shell.svcOpen({ port: b.dataset.port });
  if (act === 'clear') {
    var only = BENCH.filter[scope] || '';
    BENCH.logs[scope] = only ? (BENCH.logs[scope] || []).filter(function (l) { return l.s !== only; }) : [];
    window.shell.svcClear({ scope: scope, name: only || null });
    return benchRender(scope);
  }
  if (act === 'aifill' && p.env) {
    return window.shell.envsAi({ id: p.env.id }).then(function (res) {
      if (res.error) { BENCH.error[scope] = res.error; return benchRender(scope); }
      tsChatInBackground(res.sessionId, res.title, res.say);
      BENCH.info[scope] = uiText('已经开了一个聊天「', "Opened a chat “") + res.title + uiText('」请 AI 读项目；它的建议会出现在测试环境里，等你采纳。', "” to inspect the project. Review its suggestions in the test environment."); benchRender(scope);
    });
  }
  if (act === 'handle' && p.env) {
    b.disabled = true;
    return window.shell.envsHandle({ kind: 'start', ref: p.env.id, service: name, req: scope === 'main' ? undefined : scope }).then(function (res) {
      if (res.error) { BENCH.error[scope] = res.error; return benchRender(scope); }
      if (res.kind === 'chat') tsChatInBackground(res.sessionId, res.title, res.say);
      BENCH.fixing[scope + '|' + name] = res.kind === 'chat' ? uiText('新开的聊天「', "New chat “") + res.title + '」' : res.title;
      benchRender(scope);
    });
  }
  if (act === 'issue') {
    var said = String(BENCH.said || '').trim();
    if (!said) { BENCH.error[scope] = uiText('先写一句哪里不对。', "Describe the problem first."); return benchRender(scope); }
    BENCH.busy[scope] = 'issue'; benchRender(scope);
    return window.shell.acceptIssue({ scope: scope, text: said }).then(function (res) {
      BENCH.busy[scope] = '';
      if (res && res.error) BENCH.error[scope] = res.error;
      else { BENCH.said = ''; BENCH.info[scope] = uiText('记成了问题', "Reported issue ") + (res && res.num ? ' ' + String(res.num).replace(/^#*/, '#') : '') + uiText('，附上了当时的日志。到「问题」页里接着处理。', " with logs attached. Follow up on the Issues page."); }
      benchRender(scope);
    });
  }
}
function benchChange(e) {
  var box = e.target.closest('[data-bench]'), scope = box && benchScopeOf(box);
  if (!scope) return;
  if (e.target.dataset.bnfilter) { BENCH.filter[scope] = e.target.value; return benchRender(scope); }
  if (e.target.dataset.bncopy) { BENCH.copy[scope] = e.target.value; }
}

// 需求下的「人工验收」标签：中间整块（终端藏起来，会话不断）
function benchForReq() {
  var r = tab === 'reqs' ? byId(current) : null, on = !!(r && shown[r.id] === 'accept' && hasAcceptTab(r));
  $('bench').hidden = !on;
  if (on) {
    if (BENCH.box[r.id] !== $('bench') || !$('bench').innerHTML) benchShow($('bench'), r.id, false);
  } else Object.keys(BENCH.box).forEach(function (k) { if (BENCH.box[k] === $('bench')) delete BENCH.box[k]; });
  return on;
}
// 人工验收标签：开发中的需求都有（到了待验收自然要用；之前的阶段也可以先点开试）
var hasAcceptTab = function (r) { return !!(r && r.state === 'open' && r.worktree); };

// 「跑起来让我验收」：测试环境里配了服务的，换成「去人工验收」（切到标签并全部启动）
function benchRunButton() {
  var list = BENCH.envs, use = list && (list.envs || []).filter(function (e) { return e.id === list.use; })[0];
  var b = BM.runButton(use);
  $('run-it').textContent = b.label; $('run-it').title = b.title; $('run-it').dataset.bench = b.bench ? '1' : '';
  $('run-hint').hidden = b.bench; // 没配服务：提示可以去配
}
function benchLoadEnvs() { return window.shell.envsList().then(function (l) { BENCH.envs = l && !l.error ? l : null; benchRunButton(); }); }

// 右边面板：一条验收条件的「通过」「这里不对」（待验收时）
function acceptBox(li, r, c) {
  var m = BM.markOf(c), box = li.appendChild(el('div', 'ck-acc ' + m.state));
  if (m.text) box.appendChild(el('span', 'ck-mark', m.text));
  var writing = acceptBox.writing === r.id + '#' + c.n;
  if (writing) {
    var ta = box.appendChild(el('textarea')); ta.rows = 2; ta.placeholder = uiText('哪里不对？一句话就行', "What went wrong? One sentence is enough."); ta.value = acceptBox.draft || '';
    ta.addEventListener('input', function () { acceptBox.draft = ta.value; });
    var send = box.appendChild(el('button', 'sm primary', uiText('连同当时的日志交回开发会话', "Send to development with logs"))); send.type = 'button';
    var no = box.appendChild(el('button', 'sm quiet', uiText('取消', "Cancel"))); no.type = 'button';
    send.addEventListener('click', function () { acceptSend(r, c, ta.value, send); });
    no.addEventListener('click', function () { acceptBox.writing = null; panel(); });
    setTimeout(function () { ta.focus(); }, 0);
    return;
  }
  if (m.state !== 'ok') { var ok = box.appendChild(el('button', 'sm', uiText('通过', "Pass"))); ok.type = 'button'; ok.addEventListener('click', function () { acceptMark(r, c, 'ok', '', ok); }); }
  var bad = box.appendChild(el('button', 'sm', uiText('这里不对', "Something is wrong"))); bad.type = 'button';
  bad.addEventListener('click', function () { acceptBox.writing = r.id + '#' + c.n; acceptBox.draft = ''; panel(); });
}
function acceptMark(r, c, result, say, button) {
  if (button) button.disabled = true;
  return window.shell.acceptMark({ req: r.id, n: c.n, result: result, say: say }).then(function (res) {
    if (button) button.disabled = false;
    if (res && res.error) { window.alert(res.error); return; }
    if (result === 'bad') { acceptBox.writing = null; acceptBox.draft = ''; showNotice(uiText('已经交回开发会话', "Sent back to development") + (res.log ? uiText('，附上了当时的日志', " with logs attached") : uiText('（当时没有服务在跑，没有日志）', " (no services were running, so no logs were available)")) + uiText('。需求退回进行中，修好以后照常再进测试、回到待验收。', ". The requirement returns to development and can go through testing and acceptance again after the fix.")); }
    return window.shell.listReqs().then(function (found) { reqs = found; list(); panel(); });
  });
}
function acceptSend(r, c, text, button) {
  if (!String(text || '').trim()) { window.alert(uiText('写一句哪里不对。', "Describe what went wrong.")); return; }
  acceptMark(r, c, 'bad', String(text).trim(), button);
}
function showNotice(text) {
  var n = $('notice'); if (!n) return;
  n.replaceChildren(el('span', '', text), el('button', 'notice-close', '×'));
  n.hidden = false; clearTimeout(showNotice.timer); showNotice.timer = setTimeout(function () { n.hidden = true; }, 12000);
}

// 绑定事件：由 main.js 按顺序调用
function wireBench() {
  document.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('[data-bench]')) benchClick(e); });
  document.addEventListener('change', function (e) { if (e.target.closest && e.target.closest('[data-bench]')) benchChange(e); });
  document.addEventListener('input', function (e) { if (e.target.dataset && e.target.dataset.bnsay) BENCH.said = e.target.value; });
  window.shell.onSvcUpdate(function (v) {
    if (!v || !v.scope) return;
    if ((v.rev || 0) < ((BENCH.run[v.scope] || {}).rev || 0)) return;
    BENCH.run[v.scope] = Object.assign({}, BENCH.run[v.scope] || {}, v);
    benchRender(v.scope);
    if (current === v.scope) list(); // 左栏「人工验收」标签上的小标记
  });
  window.shell.onSvcLog(function (p) {
    if (!p || !p.scope) return;
    BENCH.logs[p.scope] = BM.append(BENCH.logs[p.scope], p.lines, 2000);
    var box = BENCH.box[p.scope], ll = box && box.querySelector('.bn-ll');
    if (!ll) return;
    var f = BENCH.filter[p.scope] || '', near = ll.scrollHeight - ll.scrollTop - ll.clientHeight < 40;
    ll.insertAdjacentHTML('beforeend', BM.filter(p.lines, f).map(BM.lineHtml).join(''));
    while (ll.childNodes.length > 800) ll.removeChild(ll.firstChild);
    if (near) ll.scrollTop = ll.scrollHeight;
  });
  window.shell.onEnvsChanged(function () { benchLoadEnvs(); Object.keys(BENCH.box).forEach(function (k) { benchLoad(k); }); });
  benchLoadEnvs();
}
