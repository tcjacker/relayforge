// 执行前的检查（rf.py hook guard）：每种会话都对 Bash 挂上；立项、排查、测试会话再对改文件的工具挂上，带上和权限规则一样的那批目录。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const main = path.join(__dirname, '..', 'app', 'src', 'main');

const project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-guard-'));
require(path.join(main, 'state')).state.project = project;
const { hooks } = require(path.join(main, 'claude-settings'));
const real = fs.realpathSync(project);

const pre = (text) => JSON.parse(text).hooks.PreToolUse;

test('every session checks rf.py commands before they run, synchronously', () => {
  const [bash, ...rest] = pre(hooks(null, false, false, true, false, false, false, { role: 'develop' }));
  assert.equal(bash.matcher, 'Bash');
  assert.match(bash.hooks[0].command, /rf\.py['"]? hook guard$/);
  assert.equal(bash.hooks[0].async, undefined);
  assert.equal(rest.length, 0); // 开发会话的文件不归这里管
});

test('intake and probe sessions are told why they cannot edit project files and records', () => {
  for (const [args, kind] of [[['/drafts/R-1', false], 'intake'], [['/drafts/probe-I-1', true], 'probe']]) {
    const [, edits] = pre(hooks(...args));
    assert.equal(edits.matcher, 'Edit|MultiEdit|Write|NotebookEdit');
    const command = edits.hooks[0].command;
    assert.ok(command.includes(`--facts '${real}/.relayforge'`), command);
    assert.ok(command.includes(`--project '${real}'`), command);
    assert.ok(command.includes(`--drafts '${args[0]}'`), command);
    assert.ok(command.endsWith(`--kind ${kind}`), command);
  }
});

test('a test session is told not to edit product code or records', () => {
  const place = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-guard-wt-'));
  const [, edits] = pre(hooks(null, false, false, false, false, false, false, { role: 'test', place }));
  const command = edits.hooks[0].command;
  assert.ok(command.includes(`--product '${fs.realpathSync(place)}/src/main'`), command);
  assert.ok(command.includes(`--facts '${fs.realpathSync(place)}/.relayforge'`), command);
  assert.ok(command.includes(`--facts '${real}/.relayforge'`), command);
  assert.ok(!command.includes('--project'), command);
});

test('paths with quotes stay one shell word', () => {
  const { guardHooks } = require(path.join(main, 'claude-settings'));
  const [bash] = guardHooks({ drafts: "/tmp/it's here" });
  assert.ok(bash.hooks[0].command.endsWith(`--drafts '/tmp/it'\\''s here'`));
});

test('Codex sessions get the same hooks with a fixed command, and their limits through the environment', () => {
  const codexHooks = require(path.join(main, 'codex-hooks'));
  const flags = codexHooks.flags();
  assert.equal(flags.filter((x) => x === '-c').length, 6);
  const values = flags.filter((x) => x !== '-c');
  for (const event of ['UserPromptSubmit', 'PreToolUse', 'PermissionRequest', 'PostToolUse', 'Stop', 'Interrupt']) {
    assert.ok(values.some((v) => v.startsWith(`hooks.${event}=[{matcher="*",hooks=[{type="command",command=`)), event);
  }
  // 信任按内容记：命令里不能有任何随会话变化的东西
  assert.deepEqual(codexHooks.flags(), flags);
  assert.ok(values.every((v) => v.includes(JSON.stringify(codexHooks.COMMAND))));
  // rf.py 的路径也不能进命令（各工作目录会互相顶掉信任），由会话的环境变量带
  assert.equal(codexHooks.COMMAND, '$RELAYFORGE_HOOK_PYTHON "$RELAYFORGE_HOOK_RF" hook codex');
  assert.match(codexHooks.env({}).RELAYFORGE_HOOK_RF, /rf\.py$/);
  assert.ok(values.find((v) => v.startsWith('hooks.PostToolUse')).includes('async=true'));
  const { guardSpec } = require(path.join(main, 'claude-settings'));
  const spec = guardSpec({ drafts: '/drafts/R-1' });
  assert.deepEqual(JSON.parse(codexHooks.env(spec).RELAYFORGE_GUARD), JSON.parse(JSON.stringify(spec)));
  assert.equal(spec.kind, 'intake');
  assert.ok(spec.project);
});
