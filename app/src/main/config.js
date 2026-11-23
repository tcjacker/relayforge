// 应用的设置和路径：环境变量在这里读一次，其他模块只从这里拿。
const fs = require('fs');
const os = require('os');
const path = require('path');

// 进展平时由会话自己边做边记。设成 1 可以恢复「停下来后另起一次模型调用来整理」的旧做法。
const AUTOSYNC = process.env.RELAYFORGE_AUTOSYNC === '1' || (process.env.RELAYFORGE_AGENT || 'claude').split(' ')[0] !== 'claude';
const IDLE_MS = Number(process.env.RELAYFORGE_IDLE_MS || 20000); // 会话停下来多久后自动整理
const AGENT = (process.env.RELAYFORGE_AGENT || 'claude').split(' ');
const PYTHON = process.env.RELAYFORGE_PYTHON || 'python3';
const YES = process.env.RELAYFORGE_ASSUME_YES === '1'; // 只给自动测试用：跳过确认框

// 目录：APP_DIR 是 app/（package.json 所在的地方）。
const APP_DIR = path.join(__dirname, '..', '..');
const PRELOAD = path.join(__dirname, '..', 'preload', 'index.js');
const INDEX_HTML = path.join(__dirname, '..', 'renderer', 'index.html');
// 打包以后应用的文件在 app.asar 里，python3 读不到；pty_bridge.py 被解到旁边的 app.asar.unpacked 里（见 electron-builder.yml）。
const PTY_BRIDGE = path.join(APP_DIR, 'resources', 'pty_bridge.py').replace(`app.asar${path.sep}`, `app.asar.unpacked${path.sep}`);
// 没有指定项目、也没有上次的项目时，先试试应用所在的这个仓库。
const HOME_REPO = path.join(APP_DIR, '..');

// 应用带着自己的 rf.py，这样旧版本初始化过的项目也能用上新功能。
// 开发时在仓库根目录的 backend/ 下；打包以后在应用资源目录的 backend/ 下。
const BUNDLED = [path.join(HOME_REPO, 'backend', 'rf.py'), process.resourcesPath && path.join(process.resourcesPath, 'backend', 'rf.py')]
  .find((file) => file && fs.existsSync(file)) || path.join(HOME_REPO, 'backend', 'rf.py');
// 写进钩子命令里的路径：带空格等字符时加引号。
const QUOTED = /^[\w./-]+$/.test(BUNDLED) ? BUNDLED : `'${BUNDLED.replace(/'/g, `'\\''`)}'`;

// ~/.relayforge 是这台电脑记项目登记和草稿的地方，不是一个项目。
const RF_HOME = process.env.RELAYFORGE_HOME || path.join(os.homedir(), '.relayforge');
const RF_HOME_REAL = (() => { try { return fs.realpathSync(RF_HOME); } catch (_) { return null; } })();

// 作为验收实例被启动时（见 app/README.md 的「作为验收实例启动」）：
//   RELAYFORGE_USER_DATA   用哪个数据目录（Electron 的 userData：上次打开的项目、初始化失败的记录都在这里）；不给就是系统默认的那个
//   RELAYFORGE_WINDOW_TAG  窗口标题前面加的一段标记（比如「验收用 · 需求 R-…」），顶栏上也显示
//   RELAYFORGE_HOME        本机登记的项目、保密值、草稿（上面的 RF_HOME）；RELAYFORGE_PROJECT 打开哪个项目（demo 是演示项目）
const USER_DATA = (process.env.RELAYFORGE_USER_DATA || '').trim() ? path.resolve(process.env.RELAYFORGE_USER_DATA.trim()) : null;
const WINDOW_TAG = String(process.env.RELAYFORGE_WINDOW_TAG || '').replace(/\s+/g, ' ').trim().slice(0, 80);
const windowTitle = (name) => (WINDOW_TAG ? WINDOW_TAG + ' · ' : '') + 'RelayForge · ' + name;

// 钩子判断好的会话状态一行一行追加到这个文件，应用只读它。
const EVENTS = path.join(os.tmpdir(), `relayforge-events-${process.pid}.jsonl`);

module.exports = {
  AUTOSYNC, IDLE_MS, AGENT, PYTHON, YES,
  APP_DIR, PRELOAD, INDEX_HTML, PTY_BRIDGE, HOME_REPO, BUNDLED, QUOTED, RF_HOME, RF_HOME_REAL, EVENTS, USER_DATA, WINDOW_TAG, windowTitle,
};
