// Codex 钩子的信任（app/src/main/codex-trust.js、codex-hooks.js 的 trustEntries、renderer/scripts/codex-trust-model.js）：
// 键和指纹要和 Codex 自己算的一样（下面的指纹是真实 Codex 信任过以后写进 ~/.codex/config.toml 的）；命令不随安装位置变；
// 缺信任时应用自动经 Codex 的 app-server 只写缺的那几个，不弹窗。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const main = path.join(__dirname, '..', 'app', 'src', 'main');
const plain = (x) => JSON.parse(JSON.stringify(x));

function load(name, mocks) {
  const ctx = vm.createContext({ require: (id) => (Object.hasOwn(mocks, id) ? mocks[id] : require(id)), module: { exports: {} }, process, console, setTimeout, clearTimeout });
  vm.runInContext(fs.readFileSync(path.join(main, name), 'utf8'), ctx);
  return ctx.module.exports;
}
const hooksWith = (rf) => load('codex-hooks.js', { './config': { PYTHON: 'python3', QUOTED: rf, BUNDLED: rf } });

test('keys and fingerprints are the ones Codex itself stores for our session-flag hooks', () => {
  // 下面的指纹是真实 Codex 对这条命令记下的，路径是当时的本机路径；改路径指纹就对不上，所以保留原样。
  const entries = hooksWith('/x/rf.py').trustEntries('darwin', 'python3 /Users/tc/ai/relayforge/backend/rf.py hook codex');
  const by = Object.fromEntries(entries.map((e) => [e.name, e]));
  // 真实 Codex 0.171 信任这些钩子后在 config.toml 里记下的
  assert.equal(by.Interrupt.key, '/<session-flags>/config.toml:interrupt:0:0');
  assert.equal(by.Interrupt.hash, 'sha256:737e9eaaf61a3a5287cebec8cad9c1dc2c2dd48397585ed44420a8b577695fe7');
  assert.equal(by.Stop.hash, 'sha256:87491dd9b1232b019aa9a60dcceb6a5c8c82482052edc4ac9eabaa52ca859d6a'); // 不看 matcher 的事件
  assert.equal(by.PreToolUse.hash, 'sha256:af6c92b431afc9aeae45e9af1c95bca40184e6e785ca348d7d015c7777abc0df');
  assert.equal(by.PostToolUse.hash, 'sha256:6f9c26a475768abbdaeca9735d0bb58fd9b3340557777ba5b809e711c4173c19'); // async=true
  assert.equal(by.UserPromptSubmit.hash, 'sha256:1f106ca1b2cd4a9fbccbfe0a2789fe36f2877a98734797fccbe159828c10d9f3');
  assert.equal(by.PermissionRequest.key, '/<session-flags>/config.toml:permission_request:0:0');
  // 主应用、测试界面、各工作目录共用同一份信任：路径不进命令，指纹不变，路径由会话的环境变量带
  const here = hooksWith('/a/backend/rf.py'), there = hooksWith('/Applications/Relay Forge/backend/rf.py');
  assert.deepEqual(plain(here.trustEntries('darwin')), plain(there.trustEntries('darwin')));
  assert.equal(there.env({}).RELAYFORGE_HOOK_RF, '/Applications/Relay Forge/backend/rf.py');
  assert.equal(there.env({}).RELAYFORGE_HOOK_PYTHON, 'python3');
  // 这条指纹是真实 Codex 0.171 用固定命令跑通 UserPromptSubmit 钩子时认可的
  assert.equal(here.trustEntries('darwin').find((e) => e.name === 'UserPromptSubmit').command, '$RELAYFORGE_HOOK_PYTHON "$RELAYFORGE_HOOK_RF" hook codex');
  assert.match(hooksWith('/x/rf.py').trustEntries('win32')[0].key, /^C:\\<session-flags>\\config\.toml:/);
});

function harness(t, { installed = true, config = null, answers = {} } = {}) {
  const home = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-codex-trust-')));
  t.after(() => { fs.rmSync(home, { recursive: true, force: true }); delete process.env.CODEX_HOME; });
  process.env.CODEX_HOME = home;
  if (config != null) fs.writeFileSync(path.join(home, 'config.toml'), config);
  const spawned = [];
  const hooks = hooksWith('/rf/rf.py');
  const mocks = {
    './backend': { cleanEnv: () => ({}) },
    './settings': { launch: () => ({ file: '/bin/codex', target: installed ? '/bin/codex' : null, args: [] }) },
    './codex-hooks': hooks,
    './proc-tree': { killTree() {} },
    '../../package.json': { version: '0.0.0' },
    child_process: { spawn: (file, args) => {
      const c = new EventEmitter();
      c.stdout = new EventEmitter(); c.stderr = new EventEmitter(); c.pid = 7; c.kill = () => {};
      c.stdin = { on() {}, destroy() {}, write: (line) => {
        const m = JSON.parse(line);
        spawned.push(m);
        if (m.id === 1) setImmediate(() => c.stdout.emit('data', JSON.stringify({ id: 1, result: {} }) + '\n'));
        if (m.id === 2) setImmediate(() => { if (answers.write) answers.write(m); c.stdout.emit('data', JSON.stringify(answers.error ? { id: 2, error: { message: answers.error } } : { id: 2, result: { status: 'ok' } }) + '\n'); });
      } };
      spawned.args = [file, ...args];
      return c;
    } },
  };
  return { trust: load('codex-trust.js', mocks), hooks, home, spawned };
}

const section = (key, hash) => `[hooks.state."${key}"]\ntrusted_hash = "${hash}"\n\n`;

test('without Codex there is nothing to check; with Codex every hook is untrusted, modified or trusted', (t) => {
  assert.equal(harness(t, { installed: false }).trust.status().trusted, true);
  const h = harness(t);
  const [first, second] = h.hooks.trustEntries();
  fs.writeFileSync(path.join(h.home, 'config.toml'), '[features]\nx = 1\n\n' + section(first.key, first.hash) + section(second.key, 'sha256:old'));
  const s = h.trust.status();
  assert.equal(s.installed, true);
  assert.equal(s.trusted, false);
  assert.deepEqual(plain(s.hooks.slice(0, 3).map((x) => x.state)), ['trusted', 'modified', 'untrusted']);
});

test('the stored hash is found in both the section form and the inline form', () => {
  const { storedHash } = require(path.join(main, 'codex-trust.js'));
  const key = '/<session-flags>/config.toml:stop:0:0';
  assert.equal(storedHash(`[hooks.state."${key}"]\ntrusted_hash = "sha256:a"\n[other]\ntrusted_hash = "sha256:b"\n`, key), 'sha256:a');
  assert.equal(storedHash(`[hooks.state]\n"${key}" = { trusted_hash = "sha256:c" }\n`, key), 'sha256:c');
  assert.equal(storedHash(`[hooks.state."${key}"]\nenabled = true\n[next]\ntrusted_hash = "sha256:d"\n`, key), null); // 别的表里的不算
});

test('missing trust is filled in automatically, only the missing fingerprints, through Codex app-server', async (t) => {
  const h = harness(t, { answers: { write: (m) => {
    // 模拟 Codex 把它们写进 config.toml
    const text = m.params.edits.map((e) => section(e.keyPath.match(/^hooks\.state\."(.*)"\.trusted_hash$/)[1], e.value)).join('');
    fs.appendFileSync(path.join(h.home, 'config.toml'), text);
  } } });
  const entries = h.hooks.trustEntries();
  fs.writeFileSync(path.join(h.home, 'config.toml'), section(entries[0].key, entries[0].hash));
  const out = await h.trust.ensure();
  assert.equal(out.trusted, true);
  assert.deepEqual(plain(h.spawned.args), ['/bin/codex', 'app-server']);
  assert.deepEqual(plain(h.spawned.map((m) => m.method)), ['initialize', 'initialized', 'config/batchWrite']);
  const write = h.spawned[2].params;
  assert.equal(write.reloadUserConfig, true);
  assert.equal(write.edits.length, entries.length - 1); // 已经信任的那个不再写
  assert.deepEqual(plain(write.edits[0]), { keyPath: `hooks.state."${entries[1].key}".trusted_hash`, value: entries[1].hash, mergeStrategy: 'replace' });
  const count = h.spawned.length;
  await h.trust.ensure();
  assert.equal(h.spawned.length, count); // 已经信任就不再启动 Codex
});

test('a refusal from Codex is not claimed trusted and is not retried on every focus', async (t) => {
  const h = harness(t, { config: '', answers: { error: 'config is read-only' } });
  const error = console.error; console.error = () => {};
  t.after(() => { console.error = error; });
  const out = await h.trust.ensure();
  assert.match(out.error, /Codex 拒绝了：config is read-only/);
  assert.equal(h.trust.status().trusted, false);
  const count = h.spawned.length;
  await h.trust.ensure();
  assert.equal(h.spawned.length, count);
});

test('no trust dialog is left in the app', () => {
  const app = path.join(__dirname, '..', 'app', 'src');
  for (const file of ['renderer/index.html', 'preload/index.js', 'main/ipc.js']) {
    assert.doesNotMatch(fs.readFileSync(path.join(app, file), 'utf8'), /codex-trust"|codex:trust|trustCodexHooks/);
  }
});
