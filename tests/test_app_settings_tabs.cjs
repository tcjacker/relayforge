// 设置窗口的分类页（R-20261123-0745）：哪项设置在哪个分类，以及切换和校验的接线
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const renderer = path.join(__dirname, '..', 'app', 'src', 'renderer');
const html = fs.readFileSync(path.join(renderer, 'index.html'), 'utf8');
const script = fs.readFileSync(path.join(renderer, 'scripts', 'settings.js'), 'utf8');
const dialog = html.slice(html.indexOf('<dialog id="settings-dlg"'), html.indexOf('</dialog>', html.indexOf('<dialog id="settings-dlg"')));
const pane = (name) => {
  const start = dialog.indexOf('id="settings-pane-' + name + '"');
  const end = name === 'general' ? dialog.indexOf('id="settings-pane-agents"') : dialog.indexOf('class="settings-foot"');
  return dialog.slice(start, end);
};

test('General holds language, logging and dropped branches; Agents holds the agent settings', () => {
  const general = pane('general'), agents = pane('agents');
  for (const id of ['language-options', 'settings-monitor', 'dropped-options', 'dropped-days', 'dropped-max']) {
    assert.ok(general.includes('id="' + id + '"'), id + ' in General');
    assert.ok(!agents.includes('id="' + id + '"'), id + ' not in Agents');
  }
  for (const id of ['agent-options', 'settings-cli-check', 'settings-cli-models', 'settings-subagent-cli', 'settings-subagent-model', 'approval-options']) {
    assert.ok(agents.includes('id="' + id + '"'), id + ' in Agents');
    assert.ok(!general.includes('id="' + id + '"'), id + ' not in General');
  }
});

test('General is selected by default; Agents is hidden; one shared Cancel/Save outside the panes', () => {
  assert.match(dialog, /id="settings-tab-general"[^>]*aria-selected="true"/);
  assert.match(dialog, /id="settings-tab-agents"[^>]*aria-selected="false"/);
  assert.match(pane('agents'), /^[^>]*hidden/);
  assert.equal((dialog.match(/id="settings-save"/g) || []).length, 1);
  assert.ok(dialog.indexOf('id="settings-save"') > dialog.indexOf('id="settings-pane-agents"'));
  assert.ok(!pane('general').includes('settings-save') && !pane('agents').includes('settings-save'));
  assert.match(dialog, /<form id="settings-form" novalidate>/); // 浏览器自带的校验聚焦不到隐藏分类里的输入框
});

test('opening resets to General and invalid retention jumps back before saving', () => {
  assert.match(script, /showTab\(tabs\[0\]\);[\s\S]*dialog\.showModal\(\)/);
  const submit = script.slice(script.indexOf("form.addEventListener('submit'"));
  assert.ok(submit.indexOf('droppedValid') < submit.indexOf('window.shell.saveSettings'));
  assert.match(submit, /showTab\(tabs\[0\]\)/);
  assert.match(submit, /return;/);
});

test('retention bounds match the inputs', () => {
  assert.match(html, /id="dropped-days" type="number" min="1" max="3650"/);
  assert.match(html, /id="dropped-max" type="number" min="1" max="1000"/);
});
