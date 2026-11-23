const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { runtimeDirectory } = require('../app/scripts/start-electron.js');

test('Electron can still launch a renderer after npm replaces its dependencies', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'relayforge-runtime-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, 'node_modules/electron/dist');
  const cache = path.join(root, '.electron-runtime');
  const bundle = 'RelayForge.app';
  const main = path.join(bundle, 'Contents/MacOS/Electron');
  const renderer = path.join(bundle, 'Contents/Frameworks/Electron Helper (Renderer).app/Contents/MacOS/Electron Helper (Renderer)');
  for (const file of [main, renderer]) {
    fs.mkdirSync(path.join(source, path.dirname(file)), { recursive: true });
    fs.writeFileSync(path.join(source, file), file, { mode: 0o755 });
  }
  const framework = path.join(bundle, 'Contents/Frameworks/Current');
  fs.symlinkSync('Electron Helper (Renderer).app', path.join(source, framework));

  const dist = runtimeDirectory(source, cache, 'v1');
  fs.rmSync(path.join(root, 'node_modules'), { recursive: true });
  assert.equal(runtimeDirectory(source, cache, 'v1'), dist);
  fs.accessSync(path.join(dist, renderer), fs.constants.X_OK);
  assert.equal(fs.readlinkSync(path.join(dist, framework)), 'Electron Helper (Renderer).app');
  assert.throws(() => runtimeDirectory(source, cache, 'v2'), /ENOENT/);
  assert.deepEqual(fs.readdirSync(cache), ['v1']);
});

test('Mac branding survives repeated starts and a fresh Electron installation', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'relayforge-brand-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const repo = path.join(__dirname, '..');
  for (const name of ['icon/icon.png', 'app/resources/app-icon.png',
    'app/resources/app-icon.png.sha256', 'app/scripts/render-icon.js']) {
    const target = path.join(root, name);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(path.join(repo, name), target);
  }
  const electron = path.join(root, 'app/node_modules/electron');
  const fresh = () => {
    const contents = path.join(electron, 'dist/Electron.app/Contents');
    fs.mkdirSync(path.join(contents, 'Resources'), { recursive: true });
    fs.mkdirSync(path.join(contents, 'MacOS'), { recursive: true });
    fs.writeFileSync(path.join(contents, 'MacOS/Electron'), 'fresh-runtime');
    fs.writeFileSync(path.join(contents, 'Resources/electron.icns'), 'default-icon');
  };
  const calls = [];
  const execFileSync = (command, args) => {
    calls.push([command, args]);
    if (command === 'iconutil') fs.writeFileSync(args[args.indexOf('-o') + 1], 'rounded-icon');
    if (command === '/usr/bin/osascript') throw new Error('Committed icon should not need regeneration');
  };
  const script = fs.readFileSync(path.join(repo, 'app/scripts/brand-electron.js'), 'utf8');
  const start = () => {
    const process = { platform: 'darwin', exitCode: 0 };
    vm.runInNewContext(script, {
      __dirname: path.join(root, 'app/scripts'), process, console: { log() {}, error() {} },
      require: (name) => name === 'child_process' ? { execFileSync } : require(name),
    });
    assert.equal(process.exitCode, 0);
  };
  fresh();
  start();
  const runtime = fs.readFileSync(path.join(electron, 'path.txt'), 'utf8');
  assert.equal(runtime, 'RelayForge.app/Contents/MacOS/Electron');
  assert.equal(fs.readFileSync(path.join(electron, 'dist', runtime), 'utf8'), 'fresh-runtime');
  assert.ok(calls.some(([cmd, args]) => cmd === 'plutil' && args.includes('dev.relayforge.app.dev')));
  assert.equal(calls.filter(([cmd]) => cmd === 'iconutil').length, 1);
  start();
  assert.equal(calls.filter(([cmd]) => cmd === 'iconutil').length, 1);
  fs.writeFileSync(path.join(electron, 'dist', runtime), 'old-runtime');
  fresh();
  start();
  assert.equal(fs.readFileSync(path.join(electron, 'dist', runtime), 'utf8'), 'fresh-runtime');
  assert.equal(calls.filter(([cmd]) => cmd === 'iconutil').length, 2);
});
