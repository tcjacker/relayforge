const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const file = path.join(__dirname, '..', 'app', 'src', 'main', 'demo.js');

function targetFor(homeRepo, rfHome, resourcesPath) {
  const mocks = { './backend': { exec: async () => ({ ok: true }) }, './config': { HOME_REPO: homeRepo, BUNDLED: 'rf.py', PYTHON: 'python3', RF_HOME: rfHome } };
  const ctx = vm.createContext({ require: (id) => (Object.hasOwn(mocks, id) ? mocks[id] : require(id)), module: { exports: {} }, process: { resourcesPath } });
  vm.runInContext(fs.readFileSync(file, 'utf8'), ctx);
  return ctx.module.exports.target;
}

function withTemplate(run) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-demo-path-')));
  try {
    const resources = path.join(root, 'RelayForge.app', 'Contents', 'Resources');
    fs.mkdirSync(path.join(resources, 'test-project'), { recursive: true });
    fs.writeFileSync(path.join(resources, 'test-project', 'build_demo.py'), '');
    run(root, resources);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test('packaged app: the demo project is generated in RF_HOME, never inside the signed app bundle', () => {
  withTemplate((root, resources) => {
    const home = path.join(root, 'home');
    assert.equal(targetFor(resources, home, resources), path.join(home, 'test-project'));
  });
});

test('running from the repository: the demo project stays in test-project/.runtime', () => {
  withTemplate((root, resources) => {
    const runtime = path.join(root, 'electron', 'Resources');
    assert.equal(targetFor(resources, path.join(root, 'home'), runtime), path.join(resources, 'test-project', '.runtime', 'flownote'));
  });
});
