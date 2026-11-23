// 引入 Agent 做衔接（应用这一侧）：安装的三级放行（每一级的代表命令）、各种会话拿到的权限规则、应用的标记（和 rf.py 对得上、不进会话的环境）、
// 「让 AI 来处理」和开发会话的提示词讲清三级、准备步骤和 AI 建议在界面上的样子、「看过了」只有点按钮才算
const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-bridge-'));
process.env.RELAYFORGE_HOME = path.join(TMP, 'rfhome'); // config.js 在第一次 require 时读它
const repo = path.join(__dirname, '..');
const main = path.join(repo, 'app', 'src', 'main');
const scripts = path.join(repo, 'app', 'src', 'renderer', 'scripts');
const R = require(path.join(main, 'install-rules'));
const { RF_HOME } = require(path.join(main, 'config'));

test('three tiers: representative commands of each', () => {
  const allow = ['npm ci', 'npm install', 'npm i --save-dev vitest', 'pnpm install', 'yarn install', 'yarn', 'python3 -m venv .venv', '.venv/bin/pip install -r requirements.txt',
    '.venv/bin/python -m pip install pytest', 'mvn -q compile', 'mvn test-compile', 'mvn dependency:build-classpath -Dmdep.outputFile=cp.txt', './mvnw -q -DskipTests compile',
    'gradle classes', './gradlew testClasses', './gradlew dependencies', 'javac -d target/rf-classes src/test/java/RfBills.java', 'java -version', 'which java', 'docker ps', 'node --version'];
  const ask = ['brew install openjdk@17', 'apt-get install -y postgresql', 'apt install redis', 'sudo make install', 'sudo npm i -g pnpm', 'npm install -g typescript', 'npm i -g pnpm',
    'npm install --global yarn', 'pip install requests', 'pip3 install -r requirements.txt', 'python3 -m pip install x', 'docker run -d -p 5432:5432 postgres:16', 'docker compose up -d',
    'docker compose -f docker-compose.test.yml up -d', 'docker-compose up', 'docker pull mysql:8', 'sdk install java 17.0.10-tem', 'nvm install 20', 'pyenv install 3.12.4', 'asdf install',
    "echo 'export JAVA_HOME=/opt/jdk' >> ~/.zshrc", 'npm ci && sudo make install'];
  const deny = ['mvn deploy', 'mvn -q clean deploy', 'mvn release:prepare', './mvnw -B deploy -DskipTests', 'npm publish', 'npm publish --access public', 'docker push acme/api:1',
    'gradle publish', 'cat ~/.relayforge/envs/proj-1234.json', `cat ${RF_HOME}/envs/x.json`, `ls ${RF_HOME}/apps`, 'npm ci && npm publish'];
  for (const c of allow) assert.equal(R.classify(c, RF_HOME), 'allow', c);
  for (const c of ask) assert.equal(R.classify(c, RF_HOME), 'ask', c);
  for (const c of deny) assert.equal(R.classify(c, RF_HOME), 'deny', c);
  for (const c of ['rm -rf build', 'curl https://example.com', 'make']) assert.equal(R.classify(c, RF_HOME), null, c); // 都不是：照 Claude Code 默认的问你
  assert.ok(R.matches('mvn -q:*', 'mvn -q') && !R.matches('mvn -q:*', 'mvn -quiet'));
  const r = R.rules(RF_HOME);
  assert.ok(r.ask.includes('Edit(~/.zshrc)') && r.ask.includes('Edit(~/.bashrc)'));
  assert.ok(![...r.allow, ...r.ask, ...r.deny].some((x) => x.startsWith('Write(')), 'Write(路径) 规则 Claude Code 对不上，只用 Edit');
  assert.ok(r.allow.every((x) => x.startsWith('Bash(')) && r.deny.some((x) => x.startsWith('Read(')));
});

test('sessions: the allow tier only where environments get handled; ask and deny everywhere; env and test commands', () => {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(TMP, 'p-'));
  const { hooks } = require(path.join(main, 'claude-settings'));
  const { BUNDLED } = require(path.join(main, 'config'));
  const kinds = { building: hooks(null, false, false, true), envFix: hooks(null, false, false, false, false, false, true), chat: hooks(null, false), fill: hooks(null, false, false, false, false, true),
    intake: hooks('/drafts/R-1', false, false, true, false, false, true), probe: hooks('/drafts/probe-I-1', true, false, true, false, false, true) };
  for (const [name, text] of Object.entries(kinds)) {
    const { allow, ask, deny } = JSON.parse(text).permissions;
    const tier = name === 'building' || name === 'envFix';
    assert.equal(allow.includes('Bash(npm ci:*)'), tier, `${name} npm ci`);
    assert.equal(allow.includes('Bash(mvn -q:*)'), tier, `${name} mvn -q`);
    assert.ok(ask.includes('Bash(brew:*)') && ask.includes('Bash(sudo:*)') && ask.includes('Bash(npm install -g:*)'), `${name} ask`);
    assert.ok(deny.includes('Bash(mvn deploy:*)') && deny.includes('Bash(docker push:*)') && deny.includes('Bash(git push:*)'), `${name} deny`);
    assert.ok(!allow.some((x) => /\b(brew|sudo|apt|apt-get)\b|docker run|deploy|npm publish|-g\b/.test(x)), `${name} nothing system-wide allowed`);
    for (const rf of [BUNDLED, '.relayforge/bin/rf.py']) {
      for (const a of ['approve', 'ai-secrets', 'reveal', 'adopt', 'save']) assert.ok(deny.includes(`Bash(python3 ${rf} env ${a}:*)`), `${name} env ${a} denied`);
      assert.ok(deny.includes(`Bash(python3 ${rf} test config:*)`), `${name} test config denied`);
      for (const a of ['prep', 'prep-run', 'handoff', 'propose']) assert.equal(allow.includes(`Bash(python3 ${rf} env ${a}:*)`), ['building', 'envFix', 'fill'].includes(name), `${name} env ${a}`);
      assert.equal(allow.includes(`Bash(python3 ${rf} test run:*)`), ['building', 'envFix'].includes(name), `${name} test run`);
      assert.ok(!allow.includes(`Bash(python3 ${rf} test add:*)`) || name === 'building', `${name} test add`);
    }
  }
});

test('the app marker: rf.py accepts it from the app, not from a session; sessions never get it', () => {
  const T = require(path.join(main, 'app-token'));
  const env = T.envFor();
  const token = env.RELAYFORGE_APP_TOKEN;
  assert.match(token, /^[0-9a-f]{48}$/);
  const reg = fs.readFileSync(T.file, 'utf8');
  assert.ok(!reg.includes(token), 'only the hash is written');
  assert.equal(fs.statSync(T.file).mode & 0o777, 0o600);
  process.env.RELAYFORGE_APP_TOKEN = token; // 就算它跑进了应用自己的环境，开会话用的 cleanEnv 也会去掉
  assert.equal(require(path.join(main, 'backend')).cleanEnv().RELAYFORGE_APP_TOKEN, undefined);
  delete process.env.RELAYFORGE_APP_TOKEN;
  // 和 rf.py 对一下：应用（这个进程）直接启动的 rf.py 认它；经过一层 shell 的不认
  const proj = path.join(TMP, 'proj');
  const setup = `import sys, os, subprocess; sys.path.insert(0, ${JSON.stringify(path.join(repo, 'backend'))}); import rf; from relayforge.envs import store as E; from relayforge.envs import edits as EE
from pathlib import Path; root = Path(${JSON.stringify(proj)}); root.mkdir(); rf.init(root, hooks=False)
subprocess.run(['git', '-C', str(root), 'init', '-q', '-b', 'main'], check=True); subprocess.run(['git', '-C', str(root), 'add', '-A'], check=True)
subprocess.run(['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@e', 'commit', '-qm', 'i'], check=True)
EE.env_new(root, '本机'); EE.env_save(root, 'ENV-001', {'name': '本机', 'prep': [{'command': 'echo hi'}]})`;
  const base = { ...process.env, GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@e', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@e' };
  const made = spawnSync('python3', ['-c', setup], { env: base, encoding: 'utf8' });
  assert.equal(made.status, 0, made.stderr);
  const rfpy = path.join(repo, 'backend', 'rf.py');
  const args = [rfpy, 'env', 'approve', 'ENV-001', '--root', proj];
  const bad = spawnSync('python3', args, { env: base, encoding: 'utf8' });
  assert.equal(bad.status, 1);
  assert.match(bad.stderr, /只能由你在应用里做/);
  // 后面接 `; exit $?`：只有一条命令时 sh 会直接 exec 成 rf.py，父进程又变回了这个「应用」，就不是在模拟会话的 shell 了
  const shell = spawnSync('sh', ['-c', 'python3 ' + args.map((a) => `'${a}'`).join(' ') + '; exit $?'], { env: { ...base, ...env }, encoding: 'utf8' });
  assert.equal(shell.status, 1, 'through a shell (a session) it does not count');
  const good = spawnSync('python3', args, { env: { ...base, ...env }, encoding: 'utf8' });
  assert.equal(good.status, 0, good.stderr);
  assert.match(good.stdout, /同意执行 P1/);
  T.unregister();
  assert.ok(!fs.existsSync(T.file));
});

test('prompts: the handling chat and development sessions explain the three tiers', () => {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(TMP, 'pp-'));
  const P = require(path.join(main, 'prompts'));
  const say = P.envHandling({ title: '处理：测试集 TS-002 跑不起来', text: '【处理：测试集 TS-002「导出」跑不起来】\n错误……' });
  for (const text of [say, P.develop({ id: 'R-1', title: 'x', file: 'a.md' }, 'S-1'), P.fixing({ id: 'F-001', branch: 'fix/f-001' }, [{ num: '#1', title: 'x', thread: [] }], '', false)]) {
    assert.match(text, /项目目录里的安装和构建预先放行/);
    assert.match(text, /每次都要我点同意——brew、apt/);
    assert.match(text, /先用一句话告诉我装什么、为什么要装、装到哪里/);
    assert.match(text, /一律禁止——mvn deploy \/ release、npm publish、docker push、git push/);
  }
  assert.match(say, /^【处理：测试集 TS-002「导出」跑不起来】/);
  // 依赖上的「要用到什么」：这个需求要用到前置需求的，和谁在等它、要用到它的什么
  const waited = { id: 'R-1', title: '日期工具', file: 'a.md', state: 'draft', depends_on: [{ id: 'R-0', title: '基础', state: 'open', need: '任务表' }],
    dependents: [{ id: 'R-2', title: '每日汇总', need: 'parseDate' }, { id: 'R-3', title: '周报', need: '' }] };
  for (const text of [P.develop(waited, 'S-1'), P.following(waited, 'S-1'), P.intake(waited, 'S-1', [], false, '/tmp/d')]) {
    assert.match(text, /这个需求要用到前置需求的：「基础」（R-0）：任务表。/);
    assert.match(text, /这些需求在等这个需求：「每日汇总」（R-2）要用到它的parseDate；「周报」（R-3）（没写要用到什么）。/);
    assert.match(text, /要拆分这个需求时，在建议正文里逐样说明它们留在哪一项/);
  }
  assert.equal(P.dependencyNeeds({ id: 'R-1', title: 'x' }), '', 'nothing to say without dependencies');
  assert.match(P.develop({ id: 'R-1', title: 'x', file: 'a.md' }, 'S-1'), /env propose 测试环境编号 --file -/);
});

test('env page model: preparation steps, the first-run confirmation, AI suggestions', () => {
  const M = require(path.join(scripts, 'env-model.js'));
  const data = { name: '本机', prep: [{ id: 'P1', note: '装依赖', command: 'npm ci', dir: 'web', when: 'changed', watch: ['web/package-lock.json'], timeout: 300, approved: true, last: { ok: false, code: 1 } },
    { id: 'P2', note: '编译', command: 'mvn -q compile', dir: '', when: 'always', watch: [], timeout: 600, approved: false }] };
  const d = M.draftFrom(data);
  assert.deepEqual(d.prep[0], { id: 'P1', note: '装依赖', command: 'npm ci', dir: 'web', when: 'changed', watch: 'web/package-lock.json', timeout: '300' });
  assert.equal(M.isDirty(d, data), false);
  d.prep[0].watch = 'web/package-lock.json, web/package.json';
  d.prep.push(M.blankRow('prep'));
  assert.deepEqual(M.changedSections(d, data), ['准备步骤']);
  const p = M.payloadOf(d);
  assert.deepEqual(p.prep[0].watch, ['web/package-lock.json', 'web/package.json']);
  assert.equal(p.prep[2].id, undefined); // 新加的由程序给编号
  assert.deepEqual(M.prepState(data.prep[0]), { cls: 'r', text: '上次失败（退出码 1）' });
  assert.equal(M.prepState(data.prep[1]).text, '还没同意执行');
  const html = M.approvalHtml([dict(data.prep[1], { n: 2, abs_dir: '/Users/me/bills' })], '本机');
  assert.match(html, /mvn -q compile/);
  assert.match(html, /执行目录：<span class="mono">\/Users\/me\/bills<\/span>/);
  assert.match(html, /AI 的会话不能替你同意/);
  const items = M.proposalItems({ items: [
    { k: 1, kind: 'prep', note: '编译', command: './mvnw -q compile', dir: '', when: 'changed', watch: ['pom.xml'], state: 'pending', why: '先编译' },
    { k: 2, kind: 'testset', set: 'TS-003', title: '金额', fields: { classpath: ['target/classes', 'lib/*'], build: '' }, state: 'pending' }] });
  assert.equal(items[0].kind, '准备步骤');
  assert.equal(items[0].text, '编译：./mvnw -q compile · pom.xml 变了才执行');
  assert.match(items[0].note, /第一次执行前还会请你看一眼命令/);
  assert.equal(items[1].text, 'TS-003「金额」：classpath target/classes : lib/* · build （清空）');
});
function dict(a, b) { return Object.assign({}, a, b); }

test('test viewer: broken sets get 「让 AI 来处理」, AI-added data is seen only by the button', () => {
  const TM = require(path.join(scripts, 'tests-model.js'));
  assert.equal(TM.isBroken({ last: { result: 'broken' } }), true);
  assert.equal(TM.isBroken({ last: { result: 'broken' }, stale: true }), false);
  assert.equal(TM.isBroken({ last: { result: 'fail' } }), false);
  assert.equal(TM.viewerCounts([{ n: 1, unseen: true }, { n: 2 }, { n: 3, unseen: true }]).unseen, 2);
  const src = fs.readFileSync(path.join(scripts, 'test-viewer.js'), 'utf8');
  const click = src.slice(src.indexOf('if (b.dataset.n) {'), src.indexOf('if (b.dataset.say)'));
  assert.doesNotMatch(click, /seen/, 'opening a case does not mark it seen');
  assert.match(src, /v === 'seen'\) \{ window\.shell\.testsAct\(\{ action: 'seen', id: TV\.id, caseN: TV\.n \}\)/);
  assert.match(src, /v === 'seenall'\) \{ window\.shell\.testsAct\(\{ action: 'seen', id: TV\.id \}\)/);
});

// 主进程：把 rf.py 换成假的，看调了哪些命令
function loadMain(answers) {
  const calls = [], sent = [];
  for (const k of Object.keys(require.cache)) if (k.startsWith(main)) delete require.cache[k];
  const backendPath = require.resolve(path.join(main, 'backend'));
  const real = require(backendPath);
  const rf = async (args, rootDir, agent, timeout, input) => {
    calls.push({ args, input, timeout });
    const key = args.slice(0, 2).join(' ');
    const made = typeof answers[key] === 'function' ? answers[key](args) : answers[key];
    return made === undefined ? { ok: false, stdout: '', stderr: '不认识', timedOut: false } : { ok: !made.error, stdout: made.error ? '' : JSON.stringify(made), stderr: made.error || '', timedOut: false };
  };
  require.cache[backendPath].exports = Object.assign({}, real, { rf });
  const { state } = require(path.join(main, 'state'));
  state.project = fs.mkdtempSync(path.join(TMP, 'm-'));
  state.win = { isDestroyed: () => false, webContents: { send: (c, p) => sent.push([c, p]) } };
  state.runs = new Map();
  return { T: require(path.join(main, 'testsets')), E: require(path.join(main, 'envs')), state, calls, sent };
}

test('main: a run first asks about preparation steps not agreed to; nothing runs until you agree', async () => {
  const blocked = [{ id: 'P1', n: 1, note: '装依赖', command: 'npm ci', abs_dir: '/p/web' }];
  const { T, calls } = loadMain({ 'test run': (args) => (args.includes('--prep-check') ? { blocked, env: { id: 'ENV-001', name: '本机' }, place: '/p' } : { sets: [] }) });
  const out = await T.run({ ids: ['TS-001'] });
  assert.deepEqual(out, { needApproval: blocked, env: { id: 'ENV-001', name: '本机' }, place: '/p' });
  assert.deepEqual(calls.map((c) => c.args.join(' ')), ['test run TS-001 --prep-check --json']);
  const { E, calls: c2 } = loadMain({ 'env approve': { approved: ['P1'] }, 'env ai-secrets': { ai_secrets: true }, 'env prep-run': { ran: [], error: null } });
  await E.act({ action: 'approve', id: 'ENV-001', steps: ['P1', '; rm -rf /'] });
  await E.act({ action: 'ai-secrets', id: 'ENV-001', on: true });
  await E.act({ action: 'ai-secrets', id: 'ENV-001', on: false });
  await E.prepRun({ id: 'ENV-001', steps: ['P2'] });
  assert.deepEqual(c2.map((c) => c.args.join(' ')), ['env approve ENV-001 P1 --json', 'env ai-secrets ENV-001 on --json', 'env ai-secrets ENV-001 off --json', 'env prep-run ENV-001 P2 --json']);
  const { T: T3 } = loadMain({ 'test config': { id: 'TS-003', changed: ['classpath'] } });
  assert.deepEqual(await T3.config({ id: 'TS-003', payload: { classpath: ['lib/*'] } }), { id: 'TS-003', changed: ['classpath'] });
});

test('main: 「让 AI 来处理」 goes to the open requirement\'s development session, otherwise a new chat that gets the handling rules', async () => {
  const handoff = { title: '处理：测试集 TS-004「合计行」跑不起来', text: '【处理：测试集 TS-004「合计行」跑不起来】\n错误……', env: { id: 'ENV-001', name: '本机' } };
  const reqs = [{ id: 'R-2', title: '导出合计', state: 'open', worktree: '/w' }];
  const { E, calls, sent, state } = loadMain({ 'env handoff': handoff, 'test show': { id: 'TS-004', in_progress: 'R-2', cases_list: [] }, 'req list': reqs });
  const toReq = await E.aiHandle({ kind: 'set', ref: 'TS-004', req: 'R-2' });
  assert.equal(toReq.kind, 'req');
  assert.equal(toReq.run, 'R-2');
  assert.deepEqual(calls[0].args, ['env', 'handoff', 'set', 'TS-004', '--req', 'R-2', '--json']);
  assert.equal(sent.find(([channel]) => channel === 'tests:say')?.[1].id, 'R-2'); // 开发会话没开着，让界面打开它
  assert.match(require(path.join(main, 'inbox')).messagesFor('R-2')[0].text, /^【处理：测试集 TS-004/); // 排在收件箱里，打开时随开场白交过去
  assert.match(require(path.join(main, 'inbox')).messagesFor('R-2')[0].text, /项目目录里的安装和构建预先放行/);
  const chat = await E.aiHandle({ kind: 'prep', ref: 'ENV-001', step: 'P2' });
  assert.equal(chat.kind, 'chat');
  assert.ok(state.envFixChats.has(chat.sessionId));
  assert.equal(chat.title, handoff.title);
  assert.deepEqual(calls[calls.length - 1].args, ['env', 'handoff', 'prep', 'ENV-001', '--step', 'P2', '--json']);
  assert.deepEqual(await E.aiHandle({ kind: 'what', ref: 'x' }), { error: '不认识要处理什么。' });
  assert.deepEqual(await E.aiHandle({ kind: 'set', ref: '../x' }), { error: '要说处理哪个。' });
});
