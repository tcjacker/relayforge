// 测试环境的 model 层：不碰 DOM，测试（tests/test_app_envs_ui.cjs）直接在 Node 里调用。
// 编辑稿（draft）是界面上正在改的那一份：从 rf.py env reveal 的结果来，保存时原样交给 rf.py env save；有没有改过就是和原来比。
var EnvModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var MASK = '••••••';
  var esc = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).esc;
  function str(v) { return v == null ? '' : String(v); }

  // 从 rf.py env reveal 的结果得到编辑稿：只留界面能改的部分；保密的值是明文（只在界面里）
  function draftFrom(d) {
    d = d || {};
    return {
      name: str(d.name), note: str(d.note),
      // accept：人工验收时额外带的环境变量（值里可以用 {{req}} {{worktree}} {{scratch}} {{project}} {{project_copy}}）
      services: (d.services || []).map(function (s) { return { name: str(s.name), command: str(s.command), port: s.port == null ? '' : String(s.port), dir: str(s.dir), auto_restart: s.auto_restart !== false, accept: (s.accept || []).map(function (a) { return { name: str(a.name), value: str(a.value) }; }) }; }),
      config: str(d.config),
      deps: (d.deps || []).filter(function (x) { return x.from === 'user'; }).map(function (x) { var own = x.own || x; return { key: str(x.key), name: str(own.name), type: str(own.type), address: str(x.address) }; }),
      vars: (d.vars || []).map(function (v) { return { name: str(v.name), value: str(v.value), secret: !!v.secret }; }),
      request: {
        items: ((d.request || {}).items || []).map(function (r) { return { in: r.in === 'query' ? 'query' : 'header', name: str(r.name), value: str(r.value), secret: !!r.secret }; }),
        before: str((d.request || {}).before), after: str((d.request || {}).after),
      },
      users: (d.users || []).map(function (u) { return { id: str(u.id), name: str(u.name), note: str(u.note), fields: (u.fields || []).map(function (f) { return { key: str(f.key), value: str(f.value), secret: !!f.secret }; }) }; }),
      prep: (d.prep || []).map(function (x) { return { id: str(x.id), note: str(x.note), command: str(x.command), dir: str(x.dir), when: x.when === 'changed' ? 'changed' : 'always', watch: (x.watch || []).join(', '), timeout: x.timeout == null ? '' : String(x.timeout) }; }),
    };
  }
  // 交给 rf.py env save 的样子：手改的依赖带上 from: user；新加的测试用户没有 id，由程序给
  function payloadOf(draft) {
    var p = JSON.parse(JSON.stringify(draft));
    p.deps = p.deps.map(function (x) { return Object.assign({}, x, { from: 'user' }); });
    p.users = p.users.map(function (u) { var o = Object.assign({}, u); if (!/^U\d+$/.test(o.id)) delete o.id; return o; });
    p.prep = (p.prep || []).map(function (x) {
      var o = { note: x.note, command: x.command, dir: x.dir, when: x.when, watch: String(x.watch || '').split(/[\n,，]+/).map(function (w) { return w.trim(); }).filter(Boolean), timeout: x.timeout };
      if (/^P\d+$/.test(x.id)) o.id = x.id;
      return o;
    });
    return p;
  }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  function isDirty(draft, data) { return !!draft && !!data && !same(draft, draftFrom(data)); }
  // 哪几块改过了（保存条上说「改了怎么启动、环境变量」）
  var SECTIONS = [['name', uiText('名称', "Name")], ['note', uiText('说明', "Description")], ['services', uiText('怎么启动', "Startup services")], ['config', uiText('配置文件', "Configuration file")], ['deps', uiText('外部依赖', "External dependencies")], ['vars', uiText('环境变量', "Environment variables")], ['request', uiText('请求的通用设置', "Request defaults")], ['users', uiText('测试用户', "Test users")], ['prep', uiText('准备步骤', "Preparation steps")]];
  function changedSections(draft, data) {
    if (!draft || !data) return [];
    var base = draftFrom(data);
    return SECTIONS.filter(function (s) { return !same(draft[s[0]], base[s[0]]); }).map(function (s) { return s[1]; });
  }

  // ---------- 外部依赖 ----------
  var STATUS = { ok: ['g', uiText('能连上', "Reachable")], bad: ['r', uiText('连不上', "Unreachable")], none: ['a', uiText('没配', "Not configured")] };
  function statusPill(check) {
    if (!check) return uiText('<span class="ts-pill">还没检查</span>', "<span class=\"ts-pill\">Not checked</span>");
    var s = STATUS[check.status] || ['', check.status];
    return '<span class="ts-pill ' + s[0] + '"' + (check.detail ? ' title="' + esc(check.detail) + '"' : '') + '>' + s[1] + '</span>';
  }
  // 依赖表：配置文件读出来的（被手改盖过的不再列）+ 编辑稿里手改的（还没保存的也列出来，状态等保存后再查）
  function depRows(data, draft) {
    var mine = (draft ? draft.deps : draftFrom(data).deps);
    var keys = {}; mine.forEach(function (d) { if (d.key) keys[d.key] = true; });
    var saved = {}; (data.deps || []).forEach(function (d) { saved[d.key] = d; });
    var rows = (data.deps || []).filter(function (d) { return d.from === 'config' && !keys[d.key]; }).map(function (d) { return Object.assign({ edit: -1 }, d); });
    mine.forEach(function (d, i) {
      var s = saved[d.key] && saved[d.key].from === 'user' && saved[d.key].address === d.address ? saved[d.key] : null;
      var base = (data.deps || []).filter(function (x) { return x.key === d.key && x.from === 'config'; })[0];
      rows.push({ key: d.key, name: d.name || (base && base.name) || '', type: d.type || (s && s.type) || (base && base.type) || '', address: d.address, from: 'user', edit: i,
        replaces: !!base || !!(s && s.replaces), check: s ? s.check : null, note: s ? s.note : null });
    });
    return rows;
  }
  function depCounts(rows) {
    var c = { ok: 0, bad: 0, none: 0, unchecked: 0 };
    rows.forEach(function (r) { if (r.check && c[r.check.status] != null) c[r.check.status]++; else c.unchecked++; });
    return c;
  }
  function scanLine(scan) {
    if (!scan) return uiText('还没有读过配置文件。', "The configuration file hasn't been read.");
    if (scan.error) return scan.error;
    var t = uiText('读于 ', "Read at ") + str(scan.at).replace('T', ' ').slice(5, 16) + (scan.kind ? '（' + ({ yaml: 'YAML', properties: 'properties', dotenv: '.env' })[scan.kind] + '）' : '');
    if (scan.stale) t += uiText('；配置文件之后改过，点「重新读配置」', ". The file has changed. Select Reread configuration.");
    if ((scan.skipped || []).length) t += uiText('；跳过 ', ". Skipped ") + scan.skipped.length + uiText(' 处不支持的写法', " unsupported entries");
    return t;
  }

  // ---------- 测试用户：JWT（只做 base64 解码，不验签） ----------
  function b64(part) {
    var s = part.replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    if (typeof atob === 'function') { var bin = atob(s), bytes = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i); return new TextDecoder().decode(bytes); }
    return Buffer.from(s, 'base64').toString('utf8'); // eslint-disable-line no-undef -- 在 Node 的测试里（没有 atob 时）
  }
  function looksJwt(v) { return /^(Bearer\s+)?[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$/.test(String(v || '').trim()); }
  function jwtInfo(token, now) {
    var t = String(token || '').trim().replace(/^Bearer\s+/i, '');
    if (!looksJwt(t)) return null;
    var parts = t.split('.'), header, payload;
    try { header = JSON.parse(b64(parts[0])); payload = JSON.parse(b64(parts[1])); } catch (_) { return { error: uiText('看起来像 JWT，但解不出来。', "This looks like a JWT but could not be decoded.") }; }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return { error: uiText('看起来像 JWT，但内容不是 JSON 对象。', "This looks like a JWT but does not contain a JSON object.") };
    var out = { header: header, payload: payload, exp: null, expired: null };
    if (typeof payload.exp === 'number') { out.exp = payload.exp; out.expired = (now != null ? now : Date.now()) >= payload.exp * 1000; }
    return out;
  }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function when(sec) { var d = new Date(sec * 1000); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  // 解出来的内容：载荷里的字段（exp、iat 这些时间单独说），过期情况
  function jwtSummary(info) {
    if (!info) return null;
    if (info.error) return { text: info.error, state: 'bad' };
    var keys = Object.keys(info.payload).filter(function (k) { return ['exp', 'iat', 'nbf'].indexOf(k) < 0; });
    var fields = keys.slice(0, 8).map(function (k) { var v = info.payload[k]; return k + '=' + (typeof v === 'string' ? v : JSON.stringify(v)); });
    if (keys.length > 8) fields.push(uiText('…另 ', "… ") + (keys.length - 8) + uiText(' 项', " more"));
    var exp = info.exp == null ? { text: uiText('没有过期时间', "No expiration"), state: '' } : info.expired ? { text: uiText('已过期（', "Expired (") + when(info.exp) + '）', state: 'bad' } : { text: uiText('有效到 ', "Valid until ") + when(info.exp), state: 'ok' };
    return { fields: fields, exp: exp, text: fields.join(' · '), state: exp.state };
  }

  // ---------- 请求的通用设置 ----------
  var REF = /\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}/g;
  // 值里引用了哪些变量、哪些在这套环境里找不到（编辑时就提示，不用等保存）
  function refsOf(value, draft, user) {
    var names = {}, out = { refs: [], missing: [] };
    (draft.vars || []).forEach(function (v) { if (v.name) names[v.name] = true; });
    var keys = {}; ((user || {}).fields || []).forEach(function (f) { if (f.key) keys[f.key] = true; });
    String(value || '').replace(REF, function (_m, name) {
      out.refs.push(name);
      if (name.indexOf('user.') === 0 ? !keys[name.slice(5)] : !names[name]) out.missing.push(name);
      return '';
    });
    return out;
  }

  // ---------- AI 的建议 ----------
  var KIND = { service: uiText('启动命令', "Startup command"), config: uiText('配置文件', "Configuration file"), var: uiText('环境变量', "Environment variable"), prep: uiText('准备步骤', "Preparation step"), testset: uiText('测试集设置', "Test set settings") };
  function whenText(x) { return x.when === 'changed' ? (x.watch || []).join('、') + uiText(' 变了才执行', " changes") : uiText('每次运行前执行', "Before every run"); }
  function testsetText(f) {
    var parts = [];
    if (f.lang) parts.push(uiText('语言 ', "Language ") + f.lang);
    if (f.entry) parts.push(uiText('接入代码 ', "Entry point ") + f.entry);
    if (f.classpath) parts.push('classpath ' + (Array.isArray(f.classpath) ? f.classpath : [f.classpath]).join(' : '));
    if (f.build != null) parts.push('build ' + (f.build || uiText('（清空）', "(clear)")));
    return parts.join(' · ');
  }
  function proposalItems(p) {
    if (!p) return [];
    return (p.items || []).filter(function (i) { return i.state === 'pending'; }).map(function (i) {
      var text = i.kind === 'service' ? i.name + '：' + (i.command || uiText('（没有命令）', "(no command)")) + (i.port ? uiText(' · 端口 ', " · Port ") + i.port : '') + (i.dir ? uiText(' · 在 ', " · Directory ") + i.dir : '')
          + ((i.accept || []).length ? uiText(' · 验收时带 ', " · With acceptance variables ") + i.accept.map(function (a) { return a.name + '=' + a.value; }).join('、') : '')
        : i.kind === 'config' ? i.file
          : i.kind === 'prep' ? i.note + '：' + i.command + (i.dir ? uiText(' · 在 ', " · Directory ") + i.dir : '') + ' · ' + whenText(i)
            : i.kind === 'testset' ? i.set + (i.title ? '「' + i.title + '」' : '') + '：' + testsetText(i.fields || {})
              : i.name + (i.secret ? uiText('（保密，值由你自己填）', " (secret. Enter the value yourself.)") : i.value ? ' = ' + i.value : '');
      return { k: i.k, kind: KIND[i.kind] || i.kind, text: text, why: i.why || '', note: i.kind === 'prep' ? uiText('采纳以后第一次执行前还会请你看一眼命令、点同意', "After applying, review and approve the command before its first run") : '' };
    });
  }

  // ---------- 准备步骤 ----------
  // 一条步骤的状态（表格最后一列）：还没同意、上次成功或失败、还没执行过
  function prepState(x) {
    if (!x || !x.command) return { cls: '', text: uiText('保存后才能执行', "Save before running") };
    if (!x.approved) return { cls: 'a', text: uiText('还没同意执行', "Approval required") };
    if (!x.last) return { cls: '', text: uiText('还没执行过', "Never run") };
    return x.last.ok ? { cls: 'g', text: uiText('上次成功', "Last run succeeded") } : { cls: 'r', text: uiText('上次失败', "Last run failed") + (x.last.code != null ? uiText('（退出码 ', " (exit code ") + x.last.code + '）' : '') };
  }
  // 第一次执行前的确认框：实际的命令、在哪个目录执行、什么时候执行（blocked：rf.py test run --prep-check 或 env show 给的步骤）
  // steps：测试环境的准备步骤，和测试集的构建命令（kind 'build'，见 backend/relayforge/testsets/store.py 的 build_item）
  function approvalHtml(steps, envName) {
    var builds = (steps || []).some(function (x) { return x.kind === 'build'; }), preps = (steps || []).some(function (x) { return x.kind !== 'build'; });
    var what = preps && builds ? uiText('准备步骤和测试集的构建命令', "preparation steps and test set build commands") : builds ? uiText('测试集的构建命令', "test set build commands") : uiText('准备步骤', "preparation steps");
    return uiText('<h2>第一次执行这些命令，先看一眼</h2><p>', "<h2>Review commands before running them</h2><p>") + (envName && preps ? uiText('测试环境「', "The test environment “") + esc(envName) + '」' : '') + uiText('运行测试集之前要先执行下面的', "” needs to run these ") + what + uiText('。它们是第一次执行，或者命令、执行目录改过了：', " before running tests. The commands or directories are new or have changed. ")
      + uiText('看过实际的命令和执行目录，没问题再点同意。同意只记在这台电脑上（按命令和目录的文字），以后命令不变就不再问；AI 的会话不能替你同意。</p>', "Review them before approving. Approval stays on this computer and applies to these exact commands and directories.</p>")
      + (steps || []).map(function (x) {
        return '<div class="ev-appr"><b>' + esc((x.n ? x.n + '. ' : '') + (x.note || '')) + '</b><pre class="mono">' + esc(x.command) + '</pre>'
          + uiText('<div class="small">执行目录：<span class="mono">', "<div class=\"small\">Working directory: <span class=\"mono\">") + esc(x.abs_dir || x.dir || uiText('项目目录', "Project directory")) + '</span> · ' + esc(whenText(x)) + uiText(' · 超时 ', " · Timeout ") + esc(x.timeout || 600) + uiText(' 秒</div></div>', " s</div></div>");
      }).join('');
  }

  // ---------- 选择框（测试集管理右上角、查看器顶栏、需求面板「全部重跑」上方共用） ----------
  function selectHtml(list) {
    var envs = (list && list.envs) || [], use = list && list.use;
    var opts = envs.map(function (e) { return '<option value="' + esc(e.id) + '"' + (e.id === use ? ' selected' : '') + '>' + esc(e.name) + '</option>'; }).join('');
    opts = '<option value=""' + (use ? '' : ' selected') + '>' + (envs.length ? uiText('不用测试环境', "No test environment") : uiText('还没有测试环境', "No test environments yet")) + '</option>' + opts;
    return uiText('<label class="ev-sel"><span>测试环境</span><select data-envuse="1" aria-label="测试环境"', "<label class=\"ev-sel\"><span>Test environment</span><select data-envuse=\"1\" aria-label=\"Test environment\"") + (list ? '' : ' disabled') + '>' + opts + '</select>'
      + uiText('<button type="button" class="link small" data-envgo="1">设置</button></label>', "<button type=\"button\" class=\"link small\" data-envgo=\"1\">Settings</button></label>");
  }

  // 新的一行
  function blankRow(kind) {
    return ({ service: { name: '', command: '', port: '', dir: '', auto_restart: true, accept: [] }, acc: { name: '', value: '' }, var: { name: '', value: '', secret: false }, req: { in: 'header', name: '', value: '', secret: false },
      dep: { key: '', name: '', type: '', address: '' }, prep: { id: '', note: '', command: '', dir: '', when: 'always', watch: '', timeout: '' }, user: { id: '', name: '', note: '', fields: [{ key: 'jwt', value: '', secret: true }, { key: 'uid', value: '', secret: false }] },
      field: { key: '', value: '', secret: false } })[kind];
  }
  // 保密的值只存本机、进仓库的值会被别人看到：给出提示用的标签
  function secretPill(secret) { return secret ? uiText('<span class="ts-pill a">只存本机</span>', "<span class=\"ts-pill a\">Local only</span>") : uiText('<span class="muted small">进仓库</span>', "<span class=\"muted small\">Stored in Git</span>"); }

  return {
    MASK: MASK, esc: esc, draftFrom: draftFrom, payloadOf: payloadOf, isDirty: isDirty, changedSections: changedSections,
    statusPill: statusPill, depRows: depRows, depCounts: depCounts, scanLine: scanLine,
    looksJwt: looksJwt, jwtInfo: jwtInfo, jwtSummary: jwtSummary, refsOf: refsOf, proposalItems: proposalItems, selectHtml: selectHtml, blankRow: blankRow, secretPill: secretPill,
    prepState: prepState, approvalHtml: approvalHtml, whenText: whenText, testsetText: testsetText,
  };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = EnvModel;
