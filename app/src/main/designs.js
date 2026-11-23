// 设计稿：AI 画的 HTML 页面，只在隔离的地方显示，碰不到应用本身
// 窗口里的预览是 sandbox="allow-scripts" 的 iframe（没有 allow-same-origin，拿不到应用的任何东西）；
// 单独打开时是一个没有预加载脚本、开了沙箱的窗口。两处都不许它联网、不许它跳到别的地址。
const { BrowserWindow, session } = require('electron');
const fs = require('fs');
const path = require('path');

const DESIGN_FILE = /[\\/]\.relayforge[\\/](designs|history)[\\/]DS-[\w-]+\.html$/;
const designWindows = new Map(); // 文件 -> { win, mtime }
const mtimeOf = (file) => { try { return fs.statSync(file).mtimeMs; } catch (_) { return 0; } };
function isDesignUrl(url) {
  try { const u = new URL(url); return u.protocol === 'file:' && DESIGN_FILE.test(decodeURIComponent(u.pathname)); } catch (_) { return false; }
}
function openDesign(file, title) {
  if (!DESIGN_FILE.test(file) || !fs.existsSync(file)) return;
  const known = designWindows.get(file);
  if (known && !known.win.isDestroyed()) { known.win.show(); known.win.focus(); return; }
  const shown = new BrowserWindow({
    width: 1280, height: 860, title: '设计稿 · ' + (title || path.basename(file)), backgroundColor: '#ffffff', autoHideMenuBar: true,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, nodeIntegrationInSubFrames: false, webviewTag: false, navigateOnDragDrop: false, spellcheck: false },
  });
  shown.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  shown.webContents.on('will-navigate', (e) => e.preventDefault());
  shown.webContents.on('will-frame-navigate', (e) => { if (!e.isMainFrame) e.preventDefault(); });
  shown.on('page-title-updated', (e) => e.preventDefault());
  shown.loadFile(file);
  designWindows.set(file, { win: shown, mtime: mtimeOf(file) });
  shown.on('closed', () => designWindows.delete(file));
}
function guard(contents) {
  // 大图里点「单独打开」：交给上面那个隔离的窗口；其他新窗口一律不开（新窗口会继承应用的预加载脚本）
  contents.setWindowOpenHandler(({ url }) => { if (isDesignUrl(url)) openDesign(decodeURIComponent(new URL(url).pathname), ''); return { action: 'deny' }; });
  // 主窗口只许停在应用自己的 index.html 上（重新载入、带查询参数可以）；以前放行任何 file: 地址，跳到别的本地页面也能拿到 window.shell
  const own = require('url').pathToFileURL(require('./config').INDEX_HTML).href;
  contents.on('will-navigate', (e) => { if (String(e.url).split(/[?#]/)[0] !== own) e.preventDefault(); });
  contents.on('will-frame-navigate', (e) => { if (!e.isMainFrame && !/^(file:|about:)/.test(String(e.url))) e.preventDefault(); });
}
function noNetworkForDesigns() {
  const fromDesign = (d) => {
    let from = '';
    try { from = (d.frame && d.frame.url) || ''; } catch (_) { /* 框架已经没了 */ }
    if (isDesignUrl(from || d.referrer || '')) return true;
    return [...designWindows.values()].some((x) => !x.win.isDestroyed() && x.win.webContents.id === d.webContentsId);
  };
  session.defaultSession.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] }, (d, done) => done({ cancel: fromDesign(d) }));
}

// 单独打开的设计稿：文件改过就重新载入（由 watcher.js 定时调用）
function reloadChanged() {
  for (const [file, shown] of designWindows) {
    const now = mtimeOf(file);
    if (now && now !== shown.mtime && !shown.win.isDestroyed()) { shown.mtime = now; shown.win.webContents.reloadIgnoringCache(); }
  }
}

module.exports = { openDesign, guard, noNetworkForDesigns, reloadChanged };
