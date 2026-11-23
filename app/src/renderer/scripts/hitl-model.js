// 后台运行的会话需要你时，右下角那张卡片上写什么（不碰 DOM，Node 测试直接 require）。数据来自主进程 hitl.js：
// approvals：等你批准的一步；questions：后台会话在问你的话；stuck：后台会话已停止，等你接管。
var HitlModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var H = typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text');
  var esc = H.esc;
  var TOOL = { Bash: uiText('跑命令', "Run command"), Edit: uiText('改文件', "Edit file"), Write: uiText('写文件', "Write file"), MultiEdit: uiText('改文件', "Edit file"), apply_patch: uiText('改文件', "Edit file"), WebFetch: uiText('读网页', "Read webpage"), NotebookEdit: uiText('改笔记本', "Edit notebook") };

  function count(data) {
    return (data?.approvals || []).length + (data?.questions || []).length + (data?.stuck || []).length;
  }

  function approvalCard(a) {
    return '<div class="hitl-card" data-kind="approve" data-id="' + esc(a.id) + '">'
      + '<div class="hitl-head"><b>' + esc(a.title) + uiText('</b> <span class="muted">等你批准</span></div>', "</b> <span class=\"muted\">Approval required</span></div>")
      + '<div class="hitl-what"><span class="hitl-tool">' + esc(TOOL[a.tool] || a.tool) + '</span><code>' + esc(a.what) + '</code></div>'
      + (a.why ? '<div class="muted hitl-why">' + esc(a.why) + '</div>' : '')
      + uiText('<div class="hitl-acts"><button type="button" class="primary" data-hitl="allow">批准</button>', "<div class=\"hitl-acts\"><button type=\"button\" class=\"primary\" data-hitl=\"allow\">Approve</button>")
      + uiText('<button type="button" data-hitl="deny">拒绝</button>', "<button type=\"button\" data-hitl=\"deny\">Deny</button>")
      + (a.run ? '<button type="button" class="quiet" data-hitl="take" data-run="' + esc(a.run) + uiText('">在终端里接管</button>', "\">Take over in terminal</button>") : '') + '</div></div>';
  }

  function questionCard(q) {
    return '<div class="hitl-card" data-kind="answer" data-id="' + esc(q.id) + '">'
      + '<div class="hitl-head"><b>' + esc(q.title) + uiText('</b> <span class="muted">在问你</span></div>', "</b> <span class=\"muted\">Waiting for your answer</span></div>")
      + '<div class="hitl-text">' + esc(q.text) + '</div>'
      + uiText('<textarea rows="2" placeholder="回复它（它会在后台接着做）"></textarea>', "<textarea rows=\"2\" placeholder=\"Reply to continue in the background\"></textarea>")
      + uiText('<div class="hitl-acts"><button type="button" class="primary" data-hitl="reply">回复</button>', "<div class=\"hitl-acts\"><button type=\"button\" class=\"primary\" data-hitl=\"reply\">Reply</button>")
      + '<button type="button" class="quiet" data-hitl="take" data-run="' + esc(q.id) + uiText('">在终端里接管</button></div></div>', "\">Take over in terminal</button></div></div>");
  }

  function stuckCard(s) {
    return '<div class="hitl-card" data-kind="stuck" data-id="' + esc(s.id) + '">'
      + '<div class="hitl-head"><b>' + esc(s.title) + '</b> <span class="muted">'
      + uiText('后台会话卡住了', 'Background session stuck') + '</span></div>'
      + '<div class="hitl-text">' + esc(s.text) + '</div>'
      + '<div class="hitl-acts"><button type="button" class="quiet" data-hitl="take" data-run="' + esc(s.id) + '">'
      + uiText('在终端里接管', 'Take over in terminal') + '</button></div></div>';
  }

  function html(data, folded) {
    var n = count(data);
    if (!n) return '';
    var head = uiText('<button type="button" class="hitl-bar" data-hitl="fold">后台会话需要你 · ', "<button type=\"button\" class=\"hitl-bar\" data-hitl=\"fold\">Background sessions need your attention · ") + n + (folded ? ' ▴' : ' ▾') + '</button>';
    if (folded) return head;
    return head + '<div class="hitl-list">' + (data.approvals || []).map(approvalCard).join('')
      + (data.questions || []).map(questionCard).join('') + (data.stuck || []).map(stuckCard).join('') + '</div>';
  }

  return { html: html, count: count, TOOL: TOOL };
})();
if (typeof module !== 'undefined') module.exports = HitlModel;
