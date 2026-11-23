// 打开旧项目（记录还是仓库里的 .relayforge/ 目录）：先问一声，用户点「迁移」才运行 rf.py data migrate（带应用的标记），
// 迁好了照常打开；取消或失败就不打开。迁移本身的行为在 tests/test_migrate.py 里测。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'src', 'main', 'project.js'), 'utf8');
const settings = fs.readFileSync(path.join(__dirname, '..', 'app', 'src', 'main', 'claude-settings.js'), 'utf8');

test('opening an old project asks to migrate instead of refusing', () => {
  assert.doesNotMatch(source, /迁移还没有做好/);
  assert.match(source, /if \(found && !\(await migrateLegacy\(found\)\)\) return null;/);
  assert.match(source, /await ask\(uiText\("「\{0\}」需要迁移", .+?, name\), [\s\S]+?uiText\('迁移', "Migrate"\)\)/);
});

test('the migration goes through rf(), which carries the app marker and reports failures', () => {
  assert.match(source, /await rf\(\['data', 'migrate', '--json'\], root, undefined, 180000\)/);
  assert.match(source, /dialog\.showErrorBox\(uiText\("「\{0\}」没有迁移", .+?, name\), why\)/);
  assert.match(source, /以前写进仓库给 AI 的约定一并拿掉/);
  assert.match(source, /你自己写在 AGENTS\.md、CLAUDE\.md 里的内容和项目里别的改动都不动/);
});

test('sessions cannot run the migration', () => {
  assert.match(settings, /data migrate:\*/);
});
