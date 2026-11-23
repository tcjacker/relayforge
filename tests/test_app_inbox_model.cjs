// 程序消息面板的 HTML 不依赖 DOM，正文和属性都要转义。
const test = require('node:test');
const assert = require('node:assert/strict');
const InboxModel = require('../app/src/renderer/scripts/inbox-model');

const sample = { id: 'm-1', from: 'testing', state: 'queued', created: '2026-11-23T09:00:00+08:00', text: '测试交回了问题清单。' };

test('the panel shows the source, creation time, queued state and withdrawal control', () => {
  const html = InboxModel.render([sample]);
  assert.match(html, /<b>测试<\/b>/);
  assert.match(html, /<time datetime="2026-11-23T09:00:00\+08:00">[^<]+<\/time>/);
  assert.match(html, /排队中/);
  assert.match(html, /data-inbox-withdraw="m-1">撤回/);
  assert.match(html, /测试交回了问题清单。/);
  assert.match(InboxModel.render([]), /还没有程序消息/);
});

test('completed states do not offer withdrawal; delivery method is shown when sent', () => {
  const ways = { open: '开场白', stop: '停止钩子', prompt: '提交钩子', type: '终端粘贴' };
  for (const [via, label] of Object.entries(ways)) {
    const html = InboxModel.render([{ ...sample, state: 'sent', via }]);
    assert.ok(html.includes('已送出 · ' + label));
    assert.doesNotMatch(html, /data-inbox-withdraw/);
  }
  const history = InboxModel.render([{ ...sample, state: 'seen' }, { ...sample, id: 'm-2', state: 'dropped' }]);
  assert.match(history, /已撤回.*已读到/);
  assert.doesNotMatch(history, /data-inbox-withdraw/);
});

test('long bodies are collapsed by default, and untrusted text and attributes stay escaped', () => {
  const id = 'm-" onmouseover="bad', text = '<img src=x onerror=bad>&'.repeat(12);
  const message = { id, text, from: 'constructor', label: '<来源>', created: '"when"', state: 'queued' };
  const html = InboxModel.render([message]);
  assert.match(html, /<details data-inbox-body="m-&quot; onmouseover=&quot;bad">/);
  assert.match(html, /&lt;img src=x onerror=bad&gt;&amp;/);
  assert.match(html, /&lt;来源&gt;/);
  assert.match(html, /datetime="&quot;when&quot;"/);
  assert.doesNotMatch(html, /<img|<来源>| onmouseover="/);
  assert.match(InboxModel.render([message], new Set([id])), /data-inbox-body="[^"]+" open>/);
  assert.match(InboxModel.badge(id), /data-inbox-run="m-&quot; onmouseover=&quot;bad"/);
});
