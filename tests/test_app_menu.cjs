const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function loadMenu(platform) {
  let menu;
  const sent = [];
  const state = { project: null, win: null };
  const Menu = {
    buildFromTemplate: (groups) => ({ groups, getMenuItemById: (id) => groups.flatMap((group) => group.submenu).find((item) => item.id === id) }),
    setApplicationMenu: (value) => { menu = value; },
    getApplicationMenu: () => menu,
  };
  const mocks = { electron: { app: { getVersion: () => '1.2.3' }, Menu }, './state': { state, send: (...args) => sent.push(args) } };
  const context = { process: { platform }, module: { exports: {} }, require: (id) => mocks[id] };
  const file = path.resolve(__dirname, '../app/src/main/app-menu.js');
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
  const appMenu = context.module.exports;
  appMenu.install(() => Boolean(state.project));
  return { appMenu, menu, state, sent };
}

test('macOS 原生菜单沿用系统编辑和窗口操作', () => {
  const { menu } = loadMenu('darwin');
  assert.equal(Array.from(menu.groups, (group) => group.label).join('、'), 'RelayForge、文件、编辑、显示、窗口');
  const [, file, edit, , window] = menu.groups;
  assert.equal(file.submenu[3].role, 'close');
  assert.equal(file.submenu[3].accelerator, 'Command+W');
  assert.equal(edit.submenu[4].role, 'copy');
  assert.equal(edit.submenu[5].role, 'paste');
  assert.equal(window.submenu[0].role, 'minimize');
  assert.equal(window.submenu[0].accelerator, 'Command+M');
  assert.equal(loadMenu('linux').menu, undefined);
});

test('两个创建入口只发送给已加载当前项目的主窗口', () => {
  const { appMenu, menu, state, sent } = loadMenu('darwin');
  const req = menu.getMenuItemById('new-req'), issue = menu.getMenuItemById('new-issue');
  state.win = { isDestroyed: () => false, show() {}, focus() {} };
  appMenu.setWindowReady(true);
  assert.deepEqual([req.enabled, issue.enabled], [false, false]);
  req.click(); issue.click();
  assert.deepEqual(sent, []);

  state.project = '/first';
  appMenu.setWindowReady(true);
  assert.deepEqual([req.enabled, issue.enabled], [true, true]);
  req.click(); issue.click();
  assert.deepEqual(sent, [['app-menu:action', 'new-req'], ['app-menu:action', 'new-issue']]);

  state.project = '/second';
  appMenu.setWindowReady(false);
  assert.deepEqual([req.enabled, issue.enabled], [false, false]);
  req.click();
  assert.equal(sent.length, 2);
  appMenu.setWindowReady(true);
  assert.deepEqual([req.enabled, issue.enabled], [true, true]);
});

test('关于菜单在空项目窗口也能打开应用内版本弹窗', () => {
  const { appMenu, menu, state, sent } = loadMenu('darwin');
  const about = menu.getMenuItemById('about');
  assert.equal(about.enabled, false);
  state.win = { isDestroyed: () => false, show() {}, focus() {} };
  appMenu.setWindowReady(true);
  assert.equal(about.enabled, true);
  about.click();
  assert.deepEqual(sent, [['window:about', '1.2.3']]);
});
