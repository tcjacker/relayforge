const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync, spawnSync } = require('node:child_process');

const repo = path.join(__dirname, '..');

function setup(t) {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), 'relayforge-split-'));
  t.after(() => fs.rmSync(project, { recursive: true, force: true }));
  const parent = 'R-20261118-abcd';
  const requirements = path.join(project, '.relayforge', 'requirements');
  const parentFile = path.join(requirements, parent + '-报表.md');
  const parentContent = [
    '---', 'id: ' + parent, 'type: requirement', 'title: 报表', 'status: current', 'state: open', '---',
    '', '## 需求说明', '- 管理报表', '', '## 验收条件', '- 可以查看报表', '',
  ].join('\n');
  fs.mkdirSync(requirements, { recursive: true });
  fs.writeFileSync(parentFile, parentContent);
  execFileSync('git', ['init', '-q', project]);
  execFileSync('git', ['-C', project, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
    'commit', '-qm', 'init', '--allow-empty']);

  const transcript = path.join(project, 'chat.jsonl');
  function add(type, content) {
    fs.appendFileSync(transcript, JSON.stringify({ type, message: { content } }) + '\n');
  }
  add('user', '请梳理本需求。');
  add('assistant', '建议独立交付导出功能。\n```relayforge-split\n' +
    JSON.stringify([{ title: '导出功能', reason: '可以独立验收', scope: '导出报表' }]) + '\n```');

  const writes = [], timers = [], handlers = new Map();
  const run = { id: parent, sessionId: 'source', root: project, transcript, splitEnabled: true, splitParent: parent,
    term: { stdin: { write: (data) => writes.push(data) } } };
  // 主窗口：ipc.js 只认它顶层的 index.html 发来的请求
  const index = path.join(repo, 'app/src/renderer/index.html');
  const mainFrame = { url: require('url').pathToFileURL(index).href };
  const win = { isDestroyed: () => false, webContents: { mainFrame } };
  const fromWindow = { sender: win.webContents, senderFrame: mainFrame };
  const state = { project, runs: new Map([[run.id, run]]), win };
  const mocks = {
    electron: { ipcMain: { handle: (name, fn) => handlers.set(name, fn), on() {} } },
    './config': { INDEX_HTML: index },
    './sandbox-check': { check: async () => ({ ok: true }) }, './inbox': { post: (m) => mocks['./sessions'].type(state.runs.get(m.to), m.text), takeForOpen: () => '' }, './attention': { setPhase() {}, phaseOf: () => 'reply', forgetPhase() {}, onPhase() {}, onSettled() {} },
    './state': { state, send() {} },
    './requirements': { pushReqs() {} },
    './backend': {
      async rf(args, root) {
        const [, , session, , chat, , parent, , approve = ''] = args;
        const result = spawnSync('python3', ['-c',
          'import json, sys; from pathlib import Path; from relayforge.requirements.splits import review; ' +
          'print(json.dumps(review(Path.cwd(), sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])))',
          chat, session, parent, approve], {
          cwd: root, encoding: 'utf8',
          env: { ...process.env, PYTHONPATH: path.join(repo, 'backend'), RELAYFORGE_HOME: path.join(project, 'local') },
        });
        if (result.error) throw result.error;
        return { ok: result.status === 0, stdout: result.stdout, stderr: result.stderr };
      },
      lastLine: (value) => value.trim().split('\n').at(-1),
    },
  };
  function load(name) {
    const file = path.join(repo, 'app/src/main', name + '.js');
    const context = { module: { exports: {} }, require: (id) => id.startsWith('.') ? mocks[id] || {} : mocks[id] || require(id),
      setTimeout: (fn) => timers.push(fn) };
    vm.runInNewContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
    return context.module.exports;
  }
  const splits = mocks['./splits'] = load('splits');
  mocks['./sessions'] = load('sessions');
  load('ipc').register();

  return {
    run, add, writes, timers, review: () => splits.review(run),
    approve: (key) => handlers.get('split:approve')(fromWindow, { id: run.id, key }),
    drafts: () => fs.readdirSync(requirements).filter((file) => file !== path.basename(parentFile))
      .map((file) => fs.readFileSync(path.join(requirements, file), 'utf8')),
    readParent: () => fs.readFileSync(parentFile, 'utf8'),
  };
}

test('panel approval creates a draft and submits the receipt to the original session once', async (t) => {
  const { run, writes, timers, review, approve, drafts, readParent } = setup(t);
  const parentBefore = readParent();
  await review();
  assert.equal(run.split.state, 'pending');
  const key = run.split.id;

  assert.equal((await approve(key)).error, undefined);
  assert.equal(run.split.state, 'done');
  const id = run.split.items[0].requirement;
  assert.equal(drafts().length, 1);
  assert.match(drafts()[0], /^state: draft$/m);
  assert.equal(readParent(), parentBefore);
  assert.equal(writes.length, 1);
  assert.match(writes[0], new RegExp(id));
  assert.match(writes[0], /继续/);
  assert.equal(timers.length, 1);
  timers.shift()();
  assert.equal(writes[1], '\r');

  assert.ok((await approve(key)).error);
  await review();
  assert.equal(writes.length, 2);
  assert.equal(drafts().length, 1);
});

test('typed approval creates a draft without adding a second terminal message', async (t) => {
  const { run, add, writes, review, drafts } = setup(t);
  await review();
  add('user', '同意拆分');
  await review();
  assert.equal(run.split.state, 'done');
  assert.equal(drafts().length, 1);
  assert.deepEqual(writes, []);
});

test('a declined proposal cannot be sent to the session by a stale panel approval', async (t) => {
  const { run, add, writes, review, approve } = setup(t);
  await review();
  add('user', '暂不拆分');
  assert.match((await approve(run.split.id)).error, /拆分建议已变化/);
  assert.deepEqual(writes, []);
});

test('the guide tells a session how items of one proposal wait for each other', () => {
  const source = fs.readFileSync(path.join(repo, 'app', 'src', 'main', 'splits.js'), 'utf8');
  assert.match(source, /这次一起提出的另一条写「#序号」/);
  assert.match(source, /在日常聊天里一次拆出几条有先后的需求时，就用「#序号」把先后写出来/);
  assert.match(source, /画一张先后图/);
  assert.match(source, /只用纯文本，不要用 Mermaid/);
  assert.match(source, /箭头从先做的指向后做的/);
  assert.match(source, /takes 列出这一项从当前需求接过去的前置需求/);
  assert.match(source, /不要去设它，把要用到它的部分拆出去，在 waits 里等它/);
  assert.match(source, /needs 给每条先后写一句要用到对方的什么/);
  assert.match(source, /每条先后都要写，漏写的用户不能同意/);
  assert.match(source, /在正文里逐样说明这些被等的东西留在哪一项/);
  assert.match(source, /箭头上的括号写要用到什么/);
});
