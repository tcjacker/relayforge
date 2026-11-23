const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../app/src/renderer/scripts/session-history-model');

test('用量区分已统计、没有计数和没有记录的会话，回放只用于可读取的记录', () => {
  const html = model.usage({
    tokens: { total: 527 },
    groups: [{ kind: 'test', agent: 'codex', model: '<model>', total: 527 }],
    sessions: [
      { kind: 'test', session: 'S-<测试>', session_id: '" onclick="alert(1)', source: 'original', tokens: { total: 527 } },
      { kind: 'develop', session: 'S-无计数', session_id: 'no-usage', source: 'archive', tokens: null },
      { kind: 'intake', session: 'S-无记录', session_id: 'missing', source: null, tokens: null },
    ],
  });
  assert.match(html, /已统计 527 token/);
  assert.match(html, /2 个会话缺少用量/);
  assert.match(html, /测试 · Codex · &lt;model&gt;/);
  assert.match(html, /S-&lt;测试&gt;/);
  assert.match(html, /data-replay-session="&quot; onclick=&quot;alert\(1\)"/);
  assert.match(html, /没有 token 数据/);
  assert.match(html, /记录不可用/);
  assert.doesNotMatch(html, /data-replay-session="missing"/);
});

test('回放按轮次展示程序消息、工具状态和回复并转义所有动态字段', () => {
  const html = model.replay({
    source: 'archive', offset: 3, turns: [{
      when: '<时间>', speaker: 'program', said: '<script>程序</script>', reply: '<回复>',
      steps: [{ what: '<读>', detail: '"文件"', status: 'ok', times: 1 },
        { what: '跑', detail: 'false', status: 'failed', times: 2 },
        { what: '改', detail: 'test.js', status: 'refused', times: 1 }],
    }, { when: '', said: '<用户>', steps: [] }],
  });
  assert.match(html, /第 4 轮.*&lt;时间&gt;.*程序消息.*&lt;script&gt;程序.*&lt;读&gt;.*&quot;文件&quot;.*失败.*×2.*被拒绝.*&lt;回复&gt;/);
  assert.match(html, /第 5 轮.*用户.*&lt;用户&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(model.replay({ source: null }), /没有可读取的聊天记录/);
  assert.match(model.replay({ source: 'archive', turns: [] }), /没有可回放的轮次/);
  assert.match(model.replay({ error: '<错误>' }), /&lt;错误&gt;/);
});
