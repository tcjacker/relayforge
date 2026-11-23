// 前置需求在界面上的说法（app/src/renderer/scripts/deps-model.js）：看板卡片、左侧列表、鼠标停上去串起前后、
// 「＋ 添加前置需求」能选哪些、立项和完成被拦的说明、立项时同步主线的结果。数据的样子和 rf.py req list --json 一样。
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const D = require(path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts', 'deps-model.js'));

const dep = (id, title, state, extra = {}) => ({ id, title, state, stage: state === 'draft' ? 'talk' : state === 'done' ? 'done' : state === 'dropped' ? 'dropped' : 'build', done: 2, steps: 4, finished: [], ...extra });
const req = (id, title, extra = {}) => ({ id, title, state: 'draft', stage: 'talk', depends_on: [], waiting: [], dependents: [], moves: {}, ...extra });

test('a card says what it waits for, that a prerequisite was cancelled, or that it can start now', () => {
  const a = dep('R-a', '截止日期', 'open');
  const one = req('R-b', '每日汇总', { depends_on: [a], waiting: ['R-a'] });
  assert.deepEqual(D.cardNotes(one).map((n) => [n.cls, n.text]), [['dep-wait', '⏳ 等《截止日期》']]);
  assert.match(D.cardNotes(one)[0].title, /确认完成后才能开始开发/);
  const two = req('R-c', '周报', { depends_on: [a, dep('R-x', '导出', 'draft')], waiting: ['R-a', 'R-x'] });
  assert.equal(D.cardNotes(two)[0].text, '⏳ 等 2 个需求');
  const gone = req('R-d', '汇总', { depends_on: [dep('R-a', '截止日期', 'dropped')], waiting: ['R-a'] });
  assert.deepEqual(D.cardNotes(gone).map((n) => n.cls), ['dep-bad']);
  assert.equal(D.listNote(gone), '前置需求已取消');
  const free = req('R-e', '汇总', { depends_on: [dep('R-a', '截止日期', 'done')], waiting: [] });
  assert.deepEqual(D.cardNotes(free).map((n) => n.text), ['● 可以开始了']);
  assert.equal(D.listNote(free), '可以开始了');
  assert.equal(D.ready({ ...free, state: 'open' }), false, 'once started it is not "can start" any more');
  assert.deepEqual(D.cardNotes(req('R-f', '没依赖')), []);
  assert.equal(D.listNote(req('R-f', '没依赖')), '');
});

test('the requirement others wait for says so; finished and cancelled ones show nothing', () => {
  const a = req('R-a', '截止日期', { state: 'open', stage: 'build', dependents: [{ id: 'R-b', title: '每日汇总', state: 'draft', ready: false }] });
  assert.deepEqual(D.cardNotes(a).map((n) => [n.cls, n.text, n.title]), [['dep-hold', '⇢ 有 1 个需求在等它', '《每日汇总》']]);
  assert.deepEqual(D.cardNotes({ ...a, state: 'done' }), []);
  assert.deepEqual(D.cardNotes({ ...a, state: 'dropped', waiting: ['R-x'], depends_on: [dep('R-x', 'x', 'open')] }), []);
  assert.deepEqual(D.related(req('R-b', 'b', { depends_on: [dep('R-a', 'a', 'open')], dependents: [{ id: 'R-c' }] })), { pre: ['R-a'], post: ['R-c'] });
});

test('adding a prerequisite offers the open requirements first and says why the others cannot be chosen', () => {
  const me = req('R-me', '每周完成趋势', { depends_on: [dep('R-old', '已经等着的', 'open')] });
  const all = [
    me,
    req('R-old', '已经等着的', { state: 'open', stage: 'build' }),
    req('R-json', '导出任务为 JSON', { state: 'open', stage: 'check' }),
    req('R-done', '截止日期', { state: 'done', stage: 'done' }),
    req('R-drop', '放弃的', { state: 'dropped', stage: 'dropped' }),
    req('R-tag', '给任务增加标签', { depends_on: [dep('R-mid', '中间', 'draft')] }),
    req('R-mid', '中间', { depends_on: [dep('R-me', '每周完成趋势', 'draft')] }), // R-tag → R-mid → R-me：选 R-tag 会互相等待
  ];
  const options = D.pickerOptions(me, all, '');
  assert.deepEqual(options.filter((o) => !o.why).map((o) => o.id), ['R-json']);
  const why = Object.fromEntries(options.map((o) => [o.id, o.why]));
  assert.equal(why['R-me'], '就是这个需求自己');
  assert.equal(why['R-old'], '已经是前置需求');
  assert.equal(why['R-done'], '已完成：不用等');
  assert.equal(why['R-drop'], '已取消：等不到它完成');
  assert.equal(why['R-tag'], '它在等这个需求，选了会互相等待');
  assert.equal(why['R-mid'], '它在等这个需求，选了会互相等待');
  assert.equal(options.find((o) => o.id === 'R-json').stage, '待验收');
  assert.deepEqual(D.pickerOptions(me, all, 'json').map((o) => o.id), ['R-json'], 'search by title');
  assert.deepEqual(D.pickerOptions(me, all, 'r-tag').map((o) => o.id), ['R-tag'], 'and by id');
  assert.deepEqual(options.filter((o) => o.cycle).map((o) => o.id).sort(), ['R-mid', 'R-tag'], 'only the ones that would wait in a circle offer to split instead');
});

test('splitting away the part that needs a prerequisite: what the session is asked to propose', () => {
  const d = { id: 'R-20261118-abcd', title: '统一的日期工具' };
  const take = D.splitAway(d, false);
  assert.match(take, /《统一的日期工具》还没完成，这个需求在等它。/);
  assert.match(take, /takes 里写 \["R-20261118-abcd"\]，不写的话这个需求还是要等/, 'the goal: the new one takes the wait over, so this one can start');
  assert.doesNotMatch(take, /order 写/, 'the order is the AI\'s call, not prescribed');
  assert.match(take, /每条先后都在 needs 里写清要用到对方的什么/);
  assert.match(take, /拆不开，直接告诉我，不要硬拆/);
  assert.match(take, /等我在右侧同意/);
  assert.match(D.splitAway({ ...d, need: 'parseDate' }, false), /这个需求在等它（要用到它的parseDate）/, 'what is needed from it, when it was written');
  const loop = D.splitAway(d, true);
  assert.match(loop, /设了会互相等待/);
  assert.match(loop, /waits 里写 \["R-20261118-abcd"\]，这个需求不设这条前置需求/, 'nothing to take over: the new one waits for it instead');
  assert.doesNotMatch(loop, /order 写/);
});

test('starting and finishing say what they wait for, with the reason the program worked out', () => {
  const waiting = req('R-b', '每日汇总', { depends_on: [dep('R-a', '截止日期', 'open')], waiting: ['R-a'], moves: { build: '要等《截止日期》确认完成后才能开始开发' } });
  assert.equal(D.startBlock(waiting), '要等《截止日期》确认完成后才能开始开发');
  assert.equal(D.startBlock(req('R-c', '没依赖', { moves: { build: '还没有验收条件，先把需求聊到「可验收」' } })), '', 'the acceptance-condition note is not ours');
  const open = { ...waiting, state: 'open', stage: 'check', moves: { done: '要等《截止日期》确认完成后才能确认完成' } };
  assert.equal(D.doneBlock(open), '要等《截止日期》确认完成后才能确认完成');
  assert.equal(D.doneBlock({ ...open, waiting: [] }), '');
});

test('starting reports what was brought in from the main branch', () => {
  const started = req('R-b', '每日汇总', { state: 'open', depends_on: [dep('R-a', '截止日期', 'done')], start_sync: { base: 'main', commits: 6, ok: true, files: [] } });
  assert.deepEqual(D.syncNote(started), { cls: 'dep-ok', text: '✓ 开始开发时已把 main 的最新内容同步进这个需求的分支，包含《截止日期》（6 个提交）。' });
  assert.match(D.syncNote({ ...started, start_sync: { base: 'main', commits: 2, ok: false, files: ['a.py', 'b.py'] } }).text, /有冲突（a\.py、b\.py），已撤回，交给开发会话处理/);
  assert.equal(D.syncNote(req('R-c', 'x')), null);
  assert.equal(D.depState(dep('R-a', 'a', 'open')), '进行中 2/4');
  assert.equal(D.depState(dep('R-a', 'a', 'done')), '已完成');
});

test('requirements linked by prerequisites share a folder in the list, prerequisites first, named after the one everyone waits for', () => {
  const link = (id) => ({ id, title: id, state: 'open' });
  const all = [
    req('R-trend', '每周完成趋势', { created: '2026-11-20', depends_on: [link('R-json')] }),
    req('R-json', '导出任务为 JSON', { created: '2026-11-18' }),
    req('R-mail', '每日汇总', { created: '2026-11-21', depends_on: [link('R-trend'), link('R-date')] }),
    req('R-date', '截止日期', { created: '2026-11-19' }),
    req('R-tag', '给任务增加标签', { created: '2026-11-17' }), // 没有依赖：不进文件夹
    req('R-a', 'A', { created: '2026-11-01', depends_on: [link('R-b')] }), // 旧数据里的环：不卡住，按创建时间排
    req('R-b', 'B', { created: '2026-11-02', depends_on: [link('R-a')] }),
  ];
  const groups = D.groups(all);
  assert.equal(groups.length, 2);
  const big = groups.find((g) => g.ids.includes('R-json'));
  assert.deepEqual(big.ids, ['R-json', 'R-date', 'R-trend', 'R-mail'], 'a prerequisite always comes before what waits for it');
  assert.deepEqual([big.key, big.title], ['R-json', '导出任务为 JSON'], 'two roots (JSON, 截止日期): the oldest names the folder');
  assert.ok(!groups.some((g) => g.ids.includes('R-tag')));
  assert.deepEqual(groups.find((g) => g.ids.includes('R-a')).ids, ['R-a', 'R-b']);
  assert.deepEqual(D.groups([req('R-x', 'x', { depends_on: [link('R-gone')] })]), [], 'a prerequisite not in the list does not make a folder');
});
