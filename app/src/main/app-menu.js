// macOS 的系统菜单。编辑和窗口操作交给系统处理，项目操作复用主窗口里的表单。
const { app, Menu } = require('electron');
const { state, send } = require('./state');

const projectActions = ['new-req', 'new-issue'];
const windowActions = ['about', 'settings'];
let windowReady = false;
let readyProject = null;
let hasUsableProject;

function canCreate() { return windowReady && state.project === readyProject && hasUsableProject(); }

function refresh() {
  if (!hasUsableProject) return;
  const menu = Menu.getApplicationMenu();
  const enabled = canCreate();
  for (const id of projectActions) menu.getMenuItemById(id).enabled = enabled;
  for (const id of windowActions) menu.getMenuItemById(id).enabled = windowReady;
  menu.getMenuItemById('main-window').enabled = Boolean(state.win && !state.win.isDestroyed());
}

function setWindowReady(ready) {
  windowReady = ready;
  readyProject = ready ? state.project : null;
  refresh();
}

function showMainWindow() {
  const { win } = state;
  if (win && !win.isDestroyed()) { win.show(); win.focus(); }
}

function command(id, label, { accelerator, run = () => send('app-menu:action', id) } = {}) {
  return { id, label, accelerator, enabled: false, click: () => {
    if (!windowReady || (projectActions.includes(id) && !canCreate())) return;
    showMainWindow();
    run();
  } };
}

const separator = () => ({ type: 'separator' });
const native = (label, role, accelerator) => ({ label, role, accelerator });

function install(canUseProject, language) {
  const label = (zh, en) => language === 'en' ? en : zh;
  if (process.platform !== 'darwin') return;
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: 'RelayForge', submenu: [
      command('about', label('关于 RelayForge', 'About RelayForge'), { run: () => send('window:about', app.getVersion()) }),
      command('settings', label('设置…', 'Settings…'), { accelerator: 'Command+,' }), separator(),
      native(label('隐藏 RelayForge', 'Hide RelayForge'), 'hide', 'Command+H'), native(label('退出 RelayForge', 'Quit RelayForge'), 'quit', 'Command+Q'),
    ] },
    { label: label('文件', 'File'), submenu: [
      command('new-req', label('新建需求…', 'New Requirement…')), command('new-issue', label('记个问题…', 'Report Issue…')), separator(),
      native(label('关闭窗口', 'Close Window'), 'close', 'Command+W'),
    ] },
    { label: label('编辑', 'Edit'), submenu: [
      native(label('撤销', 'Undo'), 'undo', 'Command+Z'), native(label('重做', 'Redo'), 'redo', 'Command+Shift+Z'), separator(),
      native(label('剪切', 'Cut'), 'cut', 'Command+X'), native(label('复制', 'Copy'), 'copy', 'Command+C'),
      native(label('粘贴', 'Paste'), 'paste', 'Command+V'), native(label('全选', 'Select All'), 'selectAll', 'Command+A'),
    ] },
    { label: label('显示', 'View'), submenu: [
      native(label('实际大小', 'Actual Size'), 'resetZoom', 'Command+0'), native(label('放大', 'Zoom In'), 'zoomIn', 'Command+Plus'),
      native(label('缩小', 'Zoom Out'), 'zoomOut', 'Command+-'), separator(), native(label('进入全屏', 'Enter Full Screen'), 'togglefullscreen'),
    ] },
    { label: label('窗口', 'Window'), submenu: [
      native(label('最小化', 'Minimize'), 'minimize', 'Command+M'), native(label('缩放', 'Zoom'), 'zoom'), separator(),
      { id: 'main-window', label: 'RelayForge', click: showMainWindow },
    ] },
  ]));
  hasUsableProject = canUseProject;
  refresh();
}

function relabel(language) {
  if (hasUsableProject) install(hasUsableProject, language);
}

module.exports = { install, refresh, relabel, setWindowReady };
