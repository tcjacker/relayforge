// 设置窗口的 CLI 登录状态和登录（R-20261121-1f0c）：主进程怎么查状态、登录、取消、超时（cli-auth.js），
// 每行显示什么（cli-auth-model.js），设置窗口有没有接上。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { EventEmitter } = require('node:events');
const root = path.join(__dirname, '..', 'app', 'src');
const Model = require(path.join(root, 'renderer', 'scripts', 'cli-auth-model'));

// installs：{ claude: { installed, desktop, status: {code, stdout, stderr, timedOut} }, codex: … }
function harness(installs) {
  const spawned = [], timers = [], statusCalls = [];
  const file = path.join(root, 'main', 'cli-auth.js');
  const localRequire = createRequire(file);
  const mocks = {
    './backend': { cleanEnv: () => ({}) },
    './settings': {
      launch: (agent) => ({
        file: '/bin/' + agent, target: installs[agent].installed ? '/real/' + agent : null, desktop: installs[agent].desktop || null,
      }),
    },
    'node:child_process': {
      execFile: (cmd, args, _options, done) => {
        const agent = path.basename(cmd);
        statusCalls.push({ agent, args });
        const s = installs[agent].status;
        if (s.code === 0) return done(null, s.stdout || '', s.stderr || '');
        const error = Object.assign(new Error('failed'), { code: s.code, killed: !!s.timedOut });
        done(error, s.stdout || '', s.stderr || '');
      },
      spawn: (cmd, args) => {
        const child = new EventEmitter();
        child.stdout = new EventEmitter();
        child.stderr = new EventEmitter();
        child.killed = [];
        child.kill = (signal) => { child.killed.push(signal); setImmediate(() => child.emit('close', null)); };
        spawned.push({ cmd, args, child });
        return child;
      },
    },
  };
  const context = vm.createContext({
    require: (id) => Object.hasOwn(mocks, id) ? mocks[id] : localRequire(id), module: { exports: {} }, process,
    setTimeout: (fn, ms) => { const t = { fn, ms }; timers.push(t); return t; }, clearTimeout: (t) => { if (t) t.cleared = true; },
  });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
  return { auth: context.module.exports, spawned, timers, statusCalls };
}
const claudeIn = (email) => ({ code: 0, stdout: JSON.stringify({ loggedIn: true, authMethod: 'claude.ai', email, subscriptionType: 'max' }) });
const claudeOut = { code: 1, stdout: JSON.stringify({ loggedIn: false }) };
const codexIn = { code: 0, stderr: 'Logged in using ChatGPT\n' };
const codexOut = { code: 1, stderr: 'Not logged in\n' };
const tick = () => new Promise((resolve) => setImmediate(resolve));
// 打开设置后一行最终显示的样子：按界面同样的路径（fromStatus → view）
async function shown(h, agent) {
  const result = await h.auth.status(agent).catch((e) => ({ error: e.message }));
  return Model.view(Model.fromStatus(result), agent);
}

test('打开设置前每行先显示「检查中…」', () => {
  const v = Model.view({ state: 'checking' });
  assert.equal(v.tag.text, '检查中…');
  assert.equal(v.button, null);
});

test('用例 1：Claude 已登录显示账号，Codex 没登录显示「登录」', async () => {
  const h = harness({ claude: { installed: true, status: claudeIn('a@example.com') }, codex: { installed: true, status: codexOut } });
  const claude = await shown(h, 'claude'), codex = await shown(h, 'codex');
  assert.equal(claude.tag.text, '已登录');
  assert.equal(claude.button, null);
  assert.match(claude.sub.text, /a@example\.com/);
  assert.equal(codex.button.text, '登录');
  assert.equal(codex.tag, null);
  assert.deepEqual(h.statusCalls.map((c) => [c.agent, ...c.args]), [['claude', 'auth', 'status', '--json'], ['codex', 'login', 'status']]);
});

test('用例 2：两个都没登录，两行都显示「登录」', async () => {
  const h = harness({ claude: { installed: true, status: claudeOut }, codex: { installed: true, status: codexOut } });
  for (const agent of ['claude', 'codex']) assert.equal((await shown(h, agent)).button.text, '登录');
});

test('用例 3：没装 Codex 显示「未安装」、没有登录按钮，也不去运行它', async () => {
  const h = harness({ claude: { installed: true, status: claudeIn('a@example.com') }, codex: { installed: false } });
  const codex = await shown(h, 'codex');
  assert.equal(codex.tag.text, '未安装');
  assert.equal(codex.button, null);
  assert.ok(!h.statusCalls.some((c) => c.agent === 'codex'));
  await assert.rejects(async () => h.auth.login('codex'), /没有安装/);
});

test('用例 4：桌面应用提供的 Claude Code 不显示登录按钮，提示在桌面应用里登录', async () => {
  const h = harness({ claude: { installed: true, desktop: 'Claude', status: claudeOut }, codex: { installed: true, status: codexIn } });
  const claude = await shown(h, 'claude');
  assert.equal(claude.button, null);
  assert.match(claude.tag.text, /在桌面应用里登录/);
  assert.match(claude.sub.text, /Claude 桌面应用/);
  await assert.rejects(async () => h.auth.login('claude'), /桌面应用里登录/);
  assert.equal(h.spawned.length, 0);
});

test('用例 5：查登录状态出错或超时，不停在检查中，显示「登录」', async () => {
  for (const status of [{ code: 2, stderr: 'boom\n' }, { code: null, timedOut: true }, { code: 0, stdout: '奇怪的输出' }]) {
    const h = harness({ claude: { installed: true, status: claudeIn('x') }, codex: { installed: true, status } });
    const codex = await shown(h, 'codex');
    assert.equal(codex.button.text, '登录');
    assert.notEqual(codex.tag?.text, '检查中…');
  }
  // 界面那头的调用本身失败也一样
  assert.equal(Model.view(Model.fromStatus({ error: '通道断了' })).button.text, '登录');
});

test('Codex 已登录显示 ChatGPT 账号；Claude 用 API Key 登录也算已登录', () => {
  assert.deepEqual(Model.view(Model.fromStatus(harness({ claude: {}, codex: {} }).auth.readStatus('codex', codexIn))).sub.text, 'ChatGPT 账号');
  const { auth } = harness({ claude: {}, codex: {} });
  assert.equal(auth.readStatus('claude', { code: 0, stdout: '{"loggedIn":true,"authMethod":"apiKey"}' }).account, 'API Key');
});

test('点登录：运行 CLI 自己的登录命令，等待时显示「等待浏览器授权…」和「取消」，授权后变成已登录', async () => {
  const installs = { claude: { installed: true, status: claudeOut }, codex: { installed: true, status: codexOut } };
  const h = harness(installs);
  const waiting = Model.view({ state: 'waiting' });
  assert.equal(waiting.tag.text, '等待浏览器授权…');
  assert.equal(waiting.tag.kind, 'wait');
  assert.equal(waiting.button.text, '取消');
  const done = h.auth.login('claude');
  assert.deepEqual(h.spawned.map((s) => [s.cmd, ...s.args]), [['/bin/claude', 'auth', 'login']]);
  assert.equal((await h.auth.status('claude')).state, 'waiting'); // 登录过程中再查不会另起一个状态命令
  assert.equal(h.auth.login('claude'), done); // 重复点不会再开一个登录进程
  installs.claude.status = claudeIn('new@example.com');
  h.spawned[0].child.emit('close', 0);
  const v = Model.view(Model.fromLogin(await done));
  assert.equal(v.tag.text, '已登录');
  assert.match(v.sub.text, /new@example\.com/);
});

test('等待授权时取消：停掉登录进程，这一行回到「登录」', async () => {
  const h = harness({ claude: { installed: true, status: claudeOut }, codex: { installed: true, status: codexOut } });
  const done = h.auth.login('codex');
  h.auth.cancel('codex');
  assert.deepEqual(h.spawned[0].child.killed, ['SIGTERM']);
  const result = await done;
  assert.equal(result.state, 'cancelled');
  assert.equal(Model.view(Model.fromLogin(result)).button.text, '登录');
  assert.equal((await h.auth.status('codex')).state, 'out'); // 再打开设置仍是没登录
});

test('取消后马上重新登录：等旧进程退出再开新的，不会拿到旧的「已取消」', async () => {
  const installs = { claude: { installed: true, status: claudeOut }, codex: { installed: true, status: codexOut } };
  const h = harness(installs);
  h.auth.login('codex');
  h.auth.cancel('codex');
  const again = h.auth.login('codex');
  await tick(); await tick();
  assert.equal(h.spawned.length, 2);
  installs.codex.status = codexIn;
  h.spawned[1].child.emit('close', 0);
  assert.equal((await again).state, 'in');
});

test('用例 6：登录命令失败，显示红色原因和「重新登录」', async () => {
  const h = harness({ claude: { installed: true, status: claudeOut }, codex: { installed: true, status: codexOut } });
  const done = h.auth.login('codex');
  h.spawned[0].child.stderr.emit('data', 'Error: login was denied\n');
  h.spawned[0].child.emit('close', 1);
  const v = Model.view(Model.fromLogin(await done));
  assert.equal(v.button.text, '重新登录');
  assert.equal(v.sub.error, true);
  assert.match(v.sub.text, /login was denied/);
  assert.equal(v.tag, null);
});

test('用例 6：登录命令说成功但其实仍没登录，也按失败显示', async () => {
  const h = harness({ claude: { installed: true, status: claudeOut }, codex: { installed: true, status: codexOut } });
  const done = h.auth.login('codex');
  h.spawned[0].child.emit('close', 0);
  const v = Model.view(Model.fromLogin(await done));
  assert.equal(v.button.text, '重新登录');
  assert.equal(v.sub.error, true);
});

test('用例 7：等待授权超时，停掉登录进程，显示红色「授权超时」和「重新登录」', async () => {
  const h = harness({ claude: { installed: true, status: claudeOut }, codex: { installed: true, status: codexOut } });
  const done = h.auth.login('codex');
  const timer = h.timers.find((t) => t.ms === h.auth.LOGIN_TIMEOUT);
  assert.ok(timer);
  timer.fn();
  assert.deepEqual(h.spawned[0].child.killed, ['SIGTERM']);
  const v = Model.view(Model.fromLogin(await done));
  assert.match(v.sub.text, /授权超时/);
  assert.equal(v.sub.error, true);
  assert.equal(v.button.text, '重新登录');
  assert.notEqual(v.tag?.text, '等待浏览器授权…');
});

test('登录通道本身出错也显示原因和「重新登录」', () => {
  const v = Model.view(Model.fromLogin({ error: '找不到 codex' }));
  assert.equal(v.button.text, '重新登录');
  assert.match(v.sub.text, /找不到 codex/);
});

test('设置窗口接上了：每行有状态位置，通道两头都登记，退出应用时停掉登录', () => {
  const html = fs.readFileSync(path.join(root, 'renderer', 'index.html'), 'utf8');
  for (const agent of ['claude', 'codex']) assert.match(html, new RegExp('data-agent="' + agent + '"[^]*?data-auth-sub[^]*?data-auth'));
  assert.ok(html.indexOf('scripts/cli-auth-model.js') < html.indexOf('scripts/settings.js'));
  const ipc = fs.readFileSync(path.join(root, 'main', 'ipc.js'), 'utf8');
  const preload = fs.readFileSync(path.join(root, 'preload', 'index.js'), 'utf8');
  for (const channel of ['cli:auth-status', 'cli:login', 'cli:login-cancel']) {
    assert.ok(ipc.includes("'" + channel + "'"), channel);
    assert.ok(preload.includes("'" + channel + "'"), channel);
  }
  const settings = fs.readFileSync(path.join(root, 'renderer', 'scripts', 'settings.js'), 'utf8');
  assert.match(settings, /cliAuthStatus/);
  assert.match(settings, /loginCli/);
  assert.match(settings, /cancelCliLogin/);
  assert.match(fs.readFileSync(path.join(root, 'main', 'index.js'), 'utf8'), /cli-auth'\)\.cancelAll\(\)/);
});
