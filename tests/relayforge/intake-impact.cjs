const { execFile } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { promisify } = require('node:util');

const exec = promisify(execFile);
const root = path.resolve(__dirname, '../..');
const renderer = path.join(root, 'app/src/renderer');

async function run(input) {
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'rf-intake-impact-'));
  try {
    const { stdout } = await exec(require('../../app/node_modules/electron'), [
      __filename, profile, JSON.stringify(input),
    ], { env: { PATH: process.env.PATH }, timeout: 15000, killSignal: 'SIGKILL' });
    return JSON.parse(stdout);
  } finally {
    await fs.rm(profile, { recursive: true, force: true });
  }
}

async function observe(profile, steps) {
  const { app, BrowserWindow } = require('electron');
  app.setPath('userData', profile);
  await app.whenReady();
  const win = new BrowserWindow({ show: false });
  await win.loadURL('data:text/html,<div id="impact-body"></div>');
  for (const script of ['core.js', 'intake.js']) {
    await win.webContents.executeJavaScript(await fs.readFile(path.join(renderer, 'scripts', script), 'utf8'));
  }
  await win.webContents.insertCSS(await fs.readFile(path.join(renderer, 'styles/app.css'), 'utf8'));
  return win.webContents.executeJavaScript(`(() => {
    const observations = [];
    for (const step of ${JSON.stringify(steps)}) {
      if (step.render) impact(step.render);
      if (step.click) document.querySelector(step.click).click();
      if (step.read) {
        observations.push(step.read.map(selector => Array.from(document.querySelectorAll(selector))
          .filter(node => node.checkVisibility({ visibilityProperty: true }))
          .map(node => node.textContent)));
      }
    }
    return observations;
  })()`);
}

if (process.type === 'browser') {
  const [profile, input] = process.argv.slice(-2);
  observe(profile, JSON.parse(input)).then(
    result => process.stdout.write(JSON.stringify(result), () => require('electron').app.exit(0)),
    error => process.stderr.write(error.stack, () => require('electron').app.exit(1)),
  );
}

module.exports = { run };
