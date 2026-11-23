// 追溯（应用这一侧）：「改动」块显示什么、rf.py trace / why 在每种会话里都放行、主进程怎么取
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const root = path.join(__dirname, '..', 'app', 'src');
const main = path.join(root, 'main');
const Model = require(path.join(root, 'renderer', 'scripts', 'trace-model.js'));

const done = { kind: 'requirement', id: 'R-20261118-ab12', state: 'done', merged: true, published: false, remotes: [], commit_count: 3, file_count: 2,
  files: [{ path: 'export.py', added: 10, deleted: 2 }, { path: 'export_test.py', added: 5, deleted: 0 }],
  sessions: [{ tag: 'S-20261118-7f3a', title: '导出账单', path: '/p/.relayforge/sessions/S-20261118-7f3a.md' }] };

test('the 改动 block says how many commits and files, whether it went out, and lists files and sessions collapsed', () => {
  const s = Model.summary(done);
  assert.equal(s.head, '3 个提交、2 个文件');
  assert.equal(s.publish, '还没发布');
  assert.deepEqual(s.files[0], { path: 'export.py', text: '+10 -2' });
  assert.equal(s.sessions[0].tag, 'S-20261118-7f3a');
  assert.equal(Model.summary({ ...done, published: true, remotes: ['origin/main'] }).publish, '已发布（origin/main）');
  const html = Model.html(done);
  assert.match(html, /<details class="trace-files"><summary>文件（2）/);
  assert.match(html, /S-20261118-7f3a 导出账单/);
  assert.doesNotMatch(html, /<script/);
});

test('nothing to say: no code changes, a read failure, or an unmerged one', () => {
  const none = Model.summary({ ...done, commit_count: 0, file_count: 0, files: [], merged: false });
  assert.equal(none.head, '没有代码改动');
  assert.equal(none.publish, '');
  assert.equal(Model.summary({ error: 'x' }), null);
  assert.equal(Model.summary(null), null);
  assert.equal(Model.html({ error: 'x' }), '');
  assert.equal(Model.summary({ ...done, merged: false, state: 'open' }).publish, '还没合并');
});

test('long file lists are cut and say how to see the rest; names are escaped', () => {
  const many = Array.from({ length: 30 }, (_, i) => ({ path: `f${i}.py`, added: 1, deleted: 0 }));
  const s = Model.summary({ ...done, file_count: 30, files: many });
  assert.equal(s.files.length, Model.FILES_SHOWN);
  assert.equal(s.filesMore, 30 - Model.FILES_SHOWN);
  assert.match(Model.html({ ...done, file_count: 30, files: many }), /rf\.py trace R-20261118-ab12/);
  assert.match(Model.html({ ...done, files: [{ path: '<img onerror=x>', added: 1, deleted: 0 }], file_count: 1 }), /&lt;img onerror=x&gt;/);
});

test('rf.py trace and rf.py why are allowed without asking in every kind of session', () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-tr-'));
  require(path.join(main, 'state')).state.project = project;
  const { hooks } = require(path.join(main, 'claude-settings'));
  const { BUNDLED } = require(path.join(main, 'config'));
  const kinds = { 'chat, development or fix': hooks(null, false), 'requirement intake': hooks('/drafts/R-1', false), 'investigation': hooks('/drafts/probe-I-1', true), 'merge chat': hooks(null, false, true) };
  for (const [name, text] of Object.entries(kinds)) {
    const { allow } = JSON.parse(text).permissions;
    for (const a of ['trace', 'why']) for (const rf of [BUNDLED, '.relayforge/bin/rf.py']) assert.ok(allow.includes(`Bash(python3 ${rf} ${a}:*)`), `${name} ${a} ${rf}`);
  }
});

test('development and fix prompts tell the session to ask rf.py why before changing code it does not understand', () => {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-trp-'));
  const { develop, following, fixing } = require(path.join(main, 'prompts'));
  const req = { id: 'R-1', title: '导出', file: '.relayforge/requirements/R-1.md', state: 'open' };
  assert.match(develop(req, 'S-1'), /rf\.py why 文件\[:行\|:起-止\]/);
  assert.match(following(req, 'S-1'), /rf\.py why/);
  assert.match(fixing({ id: 'F-001', branch: 'fix/f-001' }, [{ num: '#001', title: 'x', thread: [] }], '', false), /rf\.py why/);
});

test('the main process asks for a trace only for a real requirement, fix or issue id, and passes the answer on', async () => {
  const calls = [];
  const backendPath = require.resolve(path.join(main, 'backend'));
  const real = require.cache[backendPath];
  require.cache[backendPath] = { id: backendPath, filename: backendPath, loaded: true, exports: {
    rf: async (args) => { calls.push(args.join(' ')); return args[1] === 'R-bad' ? { ok: false, stdout: '', stderr: '没有找到需求 R-bad。\n', timedOut: false } : { ok: true, stdout: JSON.stringify({ id: args[1], commit_count: 1 }), stderr: '' }; },
    lastLine: (t) => String(t).trim().split('\n').pop(), parse: (t, f) => { try { return JSON.parse(t); } catch (_) { return f; } } } };
  const tracePath = require.resolve(path.join(main, 'trace'));
  delete require.cache[tracePath];
  try {
    const { get } = require(tracePath);
    assert.equal((await get('R-20261118-ab12')).commit_count, 1);
    assert.equal((await get('I-033')).id, 'I-033');
    assert.match((await get('R-bad')).error, /没有找到/);
    assert.match((await get('; rm -rf /')).error, /编号/);
    assert.match((await get('')).error, /编号/);
    assert.deepEqual(calls, ['trace R-20261118-ab12 --json', 'trace I-033 --json', 'trace R-bad --json']);
  } finally { delete require.cache[tracePath]; if (real) require.cache[backendPath] = real; else delete require.cache[backendPath]; }
});
