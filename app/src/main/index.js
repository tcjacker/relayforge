// RelayForge 主进程入口：左边是需求列表，中间是这个需求的 Claude Code，右边是它的进展。
// 一个需求有自己的分支和工作目录；终端通过进程内的管道连接，不开任何网络端口。
// 各部分见同目录下的模块：project（项目）、requirements（需求）、sessions（会话和终端）、attention（提醒）、
// designs（设计稿窗口）、watcher（定时检查）、ipc（和界面之间的通道）。
const { app, BrowserWindow } = require('electron');
const path = require('path');
const { INDEX_HTML, PRELOAD, USER_DATA, windowTitle } = require('./config');
const { state } = require('./state');
const project = require('./project');
const sessions = require('./sessions');
const attention = require('./attention');
const designs = require('./designs');
const watcher = require('./watcher');
const ipc = require('./ipc');
const appMenu = require('./app-menu');
const settings = require('./settings');
const { setLanguage } = require('./locale');

setLanguage(settings.language());

// 应用名和 Dock 图标：开发时用 npm start 跑的是 Electron 自带的 Electron.app，名字和图标默认是 Electron 的。
// prestart/postinstall 将开发应用包设为 RelayForge.app；Dock 与打包共用圆角图标。
app.setName('RelayForge');
// 验收实例用自己的数据目录（RELAYFORGE_USER_DATA）：要在 ready 之前、任何地方读 userData 之前设。上次打开的项目、窗口里的存储、
// Chromium 的配置都跟着它走，和正式实例互不影响
if (USER_DATA) { require('fs').mkdirSync(USER_DATA, { recursive: true }); app.setPath('userData', USER_DATA); }
const ICON = path.join(__dirname, '..', '..', 'resources', 'app-icon.png');

ipc.register();
watcher.start();

app.whenReady().then(async () => {
  require('./app-token').register();
  try { const fs = require('fs'); const { RF_HOME } = require('./config'); if (fs.statSync(RF_HOME).mode & 0o077) fs.chmodSync(RF_HOME, 0o700); } catch (_) { /* 还没建、改不了：不挡启动 */ } // 本机记录只给你自己
  try { require('./services').sweep(); } catch (_) { /* 清理不了遗留进程不挡启动 */ } // 上次没退干净留下的验收服务：整组停掉
  try { require('./session-procs').sweep(); } catch (_) { /* 同上 */ } // 上次应用崩溃时会话留下的进程
  if (process.platform === 'darwin' && app.dock && require('fs').existsSync(ICON)) app.dock.setIcon(ICON);
  appMenu.install(project.hasUsableProject, settings.language());
  state.project = await project.choose();
  if (!state.project && process.platform !== 'darwin') return app.quit();
  const win = state.win = new BrowserWindow({
    width: 1500, height: 900, title: state.project ? windowTitle(path.basename(state.project)) : 'RelayForge', backgroundColor: '#111318',
    webPreferences: { preload: PRELOAD, contextIsolation: true, nodeIntegration: false },
    // macOS：不要系统的灰色标题栏，红黄绿放进应用自己的深色顶栏（46px 高）里，上下居中。
    ...(process.platform === 'darwin' ? { titleBarStyle: 'hidden', trafficLightPosition: { x: 16, y: 16 } } : {}),
  });
  // 全屏时系统收起红黄绿，告诉界面把顶栏左边的留白去掉。
  const sendFull = (full) => { if (!win.isDestroyed()) win.webContents.send('window:fullscreen', full); };
  win.on('enter-full-screen', () => sendFull(true));
  win.on('leave-full-screen', () => sendFull(false));
  win.webContents.on('did-start-navigation', (_event, _url, inPage, mainFrame) => {
    if (mainFrame && !inPage) appMenu.setWindowReady(false);
  });
  win.webContents.on('did-finish-load', () => {
    sendFull(win.isFullScreen());
    appMenu.setWindowReady(true);
    try { require('./codex-trust').ensure(); } catch (error) { console.error('检查 Codex 钩子的信任失败', error); } // 每次启动补一次
  });
  win.on('closed', () => { state.win = null; appMenu.setWindowReady(false); });
  win.on('page-title-updated', (e) => e.preventDefault()); // 标题由这里定（带验收实例的标记），不让页面的 <title> 盖掉
  designs.guard(win.webContents);
  designs.noNetworkForDesigns();
  win.on('focus', () => { attention.onFocus(); try { require('./codex-trust').ensure(); } catch (_) { /* 查不了不影响窗口 */ } }); // 可能刚装好 Codex
  win.loadFile(INDEX_HTML, { query: { language: settings.language(), ...(state.project ? {} : { empty: '1' }) } });
});

app.on('window-all-closed', () => {
  require('./app-token').unregister();
  attention.cleanup();
  sessions.stopAll(true); // 退出应用：会话起的进程就地收拾
  require('./services').stopAll(); // 为验收启动的服务：整组停掉
  app.quit();
});
app.on('will-quit', () => { require('./app-token').unregister(); require('./services').stopAll(); require('./cli-auth').cancelAll(); });
