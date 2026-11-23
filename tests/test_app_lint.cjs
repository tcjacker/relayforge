// 桌面端代码的静态检查：ESLint（配置见 app/eslint.config.js）。界面的脚本共用一个全局作用域，
// 最容易出的错是用到一个哪里都没定义的名字（运行到那一行才抛 ReferenceError），或者两个脚本声明了同一个名字（后加载的悄悄盖掉前面的）。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const app = path.join(__dirname, '..', 'app');
const { ESLint } = require(path.join(app, 'node_modules', 'eslint'));
const espree = require(path.join(app, 'node_modules', 'espree'));

const lint = () => new ESLint({ cwd: app });
const describe = (results) => results.flatMap((r) => r.messages.map((m) => `${path.relative(app, r.filePath)}:${m.line} ${m.ruleId || ''} ${m.message}`));

test('the desktop app passes ESLint', async () => {
  const results = await lint().lintFiles(['.']);
  const problems = describe(results);
  assert.deepEqual(problems, [], '\n' + problems.join('\n'));
});

test('a name nobody defines is caught in a renderer script', async () => {
  // 自检：配置要是改坏了（比如把什么都当成全局），这条会先失败，不会让上面那条悄悄失效
  const file = path.join(app, 'src', 'renderer', 'scripts', 'new-req.js');
  const [result] = await lint().lintText('function f() { return notDefinedAnywhere.refresh(); }\nf();\n', { filePath: file });
  assert.ok(result.messages.some((m) => m.ruleId === 'no-undef' && /notDefinedAnywhere/.test(m.message)), describe([result]).join('\n'));
});

test('no two renderer scripts declare the same top-level name', () => {
  const renderer = path.join(app, 'src', 'renderer');
  const html = fs.readFileSync(path.join(renderer, 'index.html'), 'utf8');
  const owners = {};
  for (const [, rel] of html.matchAll(/<script src="(scripts\/[\w-]+\.js)"><\/script>/g)) {
    const ast = espree.parse(fs.readFileSync(path.join(renderer, rel), 'utf8'), { ecmaVersion: 'latest', sourceType: 'script' });
    for (const node of ast.body) {
      const ids = node.type === 'VariableDeclaration' ? node.declarations.map((d) => d.id) : node.id ? [node.id] : [];
      for (const id of ids) if (id && id.type === 'Identifier') (owners[id.name] = owners[id.name] || []).push(rel);
    }
  }
  const clashes = Object.entries(owners).filter(([, files]) => files.length > 1).map(([name, files]) => `${name}: ${files.join('、')}`);
  assert.deepEqual(clashes, []);
});
