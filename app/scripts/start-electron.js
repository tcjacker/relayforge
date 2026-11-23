// Electron 在运行中也会启动新的 Helper。macOS 的应用包需要独立于 npm ci 会清理的目录。
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');

function runtimeDirectory(source, cache, key) {
  const dist = path.join(cache, key);
  const bundle = 'RelayForge.app';
  const executable = path.join(bundle, 'Contents/MacOS/Electron');
  const renderer = path.join(bundle, 'Contents/Frameworks/Electron Helper (Renderer).app/Contents/MacOS/Electron Helper (Renderer)');
  const verify = (dir) => {
    for (const file of [executable, renderer]) fs.accessSync(path.join(dir, file), fs.constants.X_OK);
  };
  if (!fs.existsSync(dist)) {
    fs.mkdirSync(cache, { recursive: true });
    const staging = fs.mkdtempSync(path.join(cache, '.copy-'));
    try {
      fs.cpSync(path.join(source, bundle), path.join(staging, bundle), { recursive: true, verbatimSymlinks: true });
      verify(staging);
      try {
        fs.renameSync(staging, dist);
      } catch (error) {
        // 同一个工作目录同时启动时，另一个进程可能已经发布了完整的应用包。
        if (!['EEXIST', 'ENOTEMPTY'].includes(error.code)) throw error;
      }
    } finally {
      fs.rmSync(staging, { recursive: true, force: true });
    }
  }
  verify(dist);
  return dist;
}

if (require.main === module) {
  try {
    if (process.platform === 'darwin' && !process.env.ELECTRON_OVERRIDE_DIST_PATH) {
      const app = path.join(__dirname, '..');
      const electron = path.dirname(require.resolve('electron/package.json'));
      const { version } = require('electron/package.json');
      const fingerprint = createHash('sha256')
        .update(fs.readFileSync(path.join(app, 'resources', 'app-icon.png')))
        .update(fs.readFileSync(path.join(__dirname, 'brand-electron.js')))
        .digest('hex').slice(0, 16);
      process.env.ELECTRON_OVERRIDE_DIST_PATH = runtimeDirectory(
        path.join(electron, 'dist'), path.join(app, '.electron-runtime'), `${version}-${process.arch}-${fingerprint}`,
      );
    }
    process.argv.splice(2, 0, '.');
    require('electron/cli.js');
  } catch (error) {
    console.error(`[start-electron] 无法启动 Electron：${error.message}`);
    process.exitCode = 1;
  }
}

module.exports = { runtimeDirectory };
