// 接入代码：把需求面板里真实的 stageOf 和 sessionChoicesPanel 装进一个假页面，按 input 里的需求打开，返回「会话用的 CLI 和模型」这块显示了什么。
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const scripts = path.join(__dirname, '../../app/src/renderer/scripts');

async function run(input) {
  const boxes = {};
  const el = () => ({ hidden: false, textContent: '', children: [], className: '',
    appendChild(c) { this.children.push(c); if (c.textContent !== undefined && c.tag === 'text') this.textContent += c.textContent; return c; } });
  const window = { shell: { getSettings: async () => ({ agent: input.defaultCli }) } };
  const context = vm.createContext({
    window, Option: class {}, setTimeout,
    $: (id) => (boxes[id] ||= el()),
    document: { createElement: (tag) => { const e = el(); e.tag = tag; Object.defineProperty(e, 'text', { get: () => this.textContent }); return e; },
      createTextNode: (t) => ({ tag: 'text', textContent: t }) },
  });
  const load = (name) => vm.runInContext(fs.readFileSync(path.join(scripts, name), 'utf8'), context);
  load('html-text.js');
  load('requirement-sessions.js');
  context.RequirementSessions = window.RequirementSessions;
  load('board-page-model.js');
  load('board.js');
  load('new-req.js');
  const shown = [];
  for (const r of input.reqs) {
    boxes['session-choices'] = el();
    const req = { sections: { 验收条件: ['x'] }, loose: false, ...r };
    context.sessionChoicesPanel(req, context.stageOf(req));
    await new Promise((resolve) => setImmediate(resolve));
    const box = boxes['session-choices'];
    const text = box.children.map((p) => p.tag === 'p' ? (p.className === 'session-choices-title' ? p.textContent : p.children.map((c) => c.textContent).join('')) : '');
    shown.push({ visible: !box.hidden && text.length > 0, text: box.hidden ? [] : text });
  }
  return shown;
}

module.exports = { run };
