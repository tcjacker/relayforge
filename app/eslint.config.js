// ESLint：主要防「用到了一个哪里都没定义的名字」（比如 new-req.js 曾经引用不存在的 testingPicker，一调用就抛 ReferenceError）。
// 界面的脚本都是普通脚本，按 index.html 里的顺序加载、共用同一个全局作用域：一个脚本顶层声明的名字，别的脚本可以直接用。
// 所以这里读 index.html 列出的脚本，把它们顶层声明的名字收集起来当作共有的全局变量；不在这里面、也不是浏览器自带的名字就报错。
const fs = require('node:fs');
const path = require('node:path');
const espree = require('espree');
const js = require('@eslint/js');
const globals = require('globals');

const RENDERER = path.join(__dirname, 'src', 'renderer');

// index.html 里按顺序加载的界面脚本（不含 node_modules 里的 xterm）
function rendererScripts() {
  const html = fs.readFileSync(path.join(RENDERER, 'index.html'), 'utf8');
  return [...html.matchAll(/<script src="(scripts\/[\w-]+\.js)"><\/script>/g)].map((m) => path.join(RENDERER, m[1]));
}

// 一个脚本放进全局的名字：顶层的 function、var / let / const、class，以及任何地方的 window.名字 = …（比如 requirement-sessions.js）
function globalNames(file) {
  const ast = espree.parse(fs.readFileSync(file, 'utf8'), { ecmaVersion: 'latest', sourceType: 'script' });
  const names = [];
  for (const node of ast.body) {
    if ((node.type === 'FunctionDeclaration' || node.type === 'ClassDeclaration') && node.id) names.push(node.id.name);
    if (node.type === 'VariableDeclaration') for (const d of node.declarations) if (d.id.type === 'Identifier') names.push(d.id.name);
  }
  const visit = (node) => {
    if (!node || typeof node.type !== 'string') return;
    const left = node.type === 'AssignmentExpression' && node.left;
    if (left && left.type === 'MemberExpression' && !left.computed && left.object.type === 'Identifier' && left.object.name === 'window') names.push(left.property.name);
    for (const value of Object.values(node)) (Array.isArray(value) ? value : [value]).forEach((child) => child && typeof child.type === 'string' && visit(child));
  };
  visit(ast);
  return names;
}

const shared = {};
for (const file of rendererScripts()) for (const name of globalNames(file)) shared[name] = 'writable';

module.exports = [
  { ignores: ['node_modules/**', '.electron-runtime/**'] },
  js.configs.recommended,
  {
    files: ['src/renderer/scripts/**/*.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'script',
      globals: {
        ...globals.browser,
        ...shared,
        Terminal: 'readonly', FitAddon: 'readonly', // node_modules/@xterm 在 index.html 里先加载
        module: 'readonly', require: 'readonly', // *-model.js：在 Node 的测试里被 require，也从那里 require html-text.js
      },
    },
    rules: {
      // 每个脚本自己的顶层名字也在上面的共有全局里，和它比没有意义；两个脚本声明同一个名字由 tests/test_app_lint.cjs 检查
      'no-redeclare': ['error', { builtinGlobals: false }],
      // 顶层的函数大多是给别的脚本用的，只查函数里面的
      'no-unused-vars': ['error', { vars: 'local', caughtErrors: 'none', ignoreRestSiblings: true }],
    },
  },
  {
    files: ['src/main/**/*.js', 'src/preload/**/*.js', 'scripts/**/*.js', 'eslint.config.js'],
    languageOptions: { ecmaVersion: 'latest', sourceType: 'commonjs', globals: { ...globals.node } },
    rules: { 'no-unused-vars': ['error', { caughtErrors: 'none', ignoreRestSiblings: true }] },
  },
  {
    // macOS 自带的 JavaScript for Automation（osascript -l JavaScript）运行的脚本；run 是 osascript 调用的入口
    files: ['scripts/render-icon.js'],
    languageOptions: { sourceType: 'script', globals: { ObjC: 'readonly', $: 'readonly' } },
    rules: { 'no-unused-vars': ['error', { varsIgnorePattern: '^run$' }] },
  },
  { rules: { 'no-empty': ['error', { allowEmptyCatch: true }] } },
];
