// 设置窗口分类页的接入代码：只读输入、驱动真实的 settings.js（界面脚本）和真实的 main/settings.js（保存到文件），
// 不含数据、不做比对。界面用最小的假 DOM，保存的结果从设置文件里读回来。
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const root = path.join(__dirname, '..', 'app', 'src');
const scripts = path.join(root, 'renderer', 'scripts');

function loadMainSettings(home) {
  const file = path.join(root, 'main', 'settings.js');
  const localRequire = createRequire(file);
  const mocks = { './config': { RF_HOME: home, AGENT: ['claude'] } };
  const context = vm.createContext({ require: (id) => Object.hasOwn(mocks, id) ? mocks[id] : localRequire(id), module: { exports: {} }, process, Buffer, clearTimeout });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
  return context.module.exports;
}

function page(settings) {
  const els = {};
  class El {
    constructor(id) {
      this.id = id; this._v = ''; this.checked = false; this.hidden = false; this.disabled = false; this.textContent = ''; this.title = '';
      this.attrs = {}; this.handlers = {}; this.dataset = {}; this.scrollTop = 0; this.tabIndex = 0; this.min = ''; this.max = '';
      this.flags = new Set();
      this.classList = { toggle: (c, on) => { if (on) this.flags.add(c); else this.flags.delete(c); }, remove: (c) => this.flags.delete(c), add: (c) => this.flags.add(c), contains: (c) => this.flags.has(c) };
    }
    get value() { return this._v; }
    set value(v) { this._v = String(v); } // 真实的输入框也把值转成字符串
    addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return this.attrs[k]; }
    focus() { page.focused = this.id; }
    querySelector() { return new El('row-part'); }
    closest() { return this; }
    async fire(type, event = {}) {
      const e = { preventDefault() { e.prevented = true; }, target: this, ...event };
      for (const fn of this.handlers[type] || []) await fn(e);
      return e;
    }
  }
  const $ = (id) => els[id] || (els[id] = new El(id));
  const ids = ['settings-dlg', 'settings-form', 'settings-tab-general', 'settings-tab-agents', 'settings-pane-general', 'settings-pane-agents', 'settings-body',
    'dropped-days', 'dropped-max', 'settings-error', 'settings-cli-status', 'settings-cli-check', 'settings-cli-models', 'language-options', 'agent-options',
    'settings-monitor', 'approval-options', 'dropped-options', 'settings-save', 'settings-cancel', 'settings'];
  ids.forEach($);
  $('settings-tab-general').attrs = { 'aria-controls': 'settings-pane-general', 'aria-selected': 'true' };
  $('settings-tab-agents').attrs = { 'aria-controls': 'settings-pane-agents', 'aria-selected': 'false' };
  $('settings-tab-agents').tabIndex = -1;
  $('settings-pane-agents').hidden = true;
  Object.assign($('dropped-days'), { min: '1', max: '3650' });
  Object.assign($('dropped-max'), { min: '1', max: '1000' });
  const form = $('settings-form');
  form.elements = { agent: { value: '' }, language: { value: '' }, headlessApproval: { value: '' } };
  const dialog = $('settings-dlg');
  dialog.open = false;
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; dialog.fire('close'); };
  const state = { saveCalls: 0, reloaded: null };
  const ctx = vm.createContext({
    $, document: { documentElement: { lang: 'en' } }, location: { search: '?language=en', href: 'file:///index.html?language=en', replace: (u) => { state.reloaded = u; } },
    URL, URLSearchParams, setTimeout, clearTimeout,
    window: { shell: {
      getSettings: async () => ({ ...settings.get(), catalogs: {} }),
      saveSettings: async (value) => { state.saveCalls++; try { return settings.save(value); } catch (e) { return { error: e.message }; } },
      cliAuthStatus: async () => ({ error: 'offline' }), loginCli: async () => ({}), cancelCliLogin() {},
    } },
    subagentChoices: () => ({ lock() {}, reset() {}, status() {}, changed: () => false, value: () => ({}), catalogs() {} }),
    applyRequirementCatalogs() {},
  });
  for (const f of ['html-text', 'cli-auth-model']) vm.runInContext(fs.readFileSync(path.join(scripts, f + '.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(scripts, 'settings.js'), 'utf8') + '\nwireSettings();', ctx);
  return { $, form, dialog, state };
}

async function run(input) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-settings-tabs-'));
  try {
    // 前提：已保存的设置（只写这次要用的几项，其余走默认）
    const seed = {};
    if (input.saved.language) seed.language = input.saved.language;
    if (input.saved.headlessApproval) seed.headlessApproval = input.saved.headlessApproval;
    if (input.saved.keepDays) seed.droppedBranches = { keepDays: input.saved.keepDays, keepMax: 20 };
    fs.writeFileSync(path.join(home, 'settings.json'), JSON.stringify(seed));
    const settings = loadMainSettings(home);
    const p = page(settings);
    // 操作：打开 / 改某一项 / 切分类 / 保存 / 取消，依次执行
    for (const a of input.actions) {
      if (a.do === 'open') await p.$('settings').fire('click');
      else if (a.do === 'tab') await p.$(a.to === 'agents' ? 'settings-tab-agents' : 'settings-tab-general').fire('click');
      else if (a.do === 'language') p.form.elements.language.value = a.value;
      else if (a.do === 'approval') p.form.elements.headlessApproval.value = a.value;
      else if (a.do === 'days' || a.do === 'max') {
        const el = p.$(a.do === 'days' ? 'dropped-days' : 'dropped-max');
        el.value = String(a.value);
        await el.fire('input');
      } else if (a.do === 'save') {
        await p.form.fire('submit');
      } else if (a.do === 'cancel') await p.$('settings-cancel').fire('click');
      else throw new Error('未知操作 ' + a.do);
    }
    const tabOf = () => p.$('settings-tab-general').attrs['aria-selected'] === 'true' ? 'general' : 'agents';
    const saved = settings.get();
    const out = {
      dialogOpen: p.dialog.open,
      tab: tabOf(),
      form: { language: p.form.elements.language.value, headlessApproval: p.form.elements.headlessApproval.value, keepDays: p.$('dropped-days').value },
      invalid: ['dropped-days', 'dropped-max'].filter((id) => p.$(id).flags.has('invalid')),
      errorShown: !p.$('settings-error').hidden,
      saved: { language: saved.language, headlessApproval: saved.headlessApproval, keepDays: saved.droppedBranches.keepDays },
      saveCalls: p.state.saveCalls,
    };
    return out;
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
}
module.exports = { run };
