// 功能点核对的建议（backend/relayforge/facts/fresh.py 的 fresh show）在对话框里怎么写（不碰 DOM，Node 测试直接 require）
var FreshModel = (function () {
  var H = typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text');
  var esc = H.esc, uiText = H.text;
  var OPS = { replace: uiText('改成', 'Change to'), add: uiText('加上', 'Add'), remove: uiText('删去', 'Remove') };
  var STATES = { adopted: uiText('已采纳', 'Adopted'), ignored: uiText('已忽略', 'Ignored') };

  function item(i) {
    var done = i.state !== 'pending';
    return '<li class="fr-item' + (done ? ' done' : '') + '"><div class="fr-head"><b>' + esc(i.section) + '</b> · ' + esc(OPS[i.op] || i.op)
      + (done ? ' <span class="muted">' + esc(STATES[i.state] || i.state) + '</span>' : '') + '</div>'
      + (i.old ? '<p class="fr-old">' + esc(i.old) + '</p>' : '') + (i.new ? '<p class="fr-new">' + esc(i.new) + '</p>' : '')
      + '<p class="muted fr-why">' + esc(i.why) + (i.evidence ? '（' + esc(i.evidence) + '）' : '') + '</p>'
      + (done ? '' : '<div class="fr-acts"><button type="button" data-fr="ignore" data-k="' + i.k + '">' + esc(uiText('忽略', 'Ignore')) + '</button>'
        + '<button type="button" class="go" data-fr="adopt" data-k="' + i.k + '">' + esc(uiText('采纳', 'Adopt')) + '</button></div>') + '</li>';
  }

  function html(id, data, error) {
    var h = '<h1>' + esc(uiText('核对 ', 'Check ')) + esc(id) + '</h1>';
    if (!data) return h + '<p class="muted">' + esc(uiText('还没有核对结果。', 'No check results yet.')) + '</p><div class="actions"><span class="spacer"></span><button type="button" data-fr="close">' + esc(uiText('关闭', 'Close')) + '</button></div>';
    var pending = data.items.filter(function (i) { return i.state === 'pending'; }).length;
    h += '<p>' + esc(data.verdict === 'unchanged' ? uiText('核对会话认为描述没有变化。', 'The check found the description still matches.') : uiText('核对会话认为描述要改：', 'The check suggests updating the description: ')) + esc(data.summary || '') + '</p>';
    if (data.items.length) h += '<ul class="fr-list">' + data.items.map(item).join('') + '</ul>';
    if (error) h += '<p class="error">' + esc(error) + '</p>';
    h += '<p class="muted">' + esc(uiText('采纳的写进功能点，出处记成这次核对；核对完以后，这些提交不再让它显示「可能过时」。', 'Adopted suggestions go into the feature with this check as their source. After you finish, these commits no longer mark it as stale.')) + '</p>';
    return h + '<div class="actions"><button type="button" data-fr="close">' + esc(uiText('关闭', 'Close')) + '</button><span class="spacer"></span>'
      + '<button type="button" class="go" data-fr="done"' + (pending ? ' disabled title="' + esc(uiText('还有建议没处理', 'Some suggestions are still pending')) + '"' : '') + '>' + esc(uiText('核对完', 'Finish check')) + '</button></div>';
  }

  return { html: html };
})();
if (typeof module !== 'undefined') module.exports = FreshModel;
