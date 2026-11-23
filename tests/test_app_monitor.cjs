// 后台记录（设置里的开关，默认关）：会话说完一轮时调用 rf.py monitor record，等批准的时间随下一次记录交过去，会话关掉时记下没说完的最后一轮。
const test = require('node:test');
const assert = require('node:assert/strict');
const locale = require('../app/src/main/locale');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const main = path.join(__dirname, '..', 'app', 'src', 'main');

function load(name, mocks) {
  const localRequire = createRequire(path.join(main, name + '.js'));
  const context = vm.createContext({ require: (id) => (Object.hasOwn(mocks, id) ? mocks[id] : localRequire(id)), module: { exports: {} }, process, Buffer, setTimeout, clearTimeout, console, Date });
  vm.runInContext(fs.readFileSync(path.join(main, name + '.js'), 'utf8'), context);
  return context.module.exports;
}

function harness(on, reads = []) {
  const calls = [];
  const run = { id: 'R-1', sessionId: 'sid-1', kind: 'develop', owner: 'R-1', agent: 'claude', root: '/wt', transcript: '/t.jsonl' };
  const monitor = load('monitor', {
    './backend': { uiText: locale.uiText,  rf: async (args, root) => { calls.push({ args: [...args], root }); return { ok: true, stdout: '{}' }; } },
    './settings': { get: () => { reads.push(1); return { monitor: on }; } },
    './state': { state: { runs: new Map([[run.id, run]]) } },
  });
  return { monitor, calls, run };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const value = (args, flag) => args[args.indexOf(flag) + 1];

test('nothing is recorded while the setting is off', async () => {
  const { monitor, calls, run } = harness(false);
  monitor.settled('R-1', 'reply');
  monitor.ended(run);
  await flush();
  assert.equal(calls.length, 0);
});

test('a finished turn is recorded with who the session is, and approval waits go with the next record', async () => {
  const { monitor, calls } = harness(true);
  monitor.settled('R-1', 'running');
  monitor.settled('R-1', 'approve');
  monitor.settled('R-1', 'approve'); // 同一次等待只算一次
  monitor.settled('R-1', 'running');
  monitor.settled('R-1', 'reply');
  monitor.settled('R-2', 'reply'); // 不是开着的会话
  await flush();
  assert.equal(calls.length, 1);
  const { args, root } = calls[0];
  assert.deepEqual(args.slice(0, 2), ['monitor', 'record']);
  assert.equal(root, '/wt');
  assert.deepEqual([value(args, '--session'), value(args, '--kind'), value(args, '--owner'), value(args, '--agent'), value(args, '--transcript')],
    ['sid-1', 'develop', 'R-1', 'claude', '/t.jsonl']);
  const waits = JSON.parse(value(args, '--waits'));
  assert.equal(waits.length, 1);
  assert.ok(waits[0].seconds >= 0 && waits[0].at);
  monitor.settled('R-1', 'answer');
  await flush();
  assert.equal(calls.length, 2);
  assert.ok(!calls[1].args.includes('--waits')); // 已经交过的不再交
});

test('closing a session records the unfinished last turn with the approval it was still waiting for', async () => {
  const { monitor, calls, run } = harness(true);
  monitor.settled('R-1', 'approve');
  monitor.ended(run);
  await flush();
  assert.equal(calls.length, 1);
  assert.ok(calls[0].args.includes('--closed'));
  assert.equal(JSON.parse(value(calls[0].args, '--waits')).length, 1);
});

test('the setting is off by default, saved with the tool, and kept when not given', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-monitor-'));
  try {
    const settings = load('settings', { './config': { RF_HOME: dir, AGENT: ['claude'] }, './cli-paths': { locateCli: () => ({}) } });
    assert.equal(settings.get().monitor, false);
    assert.equal(settings.save({ agent: 'claude', monitor: true }).monitor, true);
    assert.equal(settings.save({ agent: 'codex' }).monitor, true);
    assert.equal(settings.save({ agent: 'codex', monitor: false }).monitor, false);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('the switch is read once at startup and then follows what the settings dialog saves', async () => {
  const reads = [];
  const { monitor, calls } = harness(false, reads);
  monitor.settled('R-1', 'reply');
  assert.equal(monitor.enabled(), false);
  monitor.setEnabled(true);
  monitor.settled('R-1', 'reply');
  monitor.settled('R-1', 'answer');
  await flush();
  assert.equal(calls.length, 2);
  assert.equal(reads.length, 1); // 收到事件时不再读设置文件
  monitor.settled('R-1', 'approve');
  monitor.setEnabled(false); // 关掉：丢下还没交的等批准，之后什么都不记
  monitor.setEnabled(true);
  monitor.settled('R-1', 'reply');
  await flush();
  assert.equal(calls.length, 3);
  assert.ok(!calls[2].args.includes('--waits'));
});
