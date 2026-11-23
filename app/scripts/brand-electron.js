// npm install 和 npm start 前自动运行：为开发应用包设置 RelayForge 的名字和圆角图标。
// 用 npm start 开发时，Dock 悬停显示的名字和启动瞬间的图标来自 node_modules 里 Electron.app 的 Info.plist 和 electron.icns，
// 使用独立的包名和 bundle ID，避免 Dock 沿用 Electron 的名称缓存。只在 macOS 上做。
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { createHash } = require('crypto');

const NAME = 'RelayForge';
const APP_DIR = path.join(__dirname, '..');
const ICON_PNG = path.join(APP_DIR, '..', 'icon', 'icon.png');
const DOCK_PNG = path.join(APP_DIR, 'resources', 'app-icon.png');
const ELECTRON_DIR = path.join(APP_DIR, 'node_modules', 'electron');
const BUNDLE = path.join(ELECTRON_DIR, 'dist', `${NAME}.app`);

function run(cmd, args) {
  execFileSync(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'] });
}

// 用 icon.png 生成 .icns：sips 缩出各个尺寸，iconutil 打包。
function makeIcns(png, out) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'relayforge-icon-'));
  const set = path.join(tmp, 'icon.iconset');
  fs.mkdirSync(set);
  try {
    for (const size of [16, 32, 128, 256, 512]) {
      run('sips', ['-z', String(size), String(size), png, '--out', path.join(set, `icon_${size}x${size}.png`)]);
      run('sips', ['-z', String(size * 2), String(size * 2), png, '--out', path.join(set, `icon_${size}x${size}@2x.png`)]);
    }
    run('iconutil', ['-c', 'icns', set, '-o', out]);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

function main() {
  if (process.platform !== 'darwin') return;
  // The generated PNG is committed so launching or packaging does not need regeneration.
  // Regenerate it whenever the source artwork or rendering script changes.
  const render = path.join(__dirname, 'render-icon.js');
  const fingerprint = createHash('sha256').update(fs.readFileSync(ICON_PNG))
    .update(fs.readFileSync(render)).digest('hex');
  const stamp = `${DOCK_PNG}.sha256`;
  const changed = !fs.existsSync(DOCK_PNG) || !fs.existsSync(stamp) ||
    fs.readFileSync(stamp, 'utf8').trim() !== fingerprint;
  if (changed) {
    run('/usr/bin/osascript', ['-l', 'JavaScript', render, ICON_PNG, DOCK_PNG]);
    fs.writeFileSync(stamp, `${fingerprint}\n`);
  }
  const original = path.join(ELECTRON_DIR, 'dist', 'Electron.app');
  if (fs.existsSync(original)) {
    // A fresh Electron install must replace the previous branded runtime as well.
    if (fs.existsSync(BUNDLE)) fs.rmSync(BUNDLE, { recursive: true });
    fs.renameSync(original, BUNDLE);
  }
  if (!fs.existsSync(BUNDLE)) throw new Error(`没找到 ${BUNDLE}`);
  // Electron's CLI resolves its executable through path.txt, including after npm install.
  fs.writeFileSync(path.join(ELECTRON_DIR, 'path.txt'), `${NAME}.app/Contents/MacOS/Electron`);
  const plist = path.join(BUNDLE, 'Contents', 'Info.plist');
  for (const [key, value] of Object.entries({ CFBundleName: NAME, CFBundleDisplayName: NAME,
    CFBundleIdentifier: 'dev.relayforge.app.dev' })) {
    try { run('plutil', ['-replace', key, '-string', value, plist]); } catch { run('plutil', ['-insert', key, '-string', value, plist]); }
  }
  const icns = path.join(BUNDLE, 'Contents', 'Resources', 'electron.icns');
  const iconStamp = path.join(BUNDLE, 'Contents', 'Resources', '.relayforge-icon.sha256');
  if (!fs.existsSync(icns) || !fs.existsSync(iconStamp) ||
      fs.readFileSync(iconStamp, 'utf8').trim() !== fingerprint) {
    makeIcns(DOCK_PNG, icns);
    fs.writeFileSync(iconStamp, `${fingerprint}\n`);
  }
  // 让 Dock 和访达重新读这个包，不用旧的缓存。
  try { run('touch', [BUNDLE]); } catch {}
  try {
    run('/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister', ['-f', BUNDLE]);
  } catch {}
  console.log(`[brand-electron] ${NAME}.app 的名字和圆角图标已就绪`);
}

try { main(); } catch (err) {
  console.error(`[brand-electron] 没改成：${err.message}`);
  process.exitCode = 1;
}
