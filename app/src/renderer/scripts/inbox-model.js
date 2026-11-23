// 程序消息面板的内容由纯函数生成，正文只作为文字显示。
var InboxModel = (function () {
  const { esc, text } = typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text');
  const sources = {
    testing: ['测试', 'Testing'], testsets: ['测试集', 'Test sets'], issues: ['问题', 'Issues'],
    splits: ['拆分', 'Split'], merge: ['合并', 'Merge'], conflicts: ['冲突', 'Conflicts'], app: ['应用', 'App'],
  };
  const methods = {
    open: ['开场白', 'Opening prompt'], stop: ['停止钩子', 'Stop hook'],
    prompt: ['提交钩子', 'Prompt hook'], type: ['终端粘贴', 'Terminal paste'], hook: ['钩子', 'Hook'],
  };

  function status(m) {
    if (m.state === 'queued') return text('排队中', 'Queued');
    if (m.state === 'dropped') return text('已撤回', 'Withdrawn');
    if (m.state === 'seen') return text('已读到', 'Seen');
    if (m.state !== 'sent') return text('状态未知', 'Unknown status');
    const via = Object.hasOwn(methods, m.via) ? ' · ' + text(...methods[m.via]) : '';
    return text('已送出', 'Sent') + via;
  }

  function message(m, expanded) {
    const body = String(m.text ?? '');
    const date = new Date(m.created);
    const time = Number.isNaN(date.getTime()) ? m.created : date.toLocaleString(text('zh-CN', 'en'), { hour12: false });
    const source = Object.hasOwn(sources, m.from) ? text(...sources[m.from]) : m.label || text(...sources.app);
    const long = body.length > 240 || body.split('\n').length > 4;
    const content = '<p class="inbox-body">' + esc(body) + '</p>';
    const withdraw = m.state === 'queued'
      ? '<button type="button" data-inbox-withdraw="' + esc(m.id) + '">' + esc(text('撤回', 'Withdraw')) + '</button>' : '';
    const heading = '<div class="inbox-meta"><b>' + esc(source) + '</b><time datetime="' + esc(m.created) + '">' + esc(time)
      + '</time><span class="inbox-status">' + esc(status(m)) + '</span>' + withdraw + '</div>';
    const detail = '<details data-inbox-body="' + esc(m.id) + '"' + (expanded.has(m.id) ? ' open' : '') + '><summary>'
      + esc(text('查看正文', 'View message')) + '</summary>' + content + '</details>';
    return '<li class="inbox-message' + (m.state === 'queued' ? ' queued' : '') + '">' + heading + (long ? detail : content) + '</li>';
  }

  function render(messages, expanded = new Set()) {
    if (!messages.length) return '<p class="muted">' + esc(text('这个会话还没有程序消息。', 'No program messages for this session.')) + '</p>';
    return '<ul>' + [...messages].reverse().map((m) => message(m, expanded)).join('') + '</ul>';
  }

  function badge(id) {
    if (!id) return '';
    const label = esc(text('有排队中的程序消息', 'Program messages queued'));
    return '<span class="inbox-badge" hidden data-inbox-run="' + esc(id) + '" title="' + label + '" aria-label="' + label
      + '" role="img"></span>';
  }

  return { render, badge };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = InboxModel;
