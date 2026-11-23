// 测试集（应用这一侧）：rf.py test 的放行规则（只读的每种会话放行、写的只在开发和修复会话放行、只由用户触发的一律禁止），
// 开发和修复会话的开场白讲清楚测试集的写法
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const main = path.join(__dirname, '..', 'app', 'src', 'main');

test('rf.py test: read-only everywhere, work commands only in development and fix sessions, user decisions denied everywhere', () => {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-ts-'));
  const { hooks } = require(path.join(main, 'claude-settings'));
  const { BUNDLED } = require(path.join(main, 'config'));
  const kinds = { building: hooks(null, false, false, true), chat: hooks(null, false), intake: hooks('/drafts/R-1', false, false, true), probe: hooks('/drafts/probe-I-1', true, false, true), merge: hooks(null, false, true) };
  for (const [name, text] of Object.entries(kinds)) {
    const { allow, deny } = JSON.parse(text).permissions;
    for (const rf of [BUNDLED, '.relayforge/bin/rf.py']) {
      for (const a of ['list', 'show']) assert.ok(allow.includes(`Bash(python3 ${rf} test ${a}:*)`), `${name} ${a}`);
      for (const a of ['new', 'add', 'edit', 'rm', 'run', 'analyze', 'bridge']) assert.equal(allow.includes(`Bash(python3 ${rf} test ${a}:*)`), name === 'building', `${name} ${a}`);
      for (const a of ['approve', 'reject', 'stale', 'revive', 'move', 'save', 'seen']) {
        assert.ok(deny.includes(`Bash(python3 ${rf} test ${a}:*)`), `${name} ${a} denied`);
        assert.ok(!allow.some((x) => x.includes(` test ${a}`)), `${name} ${a} not allowed`);
      }
    }
  }
});

test('fix and test sessions are told to write tests as test sets; development sessions leave it to the test session', () => {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-tsp-'));
  const P = require(path.join(main, 'prompts'));
  const req = { id: 'R-1', title: '导出', file: '.relayforge/requirements/R-1.md', state: 'open' };
  const issue = { id: 'I-001', num: '#001', title: 'x', thread: [] };
  // 需求的测试集由测试会话写（测试阶段）：开发会话的开场白不再讲怎么新建测试集；问题的修复会话照旧自己写
  const texts = { fixing: P.fixing({ id: 'F-001', branch: 'fix/f-001' }, [issue], '', false), testing: P.testing(req, 'S-1', false) };
  for (const [name, text] of Object.entries(texts)) {
    assert.match(text, /测试一律写成测试集/, name);
    assert.match(text, /rf\.py test new "测什么行为" --feature RF-xxx --entry/, name);
    assert.match(text, /不含任何测试数据/, name);
    assert.match(text, /test analyze/, name);
    assert.match(text, /--why "理由" 提出改动/, name);
    if (name === 'fixing') assert.match(text, /note doing "这些测试没写成测试集的原因/, name);
    assert.match(text, /不要运行 test approve、reject、stale、revive、move、save、seen/, name);
  }
  for (const text of [P.develop(req, 'S-1'), P.following(req, 'S-1')]) {
    assert.doesNotMatch(text, /按需求文件里的验收条件生成测试集|test new "测什么行为"/);
    assert.match(text, /需求自动进入「测试中」，由这个需求单独的测试会话来测/);
    assert.match(text, /你不负责新建测试集；为了开发方便，可以用 python3 \S+ test run 跑已有的测试集/);
    assert.match(text, /req retest "修了什么"/);
  }
  assert.doesNotMatch(P.intake(req, 'S-1', [], false, '/d'), /test new/);
});
