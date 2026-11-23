// 项目名称旁的分支选择（R-20261119-645d）：界面上显示什么（branch-model.js），主进程怎么调 rf.py branch（branches.js），页面有没有接上。
// 后端的行为（隐藏需求分支、待发布数、Git 拒绝时保留改动）在 tests/test_branches.py 里测。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const repo = path.join(__dirname, '..');
const main = path.join(repo, 'app', 'src', 'main');
const renderer = path.join(repo, 'app', 'src', 'renderer');
const M = require(path.join(renderer, 'scripts', 'branch-model'));

const LIST = {
  current: 'main', detached: false, busy: null, pending: 3, upstream: 'origin/main',
  branches: [
    { name: 'main', current: true, upstream: 'origin/main', pending: 3, blocked: null },
    { name: 'develop', current: false, upstream: 'origin/develop', pending: 0, blocked: null },
    { name: 'feature/sidebar', current: false, upstream: 'origin/feature/sidebar', pending: 2, blocked: null },
    { name: 'feature/local', current: false, upstream: null, pending: 0, blocked: null },
    { name: 'other', current: false, upstream: null, pending: 0, blocked: 'other 已经在另一个工作目录签出' },
  ],
};

test('model: filtering by name, the count in brackets, the footer uses the same count as the top bar', () => {
  assert.deepEqual(M.rows(LIST, 'FEATURE').map((b) => b.name), ['feature/sidebar', 'feature/local']);
  assert.equal(M.rows(LIST, '').length, 5);
  assert.deepEqual(M.rows(LIST, 'zzz'), []);
  assert.equal(M.countText(LIST.branches[2]), '（2 个待发布）');
  assert.equal(M.countText(LIST.branches[1]), '');
  assert.equal(M.footer(LIST), '待发布到远端：3 个需求');
  assert.equal(M.footer({ ...LIST, branches: [{ name: 'x', current: true, upstream: null, pending: 0 }] }), '当前分支未关联远端分支');
  assert.equal(M.footer({ detached: true, branches: [] }), '项目目录现在不在任何分支上');
  assert.equal(M.footer({ error: 'x' }), '');
});

test('model: switching asks first when the current branch has unpublished requirements, says why when it cannot', () => {
  const d = M.decide(LIST, LIST.branches[1]);
  assert.equal(d.kind, 'confirm');
  assert.equal(d.text, '当前分支 main 还有 3 个需求未发布到 origin/main。确定切换到 develop吗？');
  const none = { ...LIST, branches: [{ ...LIST.branches[0], pending: 0 }, ...LIST.branches.slice(1)] };
  assert.equal(M.decide(none, none.branches[1]).kind, 'go');
  assert.equal(M.decide(LIST, LIST.branches[0]).kind, 'none');
  assert.equal(M.decide(LIST, null).kind, 'none');
  assert.deepEqual(M.decide(LIST, LIST.branches[4]), { kind: 'blocked', text: 'other 已经在另一个工作目录签出' });
  assert.match(M.decide({ ...none, busy: 'merge' }, none.branches[1]).text, /合并还没做完/);
  assert.equal(M.failure({ error: '会覆盖本地改动。', detail: 'app.txt' }), '会覆盖本地改动。（git 说：app.txt）');
  assert.equal(M.failure(null), '切换没有成功。');
});

function loadMain(answers, busy = null) {
  const calls = [], sent = [];
  for (const k of Object.keys(require.cache)) if (k.startsWith(main)) delete require.cache[k];
  const backendPath = require.resolve(path.join(main, 'backend'));
  const real = require(backendPath);
  require.cache[backendPath].exports = Object.assign({}, real, {
    rf: async (args) => {
      calls.push(args);
      const made = answers[args.slice(0, 2).join(' ')];
      const value = typeof made === 'function' ? made(args) : made;
      return value === undefined ? { ok: false, stdout: '', stderr: '不认识', timedOut: false } : { ok: value.ok !== false, stdout: JSON.stringify(value), stderr: '', timedOut: false };
    },
  });
  const publishPath = path.join(main, 'publish.js');
  let refreshed = 0;
  const gate = require(path.join(renderer, 'scripts', 'publish-model')).gate();
  require.cache[publishPath] = {
    id: publishPath, filename: publishPath, loaded: true,
    exports: {
      exclusive: (name, fn) => busy ? Promise.resolve({ error: '正在' + busy + '，等它做完再点。', busy: true }) : gate.run(name, fn),
      refresh: async () => { refreshed += 1; },
    },
  };
  const { state } = require(path.join(main, 'state'));
  state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-branch-'));
  state.win = { isDestroyed: () => false, webContents: { send: (c, p) => sent.push([c, p]) } };
  return { B: require(path.join(main, 'branches')), calls, sent, refreshed: () => refreshed };
}

test('main: lists and switches through rf.py branch (with the app marker), refreshes the publish state after a switch', async () => {
  const { B, calls, sent, refreshed } = loadMain({ 'branch list': LIST, 'branch switch': { ok: true, from: 'main', to: 'develop', changed: true } });
  assert.deepEqual(await B.list(), LIST);
  const out = await B.switchTo({ name: 'develop' });
  assert.equal(out.ok, true);
  assert.deepEqual(out.list, LIST);
  assert.deepEqual(calls[1], ['branch', 'switch', 'develop', '--json']);
  assert.equal(refreshed(), 1);
  assert.match(sent.find(([c]) => c === 'notice')[1], /已切换到 develop/);
});

test('main: a refusal comes back with the reason and git\'s words; option-like names and busy publishing are refused before rf.py', async () => {
  const { B, calls } = loadMain({ 'branch switch': { ok: false, code: 'dirty', error: '切过去会覆盖这些改动，Git 没有切换。', detail: 'app.txt' } });
  assert.deepEqual(await B.switchTo({ name: 'b' }), { error: '切过去会覆盖这些改动，Git 没有切换。', code: 'dirty', detail: 'app.txt' });
  assert.equal((await B.switchTo({ name: '--orphan' })).code, 'missing');
  assert.match((await B.switchTo({})).error, /要选一个分支/);
  assert.equal(calls.length, 1);
  const busy = loadMain({}, '发布');
  assert.match((await busy.B.switchTo({ name: 'b' })).error, /正在发布/);
  assert.equal(busy.calls.length, 0);
});

test('page: the picker sits next to the project name, the scripts are loaded, IPC is wired', () => {
  const html = fs.readFileSync(path.join(renderer, 'index.html'), 'utf8');
  assert.ok(html.indexOf('id="branch-picker"') > html.indexOf('id="project"'));
  for (const id of ['branch-toggle', 'branch-panel', 'branch-search', 'branch-list', 'branch-switch', 'branch-error', 'branch-remote', 'branch-confirm', 'branch-confirm-go', 'branch-confirm-cancel']) assert.match(html, new RegExp(`id="${id}"`), id);
  assert.ok(html.indexOf('scripts/branch-model.js') < html.indexOf('scripts/branches.js'));
  assert.match(fs.readFileSync(path.join(renderer, 'scripts', 'main.js'), 'utf8'), /wireBranches\(\);/);
  assert.match(fs.readFileSync(path.join(main, 'ipc.js'), 'utf8'), /ipcMain\.handle\('branch:switch'/);
  assert.match(fs.readFileSync(path.join(repo, 'app', 'src', 'preload', 'index.js'), 'utf8'), /branchSwitch: \(request\) => ipcRenderer\.invoke\('branch:switch'/);
  // 会话里禁止切项目主目录的分支（rf.py 自己也校验）
  assert.match(fs.readFileSync(path.join(main, 'claude-settings.js'), 'utf8'), /branch switch:\*/);
});
