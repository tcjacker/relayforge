const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function run(input) {
  const scripts = path.join(__dirname, '../../app/src/renderer/scripts');
  const context = vm.createContext({});
  for (const file of ['issues.js', 'issue-thread.js', 'issue-actions.js']) {
    vm.runInContext(fs.readFileSync(path.join(scripts, file), 'utf8'), context);
  }
  const { IS } = context;
  context.renderIssues = () => {};
  IS.issues = input.issues;
  IS.sel = input.selected;
  IS.tab = input.tab;
  IS.kind = input.kind;
  IS.feat = input.feature;
  IS.pane = input.panes;
  const active = context.curIssue();
  const html = active.thread.map((entry) => context.threadEntry(active, entry)).join('');
  const link = html.match(/<button [^>]*data-act="([^"]+)" data-pane="([^"]+)" data-id="([^"]+)"/);
  if (link) {
    const [, act, pane, id] = link;
    context.issueClick({ target: { closest: () => ({ dataset: { act, pane, id } }) } });
  }
  return {
    text: html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(),
    selected: IS.sel, tab: IS.tab, feature: IS.feat, pane: IS.pane[IS.sel] || null,
  };
}

module.exports = { run };
