const { execFile } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { promisify } = require('node:util');

const exec = promisify(execFile);
const root = path.resolve(__dirname, '..');

// 用现有离线演示项目启动真正的应用。初始问题、点击、读取和预期值由测试集数据提供。
async function run(input) {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'rf-home-ui-'));
  const project = path.join(temp, 'project');
  const env = {
    PATH: process.env.PATH,
    RELAYFORGE_HOME: path.join(temp, 'relayforge'),
    RELAYFORGE_PROJECT: project,
    RELAYFORGE_UI_PROFILE: path.join(temp, 'profile'),
  };
  try {
    await fs.mkdir(env.RELAYFORGE_UI_PROFILE);
    await fs.mkdir(env.RELAYFORGE_HOME);
    // 旧的演示验收数据使用中文版的文案。
    await fs.writeFile(path.join(env.RELAYFORGE_HOME, 'settings.json'), JSON.stringify({ language: input.language || 'zh-CN' }));
    await exec('python3', [
      path.join(root, 'test-project/build_demo.py'), '--target', project,
      '--backend', path.join(root, 'backend/rf.py'),
    ], { cwd: root, env, timeout: 30000 });
    for (const issue of input.issues || []) {
      const backend = path.join(root, 'backend/rf.py');
      const options = { cwd: project, env, timeout: 10000 };
      const feature = issue.feature ? ['--feature', issue.feature] : [];
      const { stdout } = await exec('python3', [backend, 'issue', 'new', issue.title, '--source', issue.source, ...feature, '--json'], options);
      await exec('python3', [backend, 'issue', 'set', JSON.parse(stdout).id, 'kind', issue.kind], options);
    }
    const { stdout } = await exec(require('../app/node_modules/electron'), [
      __filename, JSON.stringify(input.steps || input),
    ], { cwd: root, env, timeout: 30000, killSignal: 'SIGKILL' });
    return JSON.parse(stdout.trim());
  } finally {
    await fs.rm(temp, { recursive: true, force: true, maxRetries: 5 });
  }
}

function navigate(steps) {
  const { app } = require('electron');
  app.setPath('userData', process.env.RELAYFORGE_UI_PROFILE);
  app.once('browser-window-created', (_event, win) => {
    win.webContents.once('did-finish-load', async () => {
      try {
        const values = [];
        for (const step of steps) {
          const value = await interact(win.webContents, step);
          if (!step.click && step.select === undefined) values.push(value);
        }
        process.stdout.write(JSON.stringify(values) + '\n');
        app.quit();
      } catch (error) {
        process.stderr.write(error.stack + '\n');
        app.exit(1);
      }
    });
  });
  require('../app/src/main');
}

async function interact(contents, step) {
  const script = `(() => {
    const { selector, click, select, attribute, collect } = ${JSON.stringify(step)};
    const el = document.querySelector(selector);
    if (!el || !el.checkVisibility({ visibilityProperty: true })) return null;
    if ((click || select !== undefined) && el.matches(':disabled')) return null;
    if (click) { el.click(); return { clicked: true }; }
    if (select !== undefined) {
      el.value = select;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { selected: true };
    }
    const read = (node) => attribute ? node.getAttribute(attribute) : node.textContent.trim();
    return { value: collect ? Array.from(el.querySelectorAll(collect), read) : read(el) };
  })()`;
  const deadline = Date.now() + 6000;
  while (Date.now() < deadline) {
    const main = contents.mainFrame;
    const frame = step.map ? main.frames.find((child) => child.url.includes('?embed=map')) : main;
    const result = frame && await frame.executeJavaScript(script);
    if (result) return result.value;
    await delay(50);
  }
  throw new Error(`找不到可见元素：${step.selector}`);
}

if (process.type === 'browser' && process.env.RELAYFORGE_UI_PROFILE) navigate(JSON.parse(process.argv.at(-1)));
module.exports = { run };
