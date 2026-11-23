// 问题的后台初判：同时最多两个，其余排队；判完还没判过的（比如判的时候你又回复了）马上再判；没判出来的不自动重试
const test = require('node:test');
const assert = require('node:assert/strict');
const locale = require('../app/src/main/locale');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const file = path.join(__dirname, '..', 'app', 'src', 'main', 'issues.js');
const HtmlText = require(path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts', 'html-text.js')); // 界面里 index.html 最先加载它

function load(issues, extra = {}) {
  const calls = [], sent = [], pending = [];
  const list = () => ({ ok: true, stdout: JSON.stringify({ issues: issues.map((i) => ({ thread: [], ...i })), features: [] }), stderr: '' });
  const mocks = {
    './backend': {
      uiText: locale.uiText,
      rf: (args) => {
        calls.push(args.join(' '));
        if (args[1] === 'list') return Promise.resolve(list());
        if (args[1] === 'triage') return new Promise((resolve) => pending.push({ id: args[2], resolve }));
        if (args[1] === 'reply') return Promise.resolve({ ok: true, stdout: JSON.stringify({ thread: [], ...issues.find((i) => i.id === args[2]) }), stderr: '' });
        if (args[1] === 'set') return Promise.resolve({ ok: true, stdout: JSON.stringify(issues.find((i) => i.id === args[2])), stderr: '' });
        return Promise.resolve({ ok: true, stdout: '{}', stderr: '' });
      },
      parse: (t, f) => { try { return JSON.parse(t); } catch (_) { return f; } }, lastLine: (x) => String(x).trim().split('\n').pop(),
    },
    './state': { state: { project: '/p', runs: new Map() }, send: (channel, payload) => sent.push([channel, JSON.parse(JSON.stringify(payload))]) },
    './requirements': { pushReqs() {} },
    './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, issue() {} },
    './sessions': { close() {}, type() {} },
    ...extra,
  };
  const context = vm.createContext({ require: (id) => id === './locale' ? locale : mocks[id] || require(id), module: { exports: {} }, setTimeout, clearTimeout });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
  return { issues: context.module.exports, calls, sent, pending };
}
const tick = () => new Promise((r) => setImmediate(r));
const triaged = (calls) => calls.filter((c) => c.startsWith('issue triage')).map((c) => c.split(' ')[2]);

test('at most two triages run at once and the rest wait their turn', async () => {
  const data = [{ id: 'I-003', state: 'todo', needs_triage: true }, { id: 'I-002', state: 'todo', needs_triage: true }, { id: 'I-001', state: 'todo', needs_triage: true },
    { id: 'I-004', state: 'todo', needs_triage: false, triage_failed: true }];
  const { issues, calls, sent, pending } = load(data);
  await issues.pushIssues();
  await tick();
  assert.deepEqual(triaged(calls), ['I-003', 'I-002']);
  const shown = sent.filter((x) => x[0] === 'issues').pop()[1].issues;
  assert.deepEqual(shown.map((i) => i.triage), ['running', 'running', 'queued', '']); // 没判出来的那条等你点重试
  data[0].needs_triage = false;
  pending.shift().resolve({ ok: true, stdout: '{}', stderr: '' });
  for (let k = 0; k < 6; k++) await tick();
  assert.deepEqual(triaged(calls), ['I-003', 'I-002', 'I-001']);
});

test('a reply that came in while judging is judged again right after', async () => {
  const data = [{ id: 'I-001', state: 'todo', needs_triage: true }];
  const { issues, calls, pending } = load(data);
  await issues.pushIssues();
  await tick();
  assert.deepEqual(triaged(calls), ['I-001']);
  await issues.reply({ id: 'I-001', text: '补一句' }); // still running: not started twice
  assert.deepEqual(triaged(calls), ['I-001']);
  pending.shift().resolve({ ok: true, stdout: '{}', stderr: '' }); // the judgement did not cover the reply, so the list still says needs_triage
  for (let k = 0; k < 6; k++) await tick();
  assert.deepEqual(triaged(calls), ['I-001', 'I-001']);
  data[0].needs_triage = false;
  pending.shift().resolve({ ok: true, stdout: '{}', stderr: '' });
  for (let k = 0; k < 6; k++) await tick();
  assert.deepEqual(triaged(calls), ['I-001', 'I-001']);
});

// ---------- 一起修 ----------
test('several issues start one fix, and a confirmation only goes through the merge queue when it is the last one', async () => {
  const merged = [];
  const data = [
    { id: 'I-001', state: 'review', fix: { id: 'F-001', state: 'fixing' }, confirmed: false, siblings: [{ id: 'I-002', state: 'review', confirmed: false }] },
    { id: 'I-002', state: 'review', fix: { id: 'F-001', state: 'fixing' }, confirmed: false, siblings: [{ id: 'I-001', state: 'review', confirmed: false }] },
    { id: 'I-003', state: 'review', fix: { id: 'F-002', state: 'fixing' }, confirmed: false, siblings: [{ id: 'I-004', state: 'review', confirmed: true }] },
    { id: 'I-004', state: 'review', fix: { id: 'F-002', state: 'fixing' }, confirmed: true, siblings: [{ id: 'I-003', state: 'review', confirmed: false }] },
  ];
  const { issues, calls } = load(data, { './merge': { enqueue: async (id, close, spec) => { merged.push([id, ['issue', 'confirm', spec.issue, '--json'].join(' ')]); return { state: 'resolved' }; } } });
  await issues.startFix(['I-001', 'I-002']);
  assert.ok(calls.includes('issue fix I-001 I-002 --json'));
  await issues.startFix('I-005');
  assert.ok(calls.includes('issue fix I-005 --json'));
  await issues.confirm('I-001'); // I-002 has not been confirmed: only the confirmation is recorded, nothing is merged or queued
  assert.ok(calls.includes('issue confirm I-001 --json'));
  assert.deepEqual(merged, []);
  await issues.confirm('I-003'); // the other one of F-002 is already confirmed: this is the last, so the merge queue takes it
  assert.deepEqual(merged, [['fix:F-002', 'issue confirm I-003 --json']]);
  await issues.unconfirm('I-004');
  assert.ok(calls.includes('issue unconfirm I-004 --json'));
});

test('manual finish passes arbitrary remarks and closes the terminal only when the whole fix ends', async () => {
  const closed = [];
  const data = [
    { id: 'I-001', state: 'resolved', fix: { id: 'F-001', state: 'fixing' } },
    { id: 'I-002', state: 'closed', fix: { id: 'F-001', state: 'ended' } },
  ];
  const { issues, calls } = load(data, { './sessions': { close: (id) => closed.push(id) } });
  await issues.setIssue({ id: 'I-001', action: 'finish-resolved', value: '--未能复现' });
  assert.ok(calls.includes('issue set I-001 finish-resolved --reason=--未能复现 --json'));
  assert.deepEqual(closed, []);
  await issues.setIssue({ id: 'I-002', action: 'finish-closed' });
  assert.deepEqual(closed, ['fix:F-001']);
});

test('finishing the last issue of a fix closes its session before the fix is ended, since its working directory goes', async () => {
  const closed = [];
  const data = [
    { id: 'I-001', num: '#001', state: 'fixing', fix: { id: 'F-003', state: 'fixing', issues: ['I-001', 'I-002'], finished: ['I-002'] } },
    { id: 'I-003', num: '#003', state: 'fixing', fix: { id: 'F-004', state: 'fixing', issues: ['I-003', 'I-004'], finished: [] } },
  ];
  let calls;
  const loaded = load(data, { './sessions': { close: (id) => closed.push([id, calls.filter((c) => c.startsWith('issue set')).length]) } });
  calls = loaded.calls;
  await loaded.issues.setIssue({ id: 'I-001', action: 'finish-closed' });
  assert.deepEqual(closed[0], ['fix:F-003', 0], 'closed before rf.py ends the fix and takes its working directory away');
  closed.length = 0;
  await loaded.issues.setIssue({ id: 'I-003', action: 'finish-closed' });
  assert.deepEqual(closed, [], 'another issue is still being fixed there: the session stays');
});

test('manual finish is available in both views and returning leaves the issue unchanged', async () => {
  const { api, context } = rendererApi();
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, {
      handlers: {}, addEventListener(event, fn) { this.handlers[event] = fn; },
      showModal() { this.open = true; }, close() { this.open = false; },
      querySelector() { return null; }, setAttribute() {},
    });
    return elements.get(id);
  };
  const sent = [];
  context.$ = get;
  context.document = { addEventListener() {} };
  context.window.addEventListener = () => {};
  context.window.shell = new Proxy({
    listIssues: async () => null,
    setIssue: async (request) => {
      sent.push(request);
      const state = request.action === 'finish-resolved' ? 'resolved' : 'closed';
      return { ...api.IS.issues[0], state };
    },
  }, { get: (obj, key) => obj[key] || (() => {}) });
  const file = path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts', 'issue-actions.js');
  vm.runInContext(fs.readFileSync(file, 'utf8') + '\nwireIssues(); renderIssues = function () {};', context);
  get('issue-dlg-form').elements = { reason: { value: '' } };
  for (const state of ['resolved', 'closed']) {
    const i = { id: 'I-001', num: '#001', title: '无法导出', state: 'fixing', fix: { id: 'F-001', state: 'fixing' } };
    api.IS.issues = [i];
    api.IS.sel = i.id;
    api.IS.tab = 'fixing';
    for (const pane of ['thread', 'term']) {
      api.IS.pane[i.id] = pane;
      assert.match(api.stripHtml(i), /结束处理[\s\S]*转入已处理[\s\S]*转入已取消/);
    }
    const click = (act, k) => ({ target: { closest: () => ({ dataset: { act, k } }) } });
    get('issues').handlers.click(click('finish', state));
    assert.equal(get('issue-dlg-cancel').textContent, '返回');
    const previous = sent.length;
    get('issue-dlg').handlers.click(click('dcancel'));
    assert.equal(sent.length, previous);
    assert.equal(api.IS.tab, 'fixing');
    assert.equal(api.IS.issues[0].state, 'fixing');
    get('issues').handlers.click(click('finish', state));
    get('issue-dlg-form').elements.reason.value = state === 'resolved' ? '未能复现' : '';
    get('issue-dlg-form').handlers.submit({ preventDefault() {} });
    await tick();
    assert.equal(sent.at(-1).action, 'finish-' + state);
    assert.equal(sent.at(-1).value, state === 'resolved' ? '未能复现' : '');
    assert.equal(api.IS.tab, state);
    assert.equal(api.IS.sel, i.id);
    assert.equal(api.IS.pane[i.id], undefined);
    context.renderIssueTabs();
    assert.match(get('issue-tabs').innerHTML, /data-k="fixing"><b>0<\/b>/);
    assert.match(get('issue-tabs').innerHTML, new RegExp('data-k="' + state + '"><b>1</b>'));
    assert.doesNotMatch(api.stripHtml(api.IS.issues[0]), /结束处理/);
  }
});

test('the list marks batches, confirmed ones waiting for their siblings, and what is waiting for the user', () => {
  const sent = {};
  const context = vm.createContext({ merging: {}, waiting: {}, $: () => ({}), window: {}, console, HtmlText, uiText: HtmlText.text });
  const src = fs.readFileSync(path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts', 'issues.js'), 'utf8');
  vm.runInContext(src + '\nthis.api = { iphrase, iWaitsForYou, iEligible, iMultiHint, iPrunePick, IS, iNums };', context);
  const { iphrase, iWaitsForYou, iEligible, iMultiHint, iPrunePick, IS, iNums } = context.api;
  const wait = { id: 'I-001', state: 'review', confirmed: true, confirmed_waiting: true, waiting_for: ['I-029', 'I-026'], batch: 3 };
  assert.deepEqual(Array.from(iphrase(wait)), ['已确认，等同批的 #029、#026', 'green']);
  assert.equal(iWaitsForYou(wait), false); // 已确认、等同批的：不算等你处理
  assert.equal(iWaitsForYou({ state: 'review', confirmed: false }), true);
  assert.deepEqual(Array.from(iphrase({ state: 'fixing', batch: 3 })), ['修复中 · 一起修 3 条', 'blue']);
  assert.deepEqual(Array.from(iphrase({ state: 'fixing', batch: 1 })), ['修复中', 'blue']);
  assert.equal(iNums(['I-027', 'I-026']), '#027、#026');
  const ok = { id: 'I-001', state: 'todo', kind: 'bug', feature: 'RF-101' };
  assert.equal(iEligible(ok), true);
  for (const bad of [{ kind: 'ask' }, { kind: 'request' }, { deferred: true }, { needs_triage: true }, { triage: 'queued' }, { triage_failed: true }, { state: 'fixing' }]) assert.equal(iEligible({ ...ok, ...bad }), false);
  assert.equal(iMultiHint([ok, { ...ok, id: 'I-002', feature: 'RF-102' }]), '涉及 2 个功能点，分开修更稳。');
  assert.equal(iMultiHint([{ ...ok, feature: null }, { ...ok, id: 'I-002', feature: null }]), '这几条都还没关联功能点，分开修更稳。');
  assert.equal(iMultiHint([ok, { ...ok, id: 'I-002' }]), '');
  // the selection survives a filter but not leaving 待处理, and drops what can no longer be fixed
  IS.issues = [ok, { ...ok, id: 'I-002' }]; IS.tab = 'todo'; IS.pick = { 'I-001': true, 'I-002': true, 'I-009': true };
  iPrunePick(); assert.deepEqual(Object.keys(IS.pick).sort(), ['I-001', 'I-002']);
  IS.issues[1].deferred = true; iPrunePick(); assert.deepEqual(Object.keys(IS.pick), ['I-001']);
  IS.tab = 'fixing'; iPrunePick(); assert.deepEqual(Object.keys(IS.pick), []);
});

// ---------- 交给 AI 排查 ----------
test('a reply goes into the open investigation session and is not judged again; without one it is a plain reply', async () => {
  const typed = [];
  const data = [{ id: 'I-001', state: 'todo', num: '#001', probe: { sessions: ['s1'] } }];
  const { issues, calls } = load(data, { './sessions': { close() {}, type: (run, text) => typed.push([run.id, text]) } });
  await issues.pushIssues();
  await issues.reply({ id: 'I-001', text: '在列表页' }); // no session open: an ordinary reply, judged again as before
  assert.ok(calls.includes('issue reply I-001 在列表页 --json'));
  assert.deepEqual(typed, []);
});

test('with the probe session open, the reply carries --to-probe and is typed into that session', async () => {
  const typed = [], runs = new Map([['probe:I-001', { id: 'probe:I-001' }]]);
  const data = [{ id: 'I-001', state: 'todo', num: '#001', probe: { sessions: ['s1'] } }, { id: 'I-002', state: 'todo', num: '#002' }];
  const { issues, calls } = load(data, {
    './state': { state: { project: '/p', runs }, send() {} },
    './sessions': { close() {}, type: (run, text) => typed.push([run.id, text]) },
    './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post: (m) => { if (runs.has(m.to)) typed.push([m.to, m.text]); } }, // 经收件箱：会话开着才送，没开着不打开（open: false）
  });
  await issues.reply({ id: 'I-001', text: '在列表页', images: ['/shots/a.png'] });
  assert.ok(calls.includes('issue reply I-001 在列表页 --to-probe --image /shots/a.png --json'), calls.join('|'));
  assert.deepEqual(typed, [['probe:I-001', '用户在问题 #001 里补充了一句：在列表页 附图：/shots/a.png']]);
  await issues.reply({ id: 'I-002', text: '另一条' }); // another issue has no open session: nothing is typed anywhere
  assert.ok(calls.includes('issue reply I-002 另一条 --json'));
  assert.equal(typed.length, 1);
  // a fixing issue still reaches its fix session, not the probe one
  data.push({ id: 'I-003', state: 'fixing', num: '#003', fix: { id: 'F-001' } });
  await issues.reply({ id: 'I-003', text: '补一句' });
  assert.equal(typed.length, 1);
});

test('starting a fix puts the investigation sessions of those issues away; a hand-over is a probe call', async () => {
  const closed = [];
  const { issues, calls } = load([{ id: 'I-001', state: 'todo' }, { id: 'I-002', state: 'todo' }], { './sessions': { close: (id) => closed.push(id), type() {} } });
  await issues.startFix(['I-001', 'I-002']);
  assert.deepEqual(closed, ['probe:I-001', 'probe:I-002']);
  await issues.probe('I-003');
  assert.ok(calls.includes('issue probe I-003 --json'));
  assert.equal(issues.probeId('I-003'), 'probe:I-003');
});

test('a new finding on an issue still waiting for a decision is announced once', async () => {
  const told = [];
  const data = [{ id: 'I-001', state: 'todo', num: '#001', title: '导出没反应', thread: [{ t: 'judge', kind: 'ask' }] }];
  const { issues } = load(data, { './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, issue: (id, title, body) => told.push([id, title, body]) } });
  await issues.pushIssues();
  data[0].thread = [...data[0].thread, { t: 'finding', kind: 'bug', text: '点击没有绑定。' }];
  await issues.pushIssues();
  assert.deepEqual(told, [['issue:I-001', '问题 #001 · AI 排查有了结论：是问题', '点击没有绑定。']]);
});

// 问题页：哪个终端、列表上的说法、按钮
function rendererApi() {
  const context = vm.createContext({ merging: {}, waiting: {}, running: {}, fixTerms: {}, $: () => ({}), window: {}, console, HtmlText, uiText: HtmlText.text });
  const dir = path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts');
  const src = ['issues.js', 'issue-thread.js'].map((f) => fs.readFileSync(path.join(dir, f), 'utf8')).join('\n');
  vm.runInContext(src + '\nthis.api = { iphrase, iWaitsForYou, probeRun, probeOpen, termRun, termName, judgeActions, findingCard, stripHtml, replyPlaceholder, probeLive, IS };', context);
  return { api: context.api, context };
}

test('the right side switches to the investigation session while there is one, and to the fix once a fix exists', () => {
  const { api, context } = rendererApi();
  const todo = { id: 'I-001', num: '#001', state: 'todo', kind: 'bug', thread: [], probe: { sessions: ['s'] } };
  assert.equal(api.probeRun(todo), 'probe:I-001');
  assert.equal(api.termRun(todo), 'probe:I-001');
  assert.equal(api.termName(todo), '排查会话');
  assert.equal(api.termRun({ ...todo, probe: null }), null); // no session: no switch at all
  assert.ok(api.stripHtml(todo).includes('排查会话</button>'));
  assert.ok(!api.stripHtml({ ...todo, probe: null }).includes('class="views"'));
  const fixing = { ...todo, state: 'fixing', fix: { id: 'F-001', state: 'fixing' } }; // both exist: the fix wins
  assert.equal(api.termRun(fixing), 'fix:F-001');
  assert.equal(api.termName(fixing), '修复会话');
  assert.equal(api.probeOpen(fixing), false);
  assert.ok(api.stripHtml(fixing).includes('修复会话</button>') && !api.stripHtml(fixing).includes('排查会话'));
  assert.equal(api.termRun({ ...todo, state: 'closed' }), null);
  assert.equal(api.termRun({ id: 'I-002', state: 'todo' }), null);
});

test('rows say 排查中 while the investigation works, and ask for an answer or a decision as appropriate', () => {
  const { api, context } = rendererApi();
  const mk = (extra) => ({ id: 'I-001', num: '#001', state: 'todo', kind: 'bug', thread: [], probe: { sessions: ['s'] }, ...extra });
  assert.deepEqual(Array.from(api.iphrase(mk())), ['等你决定', 'blue']);
  context.running['probe:I-001'] = true;
  assert.deepEqual(Array.from(api.iphrase(mk())), ['排查中', 'blue']);
  assert.equal(api.iWaitsForYou(mk()), false);
  delete context.running['probe:I-001'];
  context.waiting['probe:I-001'] = { kind: 'answer', text: '你点的是哪个按钮？' };
  assert.deepEqual(Array.from(api.iphrase(mk())), ['等你回答', 'amber']);
  assert.equal(api.iWaitsForYou(mk()), true); // counts on the 问题 icon
  context.waiting['probe:I-001'] = { kind: 'approve', text: '' };
  assert.deepEqual(Array.from(api.iphrase(mk())), ['等你批准', 'amber']);
  context.waiting['probe:I-001'] = { kind: 'reply', text: '查完了' };
  assert.deepEqual(Array.from(api.iphrase(mk())), ['等你决定', 'blue']); // 回复完了 and a finding: the usual 等你决定
  assert.equal(api.iWaitsForYou(mk()), false);
  assert.deepEqual(Array.from(api.iphrase(mk({ probe: null }))), ['等你决定', 'blue']); // a mark for a session that is not this issue's is ignored
  assert.deepEqual(Array.from(api.iphrase(mk({ kind: 'ask' }))), ['等你补充', 'amber']);
  assert.deepEqual(Array.from(api.iphrase(mk({ state: 'fixing', fix: { id: 'F-001' }, batch: 1 }))), ['修复中', 'blue']);
});

test('every judgement card on an issue waiting to be handled offers 交给 AI 排查, quietly; with a session it opens it', () => {
  const { api } = rendererApi();
  const mk = (extra) => ({ id: 'I-001', num: '#001', state: 'todo', thread: [], ...extra });
  for (const kind of ['bug', 'request', 'usage', 'ask']) {
    const html = api.judgeActions(mk({ kind }));
    assert.ok(html.includes('data-act="probe" class="quiet"'), kind);
    assert.ok(html.includes('交给 AI 排查'), kind);
  }
  assert.ok(api.judgeActions(mk({ kind: 'bug', deferred: true })).includes('data-act="probe"'));
  assert.ok(api.judgeActions(mk({ kind: 'bug', probe: { sessions: [] } })).includes('打开排查会话'));
  assert.equal(api.judgeActions(mk({ kind: 'bug', state: 'fixing' })), '');
  assert.equal(api.judgeActions(mk({ kind: 'bug', needs_triage: true })), ''); // still being judged
});

test('a finding card shows what was verified apart from what is inferred, with the usual buttons', () => {
  const { api } = rendererApi();
  const issue = { id: 'I-001', num: '#001', state: 'todo', kind: 'bug', thread: [] };
  const e = { t: 'finding', kind: 'bug', at: '2026-11-17 18:00', text: '点击没有绑定。', steps: '点导出', where: 'app.py', verified: '复现了', inferred: '没看提交记录' };
  const html = api.findingCard(issue, e, true);
  for (const needle of ['AI 排查结论 · 读过代码', '怎么复现', '问题在', '实际复现、验证过的', '只是推断、没验证的', '开始修复', '交给 AI 排查']) assert.ok(html.includes(needle), needle);
  assert.ok(api.findingCard(issue, e, false).includes('之前排查的结论'));
});

test('while the session is open the reply box says it goes to the session', () => {
  const { api, context } = rendererApi();
  const mk = { id: 'I-001', num: '#001', state: 'todo', kind: 'bug', thread: [], probe: { sessions: ['s'] } };
  assert.ok(!api.replyPlaceholder(mk).includes('排查会话'));
  context.fixTerms['probe:I-001'] = { dead: false };
  assert.ok(api.replyPlaceholder(mk).includes('补充给排查会话'));
  context.fixTerms['probe:I-001'].dead = true;
  assert.ok(!api.replyPlaceholder(mk).includes('排查会话'));
});
