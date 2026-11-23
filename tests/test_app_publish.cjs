// 发布（应用这一侧）：顶栏提示的各种状态、对话框要显示什么、每种会话都禁止推送、不能连点两次
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const root = path.join(__dirname, '..', 'app', 'src');
const main = path.join(root, 'main');
const Model = require(path.join(root, 'renderer', 'scripts', 'publish-model.js'));

const upstream = { remote: 'origin', branch: 'main', ref: 'origin/main' };
const base = { branch: 'main', detached: false, remotes: [{ name: 'origin', url: '/x.git' }], upstream, target: { remote: 'origin', branch: 'main', creates: false, sets_upstream: false },
  needs_remote: false, ahead: 0, behind: 0, blockers: [], warnings: [], can_publish: false, can_pull: false, state: 'synced',
  unpublished: { total: 0, requirements: [], issues: [], facts: 0, others: [], others_total: 0 } };
const st = (extra) => Object.assign({}, base, extra);

test('the indicators show the local and remote counts independently', () => {
  const ahead = st({ ahead: 9, state: 'ahead', pending_requirements: 2, unpublished: { requirements: [{ id: 'R-1' }, { id: 'R-2' }, { id: 'R-3' }], others_total: 0 } });
  assert.deepEqual([Model.pendingText(ahead), Model.indicator(ahead).text], ['待合入本地：2 个需求', '待发布到远端：3 个需求']);
  const synced = st({ pending_requirements: 2 });
  assert.deepEqual([Model.pendingText(synced), Model.indicator(synced).text], ['待合入本地：2 个需求', '已同步到远端']);
  assert.deepEqual([Model.pendingText(st({ pending_requirements: 0 })), Model.indicator(st()).text], ['', '已同步到远端']);
  const others = st({ ahead: 2, state: 'ahead', unpublished: { requirements: [], others_total: 2 } });
  assert.equal(Model.indicator(others).text, '待发布到远端：0 个需求 · 2 个其他提交');
});

test('the remote indicator keeps a quiet hint for branches that are behind or cannot be compared', () => {
  assert.match(Model.indicator(st({ ahead: 3, behind: 2 })).title, /远端也有 2 个新提交/);
  assert.deepEqual([Model.indicator(st({ remotes: [], upstream: null, target: null, state: 'no-remote' })).kind, Model.indicator(st({ remotes: [], upstream: null, target: null, state: 'no-remote' })).text], ['hint', '还没有远端']);
  const none = st({ upstream: null, state: 'no-upstream', ahead: 40, target: { remote: 'origin', branch: 'main', creates: true, sets_upstream: true } });
  assert.deepEqual([Model.indicator(none).kind, Model.indicator(none).text], ['hint', '还没设置上游分支']); // no count: there is nothing to compare with
  assert.equal(Model.indicator(st({ detached: true, branch: null })).kind, 'hint');
  assert.equal(Model.indicator(st({ behind: 4 })).text, '远端有 4 个新提交');
  assert.equal(Model.indicator(st({ target: { ...base.target, creates: true } })).text, '远端分支不存在');
  assert.equal(Model.indicator({ error: 'x' }).kind, 'none');
  assert.equal(Model.indicator(null).kind, 'none');
});

test('the dialog spells out where it pushes, warns that it may deploy, and lists what goes out by title', () => {
  const un = { total: 9, requirements: [{ id: 'R-1', title: '导出账单' }], issues: [{ id: 'I-3', num: '#003', title: '启动时打印旧的' }], facts: 5,
    others: [{ hash: 'abc1234', subject: '我自己改的' }], others_total: 1 };
  const m = Model.dialog(st({ ahead: 9, state: 'ahead', unpublished: un, can_publish: true }));
  assert.equal(m.target, 'main → origin/main');
  assert.match(m.deploy, /部署/);
  assert.deepEqual(m.requirements, [{ id: 'R-1', title: '导出账单' }]);
  assert.equal(m.issues[0].title, '启动时打印旧的');
  assert.match(m.factsText, /5 个/);
  assert.equal(m.othersTotal, 1);
  assert.deepEqual([m.can.publish, m.can.check, m.primary, m.ack], [true, true, '发布', '']);
});

test('without an upstream and one remote the dialog says it will create the branch and wants an explicit yes', () => {
  const s = st({ upstream: null, state: 'no-upstream', ahead: 2, target: { remote: 'origin', branch: 'main', creates: true, sets_upstream: true }, can_publish: true });
  const m = Model.dialog(s, '', false);
  assert.match(m.create, /新建/);
  assert.match(m.ack, /origin\/main/);
  assert.equal(m.primary, '新建远端分支并发布');
  assert.equal(m.can.publish, false);
  assert.equal(Model.dialog(s, '', true).can.publish, true);
});

test('with several remotes and no upstream the user picks one first', () => {
  const s = st({ upstream: null, target: null, needs_remote: true, state: 'no-upstream', remotes: [{ name: 'origin', url: 'a' }, { name: 'second', url: 'b' }],
    blockers: [{ code: 'pick-remote', text: '要选一个' }] });
  const m = Model.dialog(s, '', true);
  assert.deepEqual(m.remotes.map((r) => r.name), ['origin', 'second']);
  assert.equal(m.can.publish, false);
  const picked = Model.dialog(st({ upstream: null, needs_remote: true, remotes: s.remotes, ahead: 1, target: { remote: 'second', branch: 'main', creates: true, sets_upstream: true } }), 'second', true);
  assert.equal(picked.target, 'main → second/main');
  assert.equal(picked.can.publish, true);
});

test('behind the remote: blocked, with a pull action; diverged: blocked, no pull, and the reason', () => {
  const behind = Model.dialog(st({ behind: 3, state: 'behind', can_pull: true, blockers: [{ code: 'behind', text: '远端比本地多 3 个提交' }] }));
  assert.deepEqual([behind.can.publish, behind.can.pull, behind.pull], [false, true, '先拉取（3 个新提交）']);
  assert.equal(behind.blockers.length, 1);
  assert.equal(behind.empty, '');
  const split = Model.dialog(st({ ahead: 1, behind: 2, state: 'diverged', blockers: [{ code: 'diverged', text: '两边分叉了' }] }));
  assert.deepEqual([split.can.publish, split.can.pull, split.diverged, split.pull], [false, false, true, '']);
});

test('warnings and blockers pass through, and a nothing-to-publish state is said so', () => {
  const m = Model.dialog(st({ ahead: 1, can_publish: true, warnings: [{ code: 'loose', text: '有 2 处代码改动没提交', paths: ['a.py', 'b.py'], count: 2 }] }));
  assert.deepEqual(m.warnings[0].paths, ['a.py', 'b.py']);
  assert.equal(m.can.publish, true); // a warning does not block
  assert.match(Model.dialog(st()).empty, /没有需要发布/);
  const stuck = Model.dialog(st({ ahead: 1, blockers: [{ code: 'busy', text: '有一次合并没做完' }] }));
  assert.deepEqual([stuck.can.publish, stuck.blockers[0].code], [false, 'busy']);
  assert.equal(Model.dialog({ error: '读不到' }).can.publish, false);
});

test('a failed fetch is shown in the dialog without hiding the status', () => {
  const m = Model.dialog(st({ ahead: 1, fetch: { ok: false, code: 'network', error: '连不上远端', detail: 'fatal: x' } }));
  assert.equal(m.fetchError.code, 'network');
  assert.equal(m.can.publish, true);
});

test('every kind of session may not push or run publish, but may read the publish status', () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-pub-'));
  require(path.join(main, 'state')).state.project = project;
  const { hooks } = require(path.join(main, 'claude-settings'));
  const { BUNDLED } = require(path.join(main, 'config'));
  const kinds = { 'chat, development or fix': hooks(null, false), 'requirement intake': hooks('/drafts/R-1', false), 'investigation': hooks('/drafts/probe-I-1', true) };
  for (const [name, text] of Object.entries(kinds)) {
    const { allow, deny } = JSON.parse(text).permissions;
    assert.ok(deny.includes('Bash(git push:*)'), name);
    assert.ok(deny.some((x) => x.startsWith('Bash(git * push')), name); // git -C dir push
    for (const a of ['run', 'pull']) for (const rf of [BUNDLED, '.relayforge/bin/rf.py']) assert.ok(deny.includes(`Bash(python3 ${rf} publish ${a}:*)`), `${name} ${a} ${rf}`);
    assert.ok(allow.includes(`Bash(python3 ${BUNDLED} publish status:*)`) && allow.includes(`Bash(python3 ${BUNDLED} publish list:*)`), name);
    assert.ok(!allow.some((x) => /publish (run|pull)|git push/.test(x)), name);
  }
});

// 主进程这一侧：假的 rf.py，看一次只做一件事、失败的样子、成功后发的通知
function loadPublish(handler) {
  const calls = [], sent = [];
  const fakeBackend = { rf: async (args) => { calls.push(args.join(' ')); return handler(args); }, lastLine: (t) => String(t).trim().split('\n').pop(), parse: (t, f) => { try { return JSON.parse(t); } catch (_) { return f; } } };
  const backendPath = require.resolve(path.join(main, 'backend'));
  const statePath = require.resolve(path.join(main, 'state'));
  const publishPath = require.resolve(path.join(main, 'publish'));
  const realBackend = require.cache[backendPath], realState = require.cache[statePath];
  require.cache[backendPath] = { id: backendPath, filename: backendPath, loaded: true, exports: fakeBackend };
  delete require.cache[publishPath];
  const stateModule = require(statePath);
  stateModule.state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-pubmain-'));
  stateModule.state.win = { isDestroyed: () => false, webContents: { send: (channel, payload) => sent.push([channel, payload]) } };
  const publish = require(publishPath);
  const restore = () => { delete require.cache[publishPath]; if (realBackend) require.cache[backendPath] = realBackend; else delete require.cache[backendPath]; stateModule.state.win = null; void realState; };
  return { publish, calls, sent, restore };
}

test('pressing publish twice runs it once; the second press is told it is busy', async () => {
  let release;
  const hold = new Promise((r) => { release = r; });
  const made = { ok: true, record: 'P-1', remote: 'origin', branch: 'main', target: 'main', commits: 4, requirements: [{ id: 'R-1' }], issues: [], facts: 2, others_total: 1, status: { ahead: 0 } };
  const { publish, calls, sent, restore } = loadPublish(async (args) => { if (args[1] === 'run') { await hold; return { ok: true, stdout: JSON.stringify(made), stderr: '' }; } return { ok: true, stdout: '{}', stderr: '' }; });
  try {
    const first = publish.run({});
    const second = await publish.run({});
    const third = await publish.pull();
    assert.equal(second.busy, true);
    assert.equal(third.busy, true);
    assert.equal(publish.busy(), '发布');
    assert.deepEqual(sent.filter((x) => x[0] === 'publish:busy').map((x) => x[1].working), ['发布']);
    release();
    const done = await first;
    assert.equal(done.ok, true);
    assert.equal(calls.filter((c) => c.startsWith('publish run')).length, 1);
    assert.equal(publish.busy(), null);
    assert.deepEqual(sent.filter((x) => x[0] === 'publish:busy').map((x) => x[1].working), ['发布', null]);
    assert.match(sent.find((x) => x[0] === 'notice')[1], /已发布 main → origin\/main/);
    assert.deepEqual(sent.find((x) => x[0] === 'publish')[1], { ahead: 0 }); // the indicator gets the new state
    assert.equal((await publish.run({})).busy, undefined); // and it can be pressed again afterwards
  } finally { restore(); }
});

test('a failed push gives the reason and the code, and nothing else changes', async () => {
  const bad = { ok: false, stdout: JSON.stringify({ ok: false, code: 'rejected', error: '推送被拒绝了。', detail: '! [rejected] main -> main (non-fast-forward)', record: 'P-2' }), stderr: 'x' };
  const { publish, sent, restore } = loadPublish(async (args) => (args[1] === 'run' ? bad : { ok: true, stdout: JSON.stringify({ ahead: 1 }), stderr: '' }));
  try {
    const out = await publish.run({ remote: 'origin' });
    assert.deepEqual([out.error, out.code, out.record], ['推送被拒绝了。', 'rejected', 'P-2']);
    assert.match(out.detail, /non-fast-forward/);
    assert.equal(sent.some((x) => x[0] === 'notice'), false);
    assert.equal(publish.busy(), null);
  } finally { restore(); }
});

test('a timeout is reported as one', async () => {
  const { publish, restore } = loadPublish(async () => ({ ok: false, stdout: '', stderr: 'killed', timedOut: true }));
  try {
    const out = await publish.run({});
    assert.equal(out.code, 'timeout');
    assert.match(out.error, /太久/);
  } finally { restore(); }
});

// 真的走一遍：临时仓库加一个本地裸仓库当远端（不碰网络），用主进程这一层的 status / run
test('end to end through the main process module with a local bare remote', async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-pubreal-'));
  const git = (cwd, ...a) => execFileSync('git', ['-C', cwd, ...a], { encoding: 'utf8', env: { ...process.env, GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@e.x', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@e.x' } });
  const project = path.join(tmp, 'p'), bare = path.join(tmp, 'remote.git');
  fs.mkdirSync(project); git(project, 'init', '-q', '-b', 'main'); git(tmp, 'init', '-q', '--bare', '-b', 'main', bare);
  const env = { ...process.env, RELAYFORGE_HOME: path.join(tmp, 'home'), GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@e.x', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@e.x' };
  const { BUNDLED } = require(path.join(main, 'config'));
  execFileSync('python3', [BUNDLED, 'init', project], { env });
  git(project, 'add', '-A'); git(project, 'commit', '-qm', 'init'); git(project, 'remote', 'add', 'origin', bare);
  Object.assign(process.env, { RELAYFORGE_HOME: env.RELAYFORGE_HOME, GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@e.x', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@e.x', RELAYFORGE_EXTRACTOR: '["true"]' });
  const sent = [];
  const stateModule = require(path.join(main, 'state'));
  stateModule.state.project = project;
  stateModule.state.win = { isDestroyed: () => false, webContents: { send: (c, p) => sent.push([c, p]) } };
  const publish = require(path.join(main, 'publish'));
  // 应用的标记：config 在这个文件里可能早就按原来的 RELAYFORGE_HOME 载入了，所以把应用的标记（真的那个）也登记进这次的临时目录
  const marker = require(path.join(main, 'app-token')).envFor()[require(path.join(main, 'app-token')).TOKEN_ENV];
  fs.mkdirSync(path.join(env.RELAYFORGE_HOME, 'apps'), { recursive: true });
  fs.writeFileSync(path.join(env.RELAYFORGE_HOME, 'apps', process.pid + '.json'), JSON.stringify({ pid: process.pid, token_sha256: require('crypto').createHash('sha256').update(marker).digest('hex') }), { mode: 0o600 });
  try {
    const before = await publish.status({});
    assert.equal(before.state, 'no-upstream');
    assert.equal(before.target.creates, true);
    const made = await publish.run({ remote: 'origin' });
    assert.equal(made.ok, true, JSON.stringify(made));
    assert.equal(git(bare, 'rev-parse', 'main').trim(), git(project, 'rev-parse', 'HEAD').trim());
    const after = await publish.status({});
    assert.deepEqual([after.state, after.ahead], ['synced', 0]);
    assert.equal(Model.indicator(after).text, '已同步到远端');
    assert.equal((await publish.list()).length, 1);
    assert.match(sent.filter((x) => x[0] === 'notice').pop()[1], /已发布 main → origin\/main/);
    fs.writeFileSync(path.join(project, 'a.txt'), 'x'); git(project, 'add', '-A'); git(project, 'commit', '-qm', '加一个文件');
    git(project, 'remote', 'set-url', 'origin', path.join(tmp, 'gone.git'));
    const failed = await publish.run({});
    assert.equal(failed.code, 'network');
    assert.ok(failed.error && failed.record);
  } finally { stateModule.state.win = null; }
});

// ---------- 分叉时的「让 AI 来合并」 ----------

test('the merge button shows only when both sides have commits the other lacks, or a pull failed for that reason', () => {
  const split = Model.dialog(st({ ahead: 1, behind: 2, state: 'diverged', blockers: [{ code: 'diverged', text: '两边分叉了' }] }));
  assert.equal(split.aiMerge, '让 AI 来合并');
  assert.match(split.divergedText, /检查远端/);
  assert.equal(split.merging, null);
  // only behind: the plain pull is the answer, no merge button; only ahead or in sync: neither
  assert.equal(Model.dialog(st({ behind: 3, state: 'behind', can_pull: true })).aiMerge, '');
  assert.equal(Model.dialog(st({ ahead: 2, state: 'ahead' })).aiMerge, '');
  assert.equal(Model.dialog(st()).aiMerge, '');
  // the pull was refused because a fast-forward was impossible (the picture on screen was out of date)
  assert.equal(Model.dialog(st({ behind: 3, state: 'behind', can_pull: true }), '', false, { pullDiverged: true }).aiMerge, '让 AI 来合并');
  assert.equal(Model.dialog(st({ behind: 3, state: 'behind' }), '', false, { pullDiverged: false }).aiMerge, '');
  // nothing to merge against without an upstream, or off any branch
  assert.equal(Model.dialog(st({ upstream: null, ahead: 1, behind: 1 })).aiMerge, '');
  assert.equal(Model.dialog(st({ detached: true, branch: null, ahead: 1, behind: 1 })).aiMerge, '');
});

test('a merge left unfinished in the project directory is explained, with a way back only when the chat is known', () => {
  const busy = { code: 'busy', kind: 'merge', text: '项目目录里有一次合并还没做完' };
  const unknown = Model.dialog(st({ ahead: 1, behind: 2, state: 'diverged', busy: 'merge', blockers: [busy] }));
  assert.deepEqual([unknown.aiMerge, unknown.merging.kind, unknown.merging.back, unknown.can.publish], ['', 'merge', '', false]);
  assert.match(unknown.merging.text, /合并还没做完/);
  const known = Model.dialog(st({ ahead: 1, behind: 2, state: 'diverged', busy: 'merge', blockers: [busy], merge_chat: { id: 'session:abc' } }));
  assert.deepEqual([known.merging.back, known.merging.chat, known.aiMerge], ['回到那个聊天', 'session:abc', '']);
  const rebase = Model.dialog(st({ busy: 'rebase', blockers: [{ code: 'busy', kind: 'rebase', text: 'x' }] }));
  assert.match(rebase.merging.text, /变基/);
});

test('the opening prompt: merge the remote branch here, keep both sides under .relayforge/, run tests, commit, never push', () => {
  const { mergingRemote } = require(path.join(main, 'prompts'));
  const text = mergingRemote({ remote: 'origin', branch: 'main', current: 'main', behind: 2, ahead: 3, both: ['app.py'], facts: ['.relayforge/features/RF-101-x.md'], dirty: [] });
  assert.match(text, /git merge origin\/main/);
  assert.match(text, /已经抓取过/);
  assert.match(text, /不要整个文件只选一边/);
  assert.match(text, /\.relayforge\/ 下的 「\.relayforge\/features\/RF-101-x\.md」/);
  assert.match(text, /一条记录都不能丢/);
  assert.match(text, /哪些文件冲突了/);
  assert.match(text, /跑一遍/);
  assert.match(text, /git commit/);
  assert.match(text, /不要推送.*git push.*publish run.*publish pull/);
  assert.match(text, /回到「发布」，点「检查远端」/);
  assert.doesNotMatch(mergingRemote({ remote: 'o', branch: 'b', current: 'b', behind: 1, ahead: 1 }), /两边都改过的文件/);
  assert.match(mergingRemote({ remote: 'o', branch: 'b', current: 'b', behind: 1, ahead: 1, dirty: ['项目目录里有 2 处代码改动还没提交'] }), /没提交的改动/);
});

test('a merge chat can merge and commit in the project directory but still cannot push', () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-pubm-'));
  require(path.join(main, 'state')).state.project = project;
  const { hooks } = require(path.join(main, 'claude-settings'));
  const plain = JSON.parse(hooks(null, false, false)).permissions;
  const chat = JSON.parse(hooks(null, false, true)).permissions;
  for (const a of ['merge', 'commit', 'add', 'status', 'diff']) assert.ok(chat.allow.includes(`Bash(git ${a}:*)`), a);
  assert.ok(!plain.allow.includes('Bash(git merge:*)')); // other chats are asked as before
  assert.ok(chat.deny.includes('Bash(git push:*)') && chat.deny.some((x) => /publish run/.test(x)));
  // nothing denies editing or merging in the project directory for a chat
  assert.ok(!chat.deny.some((x) => /^(Edit|Write)\(/.test(x) || /git (merge|commit|add)/.test(x)));
  assert.ok(!chat.allow.some((x) => /git push|publish (run|pull)/.test(x)));
});

test('asking the AI to merge fetches first, refuses when nothing diverges, and remembers the chat it opened', async () => {
  const diverged = { branch: 'main', upstream: { remote: 'origin', branch: 'main', ref: 'origin/main' }, ahead: 1, behind: 2, fetch: { ok: true }, blockers: [{ code: 'diverged' }],
    warnings: [{ code: 'loose', text: '项目目录里有 1 处代码改动还没提交' }], divergence: { both: ['a.py'], facts_both: ['.relayforge/x.md'], total: 2 }, busy: null };
  let answer = diverged;
  const { publish, calls, restore } = loadPublish(async () => ({ ok: true, stdout: JSON.stringify(answer), stderr: '' }));
  const { state } = require(path.join(main, 'state'));
  try {
    const made = await publish.aiMerge();
    assert.equal(made.ok, true);
    assert.ok(calls[0].includes('--fetch'), calls[0]);
    assert.match(made.say, /git merge origin\/main/);
    assert.match(made.say, /a\.py/);
    assert.match(made.say, /没提交的改动/);
    assert.equal(made.title, '合并 origin/main');
    assert.ok(state.mergeChats.has(made.sessionId));
    // synced meanwhile: no chat is opened
    answer = { ...diverged, behind: 0, ahead: 1 };
    const none = await publish.aiMerge();
    assert.match(none.error, /不用合并/);
    // a failed fetch: said plainly, nothing opened
    answer = { ...diverged, fetch: { ok: false, code: 'network', error: '连不上远端', detail: 'fatal: x' } };
    assert.match((await publish.aiMerge()).error, /连不上远端/);
    // a merge already half done: no second chat; the known chat is offered back
    state.runs.set('session:' + made.sessionId, {});
    answer = { ...diverged, busy: 'merge' };
    const again = await publish.aiMerge();
    assert.equal(again.merging, true);
    assert.equal(again.status.merge_chat.id, 'session:' + made.sessionId);
    assert.equal(publish.mergeChatId(), 'session:' + made.sessionId);
  } finally { state.runs.clear(); state.mergeChats.clear(); restore(); }
});
