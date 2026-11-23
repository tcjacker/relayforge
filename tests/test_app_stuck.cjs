const test = require('node:test');
const assert = require('node:assert/strict');
const stuck = require('../app/src/main/stuck');

test('approval time is excluded from silence without resetting previous silence', () => {
  const run = {};
  const stopped = [];
  const limit = stuck.HEADLESS_SILENCE_MS;
  stuck.startHeadless(run, (reason) => stopped.push(reason), 0);
  stuck.watchHeadless(run, false, limit - 2);
  stuck.watchHeadless(run, true, limit - 2);
  const resumed = limit * 10;
  stuck.watchHeadless(run, true, resumed);
  stuck.watchHeadless(run, false, resumed);
  stuck.watchHeadless(run, false, resumed + 1);
  assert.deepEqual(stopped, []);
  stuck.watchHeadless(run, false, resumed + 2);
  stuck.watchHeadless(run, false, resumed + 3);
  assert.equal(stopped.length, 1);
  assert.match(stopped[0], /15 分钟没有输出/);
});

test('an interactive session gets one reminder per continuous stall, and only while running', () => {
  const run = { id: 'R-1', seen: 25 };
  const notices = [];
  const remind = (...args) => notices.push(args);
  const limit = stuck.INTERACTIVE_SILENCE_MS;
  const running = { phase: 'running', since: 0 };
  stuck.watchInteractive(run, running, 25, remind, limit - 1);
  stuck.watchInteractive(run, running, 30, remind, limit); // 要到线时聊天记录又变长了
  stuck.watchInteractive(run, running, null, remind, 2 * limit - 1);
  assert.deepEqual(notices, []);
  stuck.watchInteractive(run, running, 30, remind, 2 * limit);
  stuck.watchInteractive(run, running, 30, remind, 3 * limit);
  assert.equal(notices.length, 1);
  assert.deepEqual(notices[0].slice(0, 2), ['R-1', '会话可能卡住了']);
  assert.equal(notices[0][3], 'stuck');

  stuck.watchInteractive(run, running, 31, remind, 3 * limit);
  stuck.watchInteractive(run, running, 31, remind, 4 * limit);
  assert.equal(notices.length, 2);
  stuck.watchInteractive(run, { phase: 'approve', since: 4 * limit }, 31, remind, 5 * limit);
  const next = { phase: 'running', since: 5 * limit };
  run.seen = 31;
  stuck.watchInteractive(run, next, 31, remind, 5 * limit);
  stuck.watchInteractive(run, next, null, remind, 6 * limit);
  assert.equal(notices.length, 3);
});
