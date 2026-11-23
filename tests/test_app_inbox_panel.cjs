// 用实际的界面脚本核对切换会话时的异步读取和排队标记。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('the panel ignores older responses after switching sessions and refreshes only the affected inbox', async () => {
  const calls = [], pending = [], queuedBadge = { dataset: { inboxRun: 'test:R-1' }, hidden: true };
  let onChanged;
  function element() {
    const summary = {}, list = { querySelectorAll: () => [], replaceChildren() { this.innerHTML = ''; } };
    return {
      dataset: {}, hidden: true, open: false, events: {},
      querySelector: (selector) => selector === 'summary' ? summary : list,
      querySelectorAll: () => [],
      addEventListener(name, fn) { this.events[name] = fn; },
    };
  }
  const panels = { 'session-inbox': element(), 'issue-inbox': element() };
  const shell = {
    inboxQueued: () => Promise.resolve([]),
    onInboxChanged: (fn) => { onChanged = fn; },
    inboxList: (id) => { calls.push(id); return new Promise((resolve) => pending.push(resolve)); },
  };
  const ctx = vm.createContext({
    document: { querySelectorAll: () => [queuedBadge] },
    $: (id) => panels[id], window: { shell }, console, refit() {}, refitFix() {},
    ResizeObserver: class { observe() {} },
  });
  const dir = path.join(__dirname, '..', 'app', 'src', 'renderer', 'scripts');
  for (const script of ['html-text', 'inbox-model', 'inbox-panel']) {
    vm.runInContext(fs.readFileSync(path.join(dir, script + '.js'), 'utf8'), ctx);
  }

  ctx.wireInbox();
  await new Promise(setImmediate);
  const panel = panels['session-inbox'];
  ctx.showInbox('session-inbox', 'R-1');
  panel.open = true;
  const first = panel.events.toggle();
  ctx.showInbox('session-inbox', 'test:R-1');
  panel.open = true;
  const second = panel.events.toggle();
  pending[1]([{ id: 'm-2', text: '来自测试会话', state: 'queued' }]);
  await second;
  pending[0]([{ id: 'm-1', text: '来自开发会话', state: 'seen' }]);
  await first;
  const list = panel.querySelector('.inbox-list');
  assert.match(list.innerHTML, /来自测试会话/);
  assert.doesNotMatch(list.innerHTML, /来自开发会话/);

  onChanged({ id: 'fix:F-1', queued: true });
  assert.deepEqual(calls, ['R-1', 'test:R-1']);
  onChanged({ id: 'test:R-1', queued: true });
  assert.equal(queuedBadge.hidden, false);
  assert.deepEqual(calls, ['R-1', 'test:R-1', 'test:R-1']);
  pending[2]([{ id: 'm-2', text: '来自测试会话', state: 'sent', via: 'prompt' }]);
  await new Promise(setImmediate);
  assert.match(list.innerHTML, /已送出 · 提交钩子/);
});
