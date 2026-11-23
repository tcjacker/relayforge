// 用量和回放由后端计算，这里只把数据渲染成可读内容。
var SessionHistoryModel = (function () {
  var { esc, text } = typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text');
  var kinds = {
    intake: text('立项', 'Planning'), develop: text('开发', 'Development'), test: text('测试', 'Testing'),
    fix: text('修复', 'Fix'), probe: text('排查', 'Investigation'), map: text('大图', 'Project map'), chat: text('聊天', 'Chat'),
  };
  var status = {
    failed: text('失败', 'Failed'), refused: text('被拒绝', 'Rejected'), pending: text('没有结果', 'No result'),
  };
  var kindOf = (kind) => Object.hasOwn(kinds, kind) ? kinds[kind] : kind;
  var amount = (row) => esc(row.total.toLocaleString()) + ' token';
  var modelName = (row) => (row.agent === 'claude' ? 'Claude Code' : esc(row.agent === 'codex' ? 'Codex' : row.agent)) +
    ' · ' + esc(row.model || text('模型未知', 'Unknown model'));

  function usage(data) {
    var heading = '<div class="trace-head"><b>' + text('用量', 'Usage') + '</b>';
    if (!data) return heading + '<span>' + text('正在读取…', 'Loading…') + '</span></div>';
    if (data.error) return heading + '<span>' + esc(data.error) + '</span></div>';
    var sessions = data.sessions || [], groups = data.groups || [];
    var missing = sessions.filter((s) => !s.tokens).length;
    var total = groups.length ? text('已统计 ', 'Recorded ') + amount(data.tokens) : text('没有可用的 token 数据', 'No token data');
    var h = heading + '<span>' + total + '</span>' + (missing ? '<span>' + esc(missing) +
      text(' 个会话缺少用量', ' sessions lack usage data') + '</span>' : '') + '</div>';
    if (!sessions.length) return h + '<p>' + text('还没有会话', 'No sessions yet') + '</p>';
    h += '<ul class="usage-sessions">' + sessions.map((s) => {
      var value = s.tokens ? amount(s.tokens) : s.source ? text('没有 token 数据', 'No token data') : text('记录不可用', 'Record unavailable');
      var replay = s.source ? '<button type="button" class="quiet" data-replay-session="' + esc(s.session_id) + '">' +
        text('回放', 'Replay') + '</button>' : '';
      return '<li><span>' + esc(kindOf(s.kind)) + ' · <span class="mono">' + esc(s.session) + '</span></span><span>' +
        value + replay + '</span></li>';
    }).join('') + '</ul>';
    if (groups.length) {
      h += '<details><summary>' + text('按会话种类和模型', 'By session type and model') + '</summary><ul>' + groups.map((r) =>
        '<li><span>' + esc(kindOf(r.kind)) + ' · ' + modelName(r) + '</span><span>' + amount(r) + '</span></li>').join('') + '</ul></details>';
    }
    return h;
  }

  function replay(data) {
    if (data.error) return '<p role="alert">' + esc(data.error) + '</p>';
    if (!data.source) return '<p>' + text('本机没有可读取的聊天记录。', 'No session record is available on this computer.') + '</p>';
    if (!data.turns.length) return '<p>' + text('没有可回放的轮次。', 'No turns to replay.') + '</p>';
    return data.turns.map((turn, index) => {
      var speaker = turn.speaker === 'program' ? text('程序消息', 'Program message') : text('用户', 'User');
      var n = data.offset + index + 1;
      var h = '<section class="replay-turn"><h3>' + text('第 ', 'Turn ') + esc(n) + text(' 轮', '') +
        (turn.when ? ' · <time>' + esc(turn.when) + '</time>' : '') + '</h3>';
      if (turn.said) h += '<p><b>' + speaker + '</b></p><p class="replay-message">' + esc(turn.said) + '</p>';
      if (turn.steps.length) h += '<ol>' + turn.steps.map((step) => {
        var label = Object.hasOwn(status, step.status) ? status[step.status] : '';
        return '<li><b>' + esc(step.what) + '</b> <code>' + esc(step.detail) + '</code>' +
          (label ? ' <span class="replay-status">' + esc(label) + '</span>' : '') +
          (step.times > 1 ? ' ×' + esc(step.times) : '') + '</li>';
      }).join('') + '</ol>';
      if (turn.reply) h += '<p><b>' + text('AI 回复', 'AI reply') + '</b></p><p class="replay-message">' + esc(turn.reply) + '</p>';
      return h + '</section>';
    }).join('');
  }

  return { usage, replay };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = SessionHistoryModel;
