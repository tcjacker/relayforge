// 交给 AI 排查：排查会话的权限规则（只读、不碰该由用户决定的命令）和两份开场白
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const main = path.join(__dirname, '..', 'app', 'src', 'main');

const project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-probe-'));
require(path.join(main, 'state')).state.project = project;
const { hooks } = require(path.join(main, 'claude-settings'));
const { BUNDLED } = require(path.join(main, 'config'));
const prompts = require(path.join(main, 'prompts'));
const real = fs.realpathSync(project);

test('the investigation session may not edit anything in the project, nor run what the user decides', () => {
  const s = JSON.parse(hooks('/scratch/probe-I-001', true));
  const { allow, deny } = s.permissions;
  assert.ok(deny.includes(`Edit(/${real}/**)`) && deny.includes('NotebookEdit')); // Edit(路径) 管所有改文件的工具（Write(路径) 规则 Claude Code 对不上）
  for (const a of ['fix', 'set', 'reply', 'confirm', 'unconfirm', 'reject', 'to-req', 'probe']) assert.ok(deny.includes(`Bash(python3 ${BUNDLED} issue ${a}:*)`), a);
  for (const a of ['start', 'done', 'drop', 'set']) assert.ok(deny.includes(`Bash(python3 ${BUNDLED} req ${a}:*)`), a);
  assert.ok(deny.includes('Bash(python3 .relayforge/bin/rf.py issue fix:*)')); // the copy inside the project is the same command
  for (const a of ['commit', 'add', 'checkout']) assert.ok(deny.includes(`Bash(git ${a}:*)`), a);
  // it may record its finding, read the facts, and write only into its own scratch folder outside the project
  assert.ok(allow.includes(`Bash(python3 ${BUNDLED} issue finding:*)`));
  assert.ok(allow.includes(`Read(/${real}/.relayforge/**)`));
  assert.ok(allow.includes('Edit(//scratch/probe-I-001/**)'));
  // nothing that writes in the shell is pre-approved: no bare Bash, no rm, mv, cp, sed, tee, redirect
  for (const rule of allow.filter((x) => x.startsWith('Bash('))) assert.match(rule, /python3 .*rf\.py (note|req brief|design|map|trace|why|usage|history (search|show|session)|test (list|show)|publish (status|list)|issue (new|list|show|brief|handback|finding))|mkdir -p \/scratch/, rule);
  assert.deepEqual(allow.filter((x) => /^(Edit|Write)\(/.test(x)), ['Edit(//scratch/probe-I-001/**)']); // the only place it may write (Edit covers every editing tool)
});

test('other sessions do not get the finding command, and fixing or chatting sessions are not locked', () => {
  const chat = JSON.parse(hooks(null, false));
  assert.ok(!chat.permissions.allow.some((x) => x.includes('issue finding')));
  assert.ok(!chat.permissions.deny.some((x) => x.startsWith('Edit(') || x.startsWith('Write(')) && !chat.permissions.deny.includes('NotebookEdit')); // a chat may still edit; it only may not push
  const intake = JSON.parse(hooks('/drafts/R-1', false));
  assert.ok(!intake.permissions.allow.some((x) => x.includes('issue finding')));
  assert.ok(!intake.permissions.deny.some((x) => x.startsWith('Bash(git add'))); // the probe's extra rules are its own
  for (const rules of [chat, intake]) assert.ok(rules.permissions.deny.some((x) => x.includes('issue fix'))); // the user's commands are denied in every session (rf.py checks them too)
  assert.ok(intake.permissions.deny.includes(`Edit(/${real}/**)`)); // the intake rules are as before
});

test('the investigation prompt says read-only, one question at a time, and how to record the conclusion', () => {
  const issue = { id: 'I-007', num: '#007', title: '导出按钮点了没有反应' };
  const text = prompts.investigating(issue, '现象：点了没反应', '/scratch/probe-I-007', false);
  for (const needle of ['#007', '导出按钮点了没有反应', '现象：点了没反应', '只读', '一次只问一个问题', 'issue finding I-007 --kind', '--verified', '--inferred',
    '不要开始修', 'issue fix', '不要只在聊天里说结论', '/scratch/probe-I-007', project]) assert.ok(text.includes(needle), needle);
  assert.ok(prompts.investigating(issue, '', '/s', true).includes('接着之前的排查'));
  assert.ok(prompts.investigating(issue, '', '/s', false).includes('issue brief I-007')); // no brief: it can fetch it itself
});

test('a fix prompt starts from the investigation finding when there is one', () => {
  const fix = { id: 'F-001', branch: 'fix/f-001' };
  const plain = prompts.fixing(fix, [{ num: '#001', title: 'x', thread: [] }], 'BRIEF', false);
  const found = prompts.fixing(fix, [{ num: '#001', title: 'x', thread: [{ t: 'finding' }] }], 'BRIEF', false);
  assert.ok(!plain.includes('之前交给过 AI 排查'));
  for (const needle of ['之前交给过 AI 排查', '排查结论', '复现步骤', '实际复现、验证过的', '只是推断、没验证的']) assert.ok(found.includes(needle), needle);
  assert.ok(found.includes('BRIEF'));
});
