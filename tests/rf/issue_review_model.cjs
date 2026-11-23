// 测试集的接入代码：问题页「刷新」（AI 复查）在界面上怎么显示，调用 app/src/renderer/scripts/issue-review-model.js。
// input.status 是 rf.py issue review-status 交回的那份复查状态；这里只调用 model 的函数，把生成的 HTML 拆成看得懂的结构交回：
// 按钮上的字、问题页顶上那一条、结果窗口里的分组和每一条（去掉标签后的文字、有没有勾选框、并入哪张原单）、能不能点「确认处理」
// （界面在 issue-review.js 里按 pickable 的条数决定显示不显示确认按钮）。不含测试数据，不做比对。
const path = require('node:path');

const M = require(path.join(__dirname, '../../app/src/renderer/scripts/issue-review-model.js'));

const text = (html) => html.replace(/<[^>]*>/g, ' ').replace(/&gt;/g, '>').replace(/&lt;/g, '<').replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&')
  .replace(/\s+/g, ' ').trim();

function results(status) {
  const html = M.resultsHtml(status);
  const parts = html.split(/(?=<h3 class="grp")/);
  const groups = [];
  for (const part of parts) {
    const head = part.match(/^<h3 class="grp" data-kind="([^"]+)">([\s\S]*?)<\/h3>/);
    if (!head) continue;
    const body = part.slice(head[0].length);
    const items = [];
    const re = /<label><input type="checkbox" value="([^"]*)"( checked)?>([\s\S]*?)<\/label>|<div class="keep">([\s\S]*?)<\/div>/g;
    let m;
    while ((m = re.exec(body))) {
      const inner = m[3] !== undefined ? m[3] : m[4];
      const goto = inner.match(/data-review="goto" data-id="([^"]+)"/);
      items.push({ text: text(inner), checkbox: m[1] !== undefined, checked: !!m[2], goto: goto ? goto[1] : null });
    }
    groups.push({ kind: head[1], title: text(head[2]), items });
  }
  return { intro: text(parts[0] || ''), groups };
}

function run(input) {
  const s = input.status;
  const out = { button: M.button(s).label, active: M.active(s) };
  const strip = M.strip(s);
  out.strip = strip ? text(strip.html) : null;
  if (s && s.state === 'running') {
    out.progress = text(M.progressHtml(s));
    out.marks = Object.fromEntries((s.items || []).map((x) => [x.num, text(M.rowMark(s, x.id))]));
  }
  if (s && s.state === 'ready') {
    Object.assign(out, results(s));
    out.can_confirm = M.pickable(s).length > 0;
  }
  if (input.applied) out.applied_note = M.appliedNote(input.applied);
  return out;
}

module.exports = { run };
