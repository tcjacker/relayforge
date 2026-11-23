// 会话收件箱（app/src/main/inbox.js）：程序发给会话的话先记下来、标来源，只在会话说完了或停着时送；
// 在干活、在问你问题、在等你批准时排队；没开着的让界面打开它、打开时随开场白交过去；同一件事只发一次；重启不丢；聊天记录里出现编号算读到了。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const main = path.join(__dirname, '..', 'app', 'src', 'main');
const stuck = require(path.join(main, 'stuck'));

function harness(home = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-inbox-'))) {
  const typed = [], sent = [], phases = new Map(), opened = [], reminders = [];
  const state = { project: '/project', runs: new Map() };
  const mocks = {
    './config': { RF_HOME: home },
    './state': { state, send: (channel, payload) => sent.push([channel, payload]) },
    './attention': { phaseOf: (id) => phases.get(id) || null, setPhase: (id, p) => phases.set(id, p), remind: (...args) => reminders.push(args) },
    './stuck': stuck,
    './locale': require(path.join(main, 'locale')),
    './sessions': { type: (run, text) => typed.push([run.id, text]), open: (o) => opened.push(o) },
  };
  const context = vm.createContext({ require: (id) => (Object.hasOwn(mocks, id) ? mocks[id] : require(id)), module: { exports: {} }, console });
  vm.runInContext(fs.readFileSync(path.join(main, 'inbox.js'), 'utf8'), context);
  const inbox = context.module.exports;
  const open = (id, phase, transcript) => { state.runs.set(id, { id, sessionId: 'sid-' + id, transcript }); phases.set(id, phase); };
  return { inbox, typed, sent, phases, state, open, home, opened, reminders };
}

test('an unread message retries once in the same ready terminal, and warns once even if the session has since closed', (t) => {
  const h = harness();
  t.after(() => fs.rmSync(h.home, { recursive: true, force: true }));
  const id = 'R-1';
  const pathToRecord = path.join(h.home, 'transcript.jsonl');
  fs.writeFileSync(pathToRecord, '');
  h.open(id, 'idle', pathToRecord);
  const m = h.inbox.post({ to: id, text: '再核对一次' });
  const interval = stuck.RECEIPT_WAIT_MS;
  const due = Date.parse(m.sent) + interval;
  const find = () => pathToRecord;
  h.phases.set(id, 'reply');
  h.inbox.watchDelivery(find, due - 1);
  const run = h.state.runs.get(id);
  for (const phase of ['running', 'approve', 'answer', 'starting']) {
    h.phases.set(id, phase);
    h.inbox.watchDelivery(find, due);
  }
  h.phases.set(id, 'idle');
  run.headless = true;
  h.inbox.watchDelivery(find, due);
  run.headless = false;
  run.sessionId = 'another-session';
  h.inbox.watchDelivery(find, due);
  assert.equal(h.typed.length, 1);
  run.sessionId = m.session;
  h.inbox.watchDelivery(find, due);
  assert.deepEqual(h.typed[1], h.typed[0]); // 同一个编号、同一段话
  assert.equal(h.typed.length, 2);
  assert.equal(m.retried, new Date(due).toISOString());
  assert.equal(h.phases.get(id), 'running');

  const restarted = harness(h.home);
  restarted.inbox.watchDelivery(find, due + interval - 1);
  assert.deepEqual(restarted.reminders, []);
  restarted.inbox.watchDelivery(find, due + interval);
  assert.equal(restarted.reminders.length, 1);
  assert.match(restarted.reminders[0][2], new RegExp(m.id));
  assert.equal(restarted.reminders[0][3], 'unread');
  assert.equal(restarted.inbox.messagesFor(id)[0].retryWarned, new Date(due + interval).toISOString());
  const again = harness(h.home);
  again.open(id, 'idle', pathToRecord);
  again.inbox.watchDelivery(find, due + 2 * interval);
  assert.deepEqual(again.reminders, []);
  assert.deepEqual(again.typed, []);
});

test('a receipt present at the retry or reminder deadline suppresses that action', (t) => {
  const h = harness();
  t.after(() => fs.rmSync(h.home, { recursive: true, force: true }));
  const id = 'R-1';
  const pathToRecord = path.join(h.home, 'transcript.jsonl');
  const find = () => pathToRecord;
  const interval = stuck.RECEIPT_WAIT_MS;
  h.open(id, 'idle', pathToRecord);
  const first = h.inbox.post({ to: id, text: '第一条' });
  fs.writeFileSync(pathToRecord, JSON.stringify({ text: first.id }) + '\n');
  h.phases.set(id, 'idle');
  h.inbox.watchDelivery(find, Date.parse(first.sent) + interval);
  assert.equal(h.typed.length, 1);
  assert.equal(first.state, 'seen');

  const second = h.inbox.post({ to: id, text: '第二条' });
  h.phases.set(id, 'idle');
  h.inbox.watchDelivery(find, Date.parse(second.sent) + interval);
  assert.equal(h.typed.length, 3);
  fs.appendFileSync(pathToRecord, JSON.stringify({ text: second.id }) + '\n');
  h.inbox.watchDelivery(find, Date.parse(second.retried) + interval);
  assert.deepEqual(h.reminders, []);
  assert.equal(second.state, 'seen');
});

test('a busy session gets nothing until it has finished its turn, then everything waiting in one go', () => {
  const h = harness();
  h.open('R-1', 'running');
  const first = h.inbox.post({ to: 'R-1', from: 'testing', text: '测试交回了 2 条问题' });
  h.inbox.post({ to: 'R-1', from: 'merge', text: '先同步 main' });
  assert.equal(h.typed.length, 0);
  assert.equal(first.state, 'queued');
  for (const phase of ['approve', 'answer']) { // 在等你批准、在问你问题：绝不往里输
    h.phases.set('R-1', phase);
    h.inbox.phaseChanged('R-1', phase);
    assert.equal(h.typed.length, 0);
  }
  h.phases.set('R-1', 'reply');
  h.inbox.phaseChanged('R-1', 'reply');
  assert.equal(h.typed.length, 1);
  const [to, text] = h.typed[0];
  assert.equal(to, 'R-1');
  assert.match(text, new RegExp(`^〔RelayForge 程序 · 测试 · ${first.id}〕\\n测试交回了 2 条问题\\n\\n〔RelayForge 程序 · 合并 · m-[0-9a-f]{6}〕\\n先同步 main$`));
  assert.equal(h.phases.get('R-1'), 'running'); // 交过去了：它这一轮说完之前不再送
  assert.deepEqual(JSON.parse(JSON.stringify(h.inbox.messagesFor('R-1').map((m) => [m.state, m.session]))), [['sent', 'sid-R-1'], ['sent', 'sid-R-1']]);
  h.inbox.phaseChanged('R-1', 'reply');
  assert.equal(h.typed.length, 1); // 没有新的就不再输
});

test('an idle session gets it right away; the same thing is sent only once', () => {
  const h = harness();
  h.open('test:R-1', 'idle');
  h.inbox.post({ to: 'test:R-1', from: 'testing', key: 'kick:R-1:2', text: '再测一轮' });
  h.inbox.post({ to: 'test:R-1', from: 'testing', key: 'kick:R-1:2', text: '再测一轮' });
  assert.equal(h.typed.length, 1);
  assert.equal(h.inbox.messagesFor('test:R-1').length, 1);
});

test('a session that is not open is asked to open, and gets what waited in its opening prompt', () => {
  const h = harness();
  h.inbox.post({ to: 'R-1', from: 'testsets', text: '不同意改动 1' });
  h.inbox.post({ to: 'test:R-1', from: 'testing', text: '再测一轮' });
  h.inbox.post({ to: 'fix:F-1', from: 'issues', text: '还有问题' });
  h.inbox.post({ to: 'probe:I-1', from: 'issues', text: '用户补充了一句', open: false });
  assert.deepEqual(JSON.parse(JSON.stringify(h.sent.filter(([channel]) => channel !== 'inbox:changed'))), [['tests:say', { id: 'R-1' }]]);
  // 测试会话的再测一轮、问题的修复会话：不让界面开终端，在后台无界面跑一轮（headless.js）
  assert.deepEqual(JSON.parse(JSON.stringify(h.opened)), [{ id: 'test:R-1', headless: true }, { id: 'fix:F-1', headless: true }]);
  assert.equal(h.typed.length, 0);
  const opening = h.inbox.takeForOpen('R-1', 'new-sid');
  assert.match(opening, /^〔RelayForge 程序 · 测试集 · m-[0-9a-f]{6}〕\n不同意改动 1$/);
  assert.equal(h.inbox.messagesFor('R-1')[0].session, 'new-sid');
  assert.equal(h.inbox.takeForOpen('R-1', 'new-sid'), '');
});

test('what waits survives a restart; seeing the id in the transcript marks it read', () => {
  const h = harness();
  h.open('R-1', 'running');
  const m = h.inbox.post({ to: 'R-1', from: 'testing', text: '问题清单' });
  const again = harness(h.home); // 应用重启：从本机文件读回来
  const transcript = path.join(h.home, 't.jsonl');
  fs.writeFileSync(transcript, '{"type":"user"}\n');
  again.open('R-1', 'reply', transcript);
  again.inbox.flush('R-1');
  assert.equal(again.typed.length, 1);
  assert.equal(again.inbox.messagesFor('R-1')[0].state, 'sent');
  fs.appendFileSync(transcript, JSON.stringify({ type: 'user', message: { content: `〔RelayForge 程序 · 测试 · ${m.id}〕\n问题清单` } }) + '\n');
  again.inbox.markSeen(again.state.runs.get('R-1'));
  const seen = again.inbox.messagesFor('R-1')[0];
  assert.equal(seen.state, 'seen');
  assert.ok(seen.seen);
});

test('nothing to say, or no project, is not recorded', () => {
  const h = harness();
  assert.equal(h.inbox.post({ to: 'R-1', text: '   ' }), null);
  h.state.project = null;
  assert.equal(h.inbox.post({ to: 'R-1', text: '话' }), null);
});

const deliveredBy = (h, lines) => fs.appendFileSync(h.inbox.envFor('/project').RELAYFORGE_INBOX + '.delivered', lines.map((l) => l.join('\t') + '\n').join(''));

test('a message for an open session records which session it waits for, and the hook finds the file through the environment', () => {
  const h = harness();
  h.open('R-1', 'running');
  const m = h.inbox.post({ to: 'R-1', from: 'conflicts', text: '主分支上会冲突' });
  assert.deepEqual([m.session, m.label], ['sid-R-1', '冲突']);
  const file = h.inbox.envFor('/project').RELAYFORGE_INBOX;
  assert.ok(file.startsWith(h.home) && file.endsWith('.json'));
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8'))[0].session, 'sid-R-1'); // 钩子读的就是这个文件
});

test('what the Stop hook handed over is not typed again', () => {
  const h = harness();
  h.open('R-1', 'running');
  const m = h.inbox.post({ to: 'R-1', from: 'testing', text: '问题清单' });
  deliveredBy(h, [[m.id, 'stop', '2026-11-22T10:00:00+08:00']]); // 这一轮说完时钩子交了
  h.phases.set('R-1', 'reply');
  h.inbox.phaseChanged('R-1', 'reply');
  assert.equal(h.typed.length, 0);
  assert.deepEqual(JSON.parse(JSON.stringify(h.inbox.messagesFor('R-1').map((x) => [x.state, x.via]))), [['sent', 'stop']]);
});

test('a hooked session that has stopped gets only the wake line; the text goes in through the prompt hook', () => {
  const h = harness();
  h.open('R-1', 'idle');
  h.state.runs.get('R-1').hooked = true;
  const m = h.inbox.post({ to: 'R-1', from: 'testing', text: '再测一轮' });
  assert.equal(h.typed.length, 1);
  assert.ok(h.typed[0][1].startsWith('〔RelayForge〕有新的程序消息'));
  assert.doesNotMatch(h.typed[0][1], /再测一轮/);
  assert.equal(h.inbox.messagesFor('R-1')[0].state, 'queued'); // 等钩子交
  deliveredBy(h, [[m.id, 'prompt', '2026-11-22T10:00:00+08:00']]);
  h.phases.set('R-1', 'reply');
  h.inbox.phaseChanged('R-1', 'reply');
  assert.equal(h.typed.length, 1);
  assert.equal(h.inbox.messagesFor('R-1')[0].via, 'prompt');
});

test('if the hook did not hand it over after the wake line, the text is typed after all', () => {
  const h = harness();
  h.open('R-1', 'idle');
  h.state.runs.get('R-1').hooked = true;
  h.inbox.post({ to: 'R-1', from: 'testing', text: '再测一轮' });
  h.phases.set('R-1', 'reply');
  h.inbox.phaseChanged('R-1', 'reply'); // 唤醒那一轮说完了，钩子却没交
  assert.equal(h.typed.length, 2);
  assert.match(h.typed[1][1], /再测一轮/);
  assert.equal(h.inbox.messagesFor('R-1')[0].via, 'type');
});

test('a session running in the background is never typed into; what it did not get is run again once it has finished', () => {
  const h = harness();
  h.open('fix:F-1', 'reply');
  h.state.runs.get('fix:F-1').headless = true;
  h.inbox.post({ to: 'fix:F-1', from: 'issues', text: '还有问题' });
  assert.equal(h.typed.length, 0); // 没有终端可输：这一轮说完时 Stop 钩子交给它
  assert.equal(h.inbox.afterHeadless('fix:F-1'), false); // 还在跑：不另开
  h.state.runs.delete('fix:F-1');
  assert.equal(h.inbox.afterHeadless('fix:F-1'), true);
  assert.deepEqual(JSON.parse(JSON.stringify(h.opened)), [{ id: 'fix:F-1', headless: true }]);
  h.inbox.takeForOpen('fix:F-1', 'sid');
  assert.equal(h.inbox.afterHeadless('fix:F-1'), false); // 都交出去了：不再跑
});

test('posting, opening, reading and absorbing hook deliveries notify the corresponding session', (t) => {
  const h = harness();
  t.after(() => fs.rmSync(h.home, { recursive: true, force: true }));
  const to = 'R-1', testTo = 'test:R-1';
  const first = h.inbox.post({ to, text: '开发消息', open: false });
  const second = h.inbox.post({ to: testTo, text: '测试消息', open: false });
  const third = h.inbox.post({ to: testTo, text: '下一轮', open: false });
  assert.deepEqual([...h.inbox.queuedForProject()], [to, testTo]);
  h.inbox.takeForOpen(to, 'new-sid');
  const transcript = path.join(h.home, 'session.jsonl');
  fs.writeFileSync(transcript, first.id);
  h.inbox.markSeen({ id: to, sessionId: 'new-sid', transcript }); // 回执只认发给这个会话的（stuck 分支加的检查）
  deliveredBy(h, [[second.id, 'stop'], [third.id, 'prompt']]);
  h.inbox.absorbDelivered(h.state.project);
  h.inbox.absorbDelivered(h.state.project);
  assert.deepEqual(JSON.parse(JSON.stringify(h.sent)), [
    ['inbox:changed', { id: to, queued: true }],
    ['inbox:changed', { id: testTo, queued: true }],
    ['inbox:changed', { id: testTo, queued: true }],
    ['inbox:changed', { id: to, queued: false }],
    ['inbox:changed', { id: to, queued: false }],
    ['inbox:changed', { id: testTo, queued: false }],
  ]);
  assert.equal(h.inbox.messagesFor(to)[0].state, 'seen');
  assert.deepEqual([...h.inbox.queuedForProject()], []);
});

test('withdrawal checks the session and current state, survives restart, and prevents delivery', (t) => {
  const h = harness();
  t.after(() => fs.rmSync(h.home, { recursive: true, force: true }));
  const to = 'fix:F-1';
  h.open(to, 'running');
  const canceled = h.inbox.post({ to, text: '排队中的消息' });
  const delivered = h.inbox.post({ to, text: '由钩子交付的消息' });
  assert.equal(h.inbox.withdraw('probe:I-1', canceled.id), false);
  assert.equal(h.inbox.withdraw(to, 'm-missing'), false);
  deliveredBy(h, [[delivered.id, 'stop']]);
  assert.equal(h.inbox.withdraw(to, delivered.id), false);
  assert.equal(h.inbox.withdraw(to, canceled.id), true);
  assert.equal(h.inbox.withdraw(to, canceled.id), false);
  assert.equal(h.sent.at(-1)[0], 'inbox:changed');
  assert.equal(h.sent.at(-1)[1].queued, false);
  const again = harness(h.home);
  assert.deepEqual(Array.from(again.inbox.messagesFor(to), (m) => m.state), ['dropped', 'sent']);
  again.open(to, 'reply');
  assert.equal(again.inbox.flush(to), 0);
  assert.equal(again.inbox.takeForOpen(to, 'new-sid'), '');
  assert.equal(again.inbox.afterHeadless(to), false);
});

test('a receipt from a hook already in flight takes precedence over a withdrawal', (t) => {
  const h = harness();
  t.after(() => fs.rmSync(h.home, { recursive: true, force: true }));
  const to = 'probe:I-1';
  h.open(to, 'running');
  const m = h.inbox.post({ to, text: '问题补充' });
  assert.equal(h.inbox.withdraw(to, m.id), true);
  assert.equal(h.inbox.messagesFor(to)[0].state, 'dropped');
  deliveredBy(h, [[m.id, 'stop']]);
  assert.equal(h.inbox.messagesFor(to)[0].state, 'sent');
  assert.equal(h.inbox.withdraw(to, m.id), false);
});
