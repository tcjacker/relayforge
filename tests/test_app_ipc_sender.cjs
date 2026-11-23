// 界面和主进程之间的通道只认主窗口顶层的 index.html（ipc.js）；主窗口也只许停在 index.html 上（designs.js 的 guard）。
// 这样界面哪里漏了转义、设计稿或 iframe、跳到别的本地页面，都拿不到 window.shell 去取明文、推送、同意执行命令。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { pathToFileURL } = require('node:url');

const main = path.join(__dirname, '..', 'app', 'src', 'main');
const index = path.join(main, '..', 'renderer', 'index.html');
const indexUrl = pathToFileURL(index).href;

function load(name, mocks) {
  const context = vm.createContext({ require: (id) => (Object.hasOwn(mocks, id) ? mocks[id] : id.startsWith('.') ? new Proxy({}, { get: () => () => ({}) }) : require(id)),
    module: { exports: {} }, process, Buffer, setTimeout, clearTimeout, console });
  vm.runInContext(fs.readFileSync(path.join(main, name + '.js'), 'utf8'), context);
  return context.module.exports;
}

test('every channel answers only the main window\'s own page', async () => {
  const handlers = new Map(), listeners = new Map();
  const mainFrame = { url: indexUrl + '?empty=1' };
  const win = { isDestroyed: () => false, webContents: { mainFrame } };
  const calls = [];
  const ipc = load('ipc', {
    electron: { ipcMain: { handle: (name, fn) => handlers.set(name, fn), on: (name, fn) => listeners.set(name, fn) } },
    './config': { INDEX_HTML: index },
    './locale': require(path.join(main, 'locale')),
    './state': { state: { win, runs: new Map() }, send() {} },
    './settings': { get: () => { calls.push('settings:get'); return {}; }, catalogs: () => ({}) },
    './inbox': { messagesFor: (id) => { calls.push('inbox:list ' + id); return []; }, withdraw: (to, id) => {
      calls.push('inbox:withdraw ' + to + ' ' + id);
      return true;
    } },
    './sessions': { input: (id) => calls.push('term:input ' + id) },
  });
  ipc.register();
  assert.ok(handlers.size > 50 && listeners.size > 3, 'channels registered');
  const get = handlers.get('settings:get');
  const messages = handlers.get('inbox:list'), withdraw = handlers.get('inbox:withdraw');
  const input = listeners.get('term:input');

  await get({ sender: win.webContents, senderFrame: mainFrame });
  input({ sender: win.webContents, senderFrame: mainFrame }, { id: 'a', data: 'x' });
  assert.deepEqual(calls, ['settings:get', 'term:input a']);

  const subFrame = { url: indexUrl };
  const forged = [
    null, {},
    { sender: { mainFrame: {} }, senderFrame: mainFrame }, // 别的窗口（设计稿窗口）
    { sender: win.webContents, senderFrame: subFrame }, // 主窗口里的 iframe
    { sender: win.webContents, senderFrame: { url: indexUrl } }, // 冒充主窗口地址的别的框架
  ];
  for (const event of forged) {
    assert.throws(() => get(event), /不认这个来源/);
    assert.throws(() => messages(event, 'R-1'), /不认这个来源/);
    assert.throws(() => withdraw(event, 'R-1', 'm-1'), /不认这个来源/);
    input(event, { id: 'b', data: 'x' });
  }
  mainFrame.url = 'file:///tmp/evil.html'; // 主窗口跳到了别的本地页面
  assert.throws(() => get({ sender: win.webContents, senderFrame: mainFrame }), /不认这个来源/);
  win.isDestroyed = () => true;
  mainFrame.url = indexUrl;
  assert.throws(() => get({ sender: win.webContents, senderFrame: mainFrame }), /不认这个来源/);
  assert.deepEqual(calls, ['settings:get', 'term:input a']);
  win.isDestroyed = () => false;
  const event = { sender: win.webContents, senderFrame: mainFrame };
  assert.deepEqual(messages(event, 'R-1'), []);
  assert.equal(withdraw(event, 'R-1', { id: 'm-1' }), false);
  assert.equal(withdraw(event, 'R-1', 'm-1'), true);
  assert.deepEqual(calls.slice(2), ['inbox:list R-1', 'inbox:withdraw R-1 m-1']);
});

test('the main window may only stay on index.html', () => {
  const designs = load('designs', { electron: { BrowserWindow: class {}, session: {} }, './config': { INDEX_HTML: index } });
  const contents = new EventEmitter();
  contents.setWindowOpenHandler = () => {};
  designs.guard(contents);
  const go = (url) => { let stopped = false; contents.emit('will-navigate', { url, preventDefault: () => { stopped = true; } }); return !stopped; };
  assert.equal(go(indexUrl), true);
  assert.equal(go(indexUrl + '?empty=1'), true);
  assert.equal(go('file:///tmp/evil.html'), false);
  assert.equal(go('file:///Users/me/project/.relayforge/designs/DS-20261122-abcd.html'), false);
  assert.equal(go('https://example.com/'), false);
});
