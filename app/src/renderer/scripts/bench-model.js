// 人工验收和人工测试（同一套界面）的 model 层：不碰 DOM，测试（tests/test_app_accept.cjs）直接在 Node 里调用。
// 数据：主进程 services.js 给的服务状态和日志行（{t 时间, s 服务, k out|err|sys, x 文字（已打码）}），rf.py env plan 给的「这次怎么启动」。
var BenchModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var esc = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).esc;
  var pad = function (n) { return (n < 10 ? '0' : '') + n; };
  function clock(t) { var d = new Date(t); return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()); }

  // 一个服务的状态（运行着的，或者还没启动过的：只有计划里的名字）
  function statusOf(svc) {
    var s = (svc && svc.status) || 'idle';
    if (s === 'starting') return { cls: 'a', text: svc.port ? uiText('正在启动 · 等端口 ', "Starting · Waiting for port ") + svc.port + uiText(' 可连', "") : uiText('正在启动', "Starting"), busy: true };
    if (s === 'running') return { cls: 'g', text: uiText('运行中', "Running") + (svc.port ? ' :' + svc.port : '') };
    if (s === 'stopping') return { cls: 'a', text: uiText('正在停止', "Stopping"), busy: true };
    if (s === 'exited') return { cls: '', text: uiText('已退出（', "Exited (") + (svc.code != null ? uiText('退出码 ', "exit code ") + svc.code : uiText('信号 ', "signal ") + (svc.signal || '?')) + '）' };
    if (s === 'failed') return { cls: 'r', text: uiText('启动失败', "Startup failed") };
    return { cls: '', text: uiText('没启动', "Stopped") };
  }
  var live = function (svc) { return !!svc && (svc.status === 'starting' || svc.status === 'running' || svc.status === 'stopping'); };

  // 服务的行：计划里的每个服务（按环境里的顺序）配上运行状态；计划里没有、但还开着的（刚从环境里删掉的）也列出来，能停
  function rows(plan, running, histories) {
    var byName = {}, hs = histories || {};
    (running || []).forEach(function (s) { byName[s.name] = s; });
    var out = ((plan && plan.services) || []).map(function (p) { return { name: p.name, plan: p, run: byName[p.name] || null, hist: hs[p.name] || [] }; });
    (running || []).forEach(function (s) { if (!out.some(function (o) { return o.name === s.name; }) && live(s)) out.push({ name: s.name, plan: null, run: s, hist: hs[s.name] || [] }); });
    return out;
  }

  // 启动后代码改过：运行中的服务，代码比启动时新（醒目的标记和「重启」）
  var isStale = function (svc) { return !!svc && svc.status === 'running' && !!svc.stale; };
  // 这个服务的自动重启现在是什么情况（标记旁边的小字）
  function staleNote(svc, planItem) {
    if (!isStale(svc)) return '';
    if (svc.consent) return uiText('启动命令或准备步骤需要你重新同意，所以没有自动重启；点「重启」时会让你看一眼', "The startup command or preparation steps need approval again. Select Restart to review them.");
    var on = planItem ? planItem.auto_restart !== false : svc.autoRestart !== false;
    return on ? uiText('「代码变了自动重启」开着：开发会话下一轮停下时、或你下次打开这个标签时会重启', "Automatic restart is on. Services will restart when the development session finishes its next turn or when you reopen this tab.") : uiText('「代码变了自动重启」关着（在测试环境里设置），要自己点重启', "Automatic restart is off in the test environment. Select Restart to apply code changes.");
  }

  // 启动记录：一条一行。「11-19 15:10 自动重启：开发会话改了代码 · abc1234 + 未提交改动」，有退出码、用时的也带上
  function histLine(h) {
    var d = new Date(h.t), when = pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    var actions = { 启动: uiText('启动', 'Start'), 重启: uiText('重启', 'Restart'), 自动重启: uiText('自动重启', 'Automatic restart'),
      停止: uiText('停止', 'Stop'), 退出: uiText('退出', 'Exit'), 自己退出: uiText('自己退出', 'Exited'),
      启动失败: uiText('启动失败', 'Startup failed'), 应用退出时停掉: uiText('应用退出时停掉', 'Stopped on app exit'),
      清理遗留进程: uiText('清理遗留进程', 'Cleaned up leftover process') };
    var reasons = {
      你点的: uiText('你点的', 'Requested by you'), 退出应用: uiText('退出应用', 'App exited'),
      开发会话改了代码: uiText('开发会话改了代码', 'Development session changed the code'),
      打开人工验收时发现代码改过: uiText('打开人工验收时发现代码改过', 'Found changes when opening manual acceptance'),
      确认完成前停掉: uiText('确认完成前停掉', 'Stopped before completion'),
      '上次应用没退干净，进程还在': uiText('上次应用没退干净，进程还在', 'Process remained after the app exited'),
    };
    var action = Object.hasOwn(actions, h.action) ? actions[h.action] : h.action;
    var reason = Object.hasOwn(reasons, h.reason) ? reasons[h.reason] : h.reason;
    var s = when + ' ' + action + (reason ? uiText('：', ': ') + reason : '');
    if (h.head) s += ' · ' + h.head + (h.dirty ? uiText(' + 未提交改动', " + uncommitted changes") : '');
    if (h.code != null) s += uiText(' · 退出码 ', " · Exit code ") + h.code; else if (h.signal) s += uiText(' · 信号 ', " · Signal ") + h.signal;
    if (h.ms != null && h.ms >= 0 && ['启动', '重启', '自动重启'].includes(h.action)) s += ' · ' + (h.ms < 1000 ? h.ms + uiText(' 毫秒', " ms") : (h.ms / 1000).toFixed(1) + uiText(' 秒', " s")) + uiText('后可用', " until ready");
    return s;
  }
  var histLast = function (list) { return list && list.length ? histLine(list[list.length - 1]) : ''; };

  // 「这次会用什么」：RelayForge 这样的应用说成人话（打开的是演示项目 / 当前项目的副本），别的项目列出解析后的验收变量
  function planLines(p) {
    if (!p) return [];
    var lines = [];
    if (p.opens) lines.push({ k: uiText('打开的是', "Running "), v: p.opens });
    (p.accept || []).forEach(function (a) { lines.push({ k: a.name, v: a.value, mono: true, missing: (a.missing || []).length > 0 }); });
    return lines;
  }
  var usesCopy = function (plan) { return ((plan && plan.services) || []).some(function (s) { return s.uses_copy; }); };

  // 日志：全部或某一个服务；保留最近 max 行
  function filter(lines, svc) { return (lines || []).filter(function (l) { return !svc || l.s === svc; }); }
  function append(lines, more, max) { var out = (lines || []).concat(more || []); return out.length > (max || 2000) ? out.slice(out.length - (max || 2000)) : out; }
  function lineHtml(l) {
    return '<div class="bn-l ' + (l.k === 'err' ? 'err' : l.k === 'sys' ? 'sys' : '') + '"><time>' + clock(l.t) + '</time><b>' + esc(l.s) + '</b><span>' + esc(l.x) + '</span></div>';
  }

  // 启动前要你同意的：启动命令（第一次执行、或命令和目录改了以后）和要执行的准备步骤
  function needsHtml(needs, plan) {
    var svcs = ((plan && plan.services) || []).filter(function (s) { return (needs.services || []).indexOf(s.name) >= 0; });
    var steps = ((plan && plan.prep) || []).filter(function (x) { return (needs.prep || []).indexOf(x.id) >= 0; });
    var h = uiText('<h2>第一次执行这些命令，先看一眼</h2><p>', "<h2>Review commands before running them</h2><p>") + (plan && plan.env ? uiText('测试环境「', "The test environment “") + esc(plan.env.name) + '」' : '') + uiText('要从 <span class="mono">', "” will start services from <span class=\"mono\">") + esc(plan && plan.place) + uiText('</span> 启动服务。', "</span>.")
      + uiText('下面这些命令是第一次执行，或者命令、目录改过了：看过实际的命令和目录，没问题再点同意。同意只记在这台电脑上，以后不变就不再问；AI 的会话不能替你同意。</p>', "Review these new or changed commands and their working directories before approving. Approval is saved on this computer for these exact commands and directories.</p>");
    h += svcs.map(function (s) {
      return uiText('<div class="ev-appr"><b>启动服务「', "<div class=\"ev-appr\"><b>Start service “") + esc(s.name) + '」</b><pre class="mono">' + esc(s.command) + uiText('</pre><div class="small">目录：<span class="mono">', "</pre><div class=\"small\">Directory: <span class=\"mono\">") + esc(s.cwd) + '</span>'
        + (s.port ? uiText(' · 等端口 ', " · Wait for port ") + esc(s.port) + uiText(' 可连', "") : uiText(' · 没配端口，进程活着就算运行中', " · No port check. The service is ready while its process is running.")) + '</div>'
        + ((s.accept || []).length ? uiText('<div class="small">验收时额外带：', "<div class=\"small\">Environment variables for acceptance: ") + s.accept.map(function (a) { return '<span class="mono">' + esc(a.name) + '=' + esc(a.value) + '</span>'; }).join('、') + '</div>' : '') + '</div>';
    }).join('');
    h += steps.map(function (x) {
      return uiText('<div class="ev-appr"><b>准备步骤 ', "<div class=\"ev-appr\"><b>Preparation step ") + esc((x.n ? x.n + '. ' : '') + (x.note || '')) + '</b><pre class="mono">' + esc(x.command) + uiText('</pre><div class="small">执行目录：<span class="mono">', "</pre><div class=\"small\">Working directory: <span class=\"mono\">") + esc(x.abs_dir || x.dir) + '</span> · ' + esc(x.due_why || '') + '</div></div>';
    }).join('');
    return h;
  }

  // 需求面板里一条验收条件的逐条验收：上次点的什么（通过 / 这里不对 / 还没点）
  function markOf(c) {
    var a = c && c.accept;
    if (!a) return { state: 'none', text: '' };
    var when = String(a.at || '').slice(5);
    if (a.result === 'ok') return { state: 'ok', text: uiText('✓ 你点了通过 · ', "✓ You marked this as passed · ") + when };
    return { state: 'bad', text: uiText('上次说这里不对（', "Last marked as incorrect (") + when + '）：' + (a.say || '') + uiText('。已经交回开发会话；修好回到待验收以后再试一次。', ". Sent back to development. Try again once it returns to acceptance.") };
  }

  // 「跑起来让我验收」换成什么：有可启动的服务时去人工验收标签并启动；没有就保留原来的（交给开发会话）
  function runButton(envSummary) {
    var n = envSummary ? envSummary.services : 0;
    return n ? { label: uiText('去人工验收', "Open manual acceptance"), title: uiText('切到「人工验收」标签，程序用这个需求的代码把测试环境里配的 ', "In Manual acceptance, RelayForge starts the ") + n + uiText(' 个服务启动起来；你自己试，然后在下面逐条点', " configured services using this requirement's code. Try it yourself and mark each criterion below."), bench: true }
      : { label: uiText('跑起来让我验收', "Start app for acceptance"), title: uiText('测试环境里还没配「怎么启动」：先让开发会话把程序跑起来告诉你怎么试。也可以去测试环境里配好服务，以后由程序直接启动', "No startup services are configured. Ask the development session to start the app now, or configure services in the test environment for future runs."), bench: false };
  }

  return { esc: esc, clock: clock, statusOf: statusOf, live: live, rows: rows, isStale: isStale, staleNote: staleNote, histLine: histLine, histLast: histLast, planLines: planLines, usesCopy: usesCopy, filter: filter, append: append, lineHtml: lineHtml,
    needsHtml: needsHtml, markOf: markOf, runButton: runButton };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = BenchModel;
