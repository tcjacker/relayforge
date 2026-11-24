// 英文界面里不混中文（问题 #039）：提示词是中文写的，会话的回复要用英文界面上的说法引用按钮和标签；
// 界面里的静态文案要都在英文词典里。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repo = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(repo, ...p), 'utf8');

// html-copy.js 里的 staticEnglish 是普通的对象字面量：只求值这一段，不跑文件末尾碰 DOM 的部分
function staticEnglish() {
  const src = read('app/src/renderer/scripts/html-copy.js');
  const start = src.indexOf('var staticEnglish = {');
  const end = src.indexOf('\n};', start) + 3;
  return vm.runInNewContext(src.slice(start, end) + '\n;staticEnglish', {});
}

// 界面上所有英文译文的来源：词典、各界面脚本里的 uiText 英文参数、index.html
function uiCorpus() {
  const dir = path.join(repo, 'app/src/renderer/scripts');
  const scripts = fs.readdirSync(dir).filter((f) => f.endsWith('.js')).map((f) => read('app/src/renderer/scripts', f));
  return scripts.concat([read('app/src/renderer/index.html')]).join('\n');
}

test('英文界面的会话按英文界面上的说法引用按钮，中文界面不加', () => {
  const uiNames = require(path.join(repo, 'app/src/main/ui-names'));
  assert.equal(uiNames.guide('zh-CN'), '');
  const guide = uiNames.guide('en');
  assert.ok(guide.length > 0);
  for (const [zh, en] of uiNames.NAMES) {
    assert.ok(guide.includes(`「${zh}」`) && guide.includes(`「${en}」`), `${zh} 没有写进英文对照`);
  }
});

test('对照里的英文都是界面上真的显示的译文，不是自己编的', () => {
  const uiNames = require(path.join(repo, 'app/src/main/ui-names'));
  const corpus = uiCorpus();
  const words = new Set(Object.values(staticEnglish()));
  for (const [zh, en] of uiNames.NAMES) {
    assert.ok(words.has(en) || corpus.includes(en), `「${zh}」的英文「${en}」在界面里找不到`);
  }
});

test('index.html 里「跳过检查，仍然合并」的文字和提示都有英文译文', () => {
  const dict = staticEnglish();
  assert.equal(typeof dict['跳过检查，仍然合并'], 'string');
  assert.equal(typeof dict['合并后的代码没过合并前的检查：不再检查，直接合并（完成记录里会写明）'], 'string');
});

test('会话的回复规则把界面名字对照带进英文会话', () => {
  const sessions = read('app/src/main/sessions.js');
  assert.match(sessions, /require\('\.\/ui-names'\)/);
  assert.match(sessions, /uiNames\.guide\(language\)/);
});
