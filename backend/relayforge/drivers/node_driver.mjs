// 测试集的 Node 驱动：协议和 python_driver.py 一样（见 FORMAT.md 的「测试集」）。
//   标准输入一份 JSON {"protocol":1,"root","entry","cases":[{"n","input"}…]}；
//   标准输出一行一个 JSON：{"t":"ready"}、{"t":"load_error",…}、{"t":"case",…}、{"t":"done"}。
// 接入代码：一个模块，导出 run(input)（可以是 async）；CommonJS（module.exports.run / exports.run）和 ES 模块
// （export function run / export default）都行，按扩展名和最近的 package.json 的 type 判断，判断不了就两种都试。
// 只用 Node 自带的模块。被测代码写到 stdout 的东西（console.log、process.stdout.write）转到标准错误。
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

// 协议走原来的 stdout.write；之后 process.stdout.write 换成写标准错误，console.log 等也就跟着去了标准错误
const protocolWrite = process.stdout.write.bind(process.stdout);
process.stdout.write = (chunk, encoding, callback) => process.stderr.write(chunk, encoding, callback);

const say = (obj) => protocolWrite(JSON.stringify(obj) + '\n');
const finish = (code = 0) => {
  process.exitCode = code;
  protocolWrite('', () => process.exit(code)); // 等已经写出的行冲出去再退，被测代码留下的定时器、连接不会拖住进程
};
// 被测代码在这一条之外漏出来的异步错误：记到标准错误，不让进程死在半路
process.on('uncaughtException', (error) => process.stderr.write(`未捕获的异常：${error && error.stack || error}\n`));
process.on('unhandledRejection', (reason) => process.stderr.write(`未处理的 Promise 拒绝：${reason && reason.stack || reason}\n`));

const describe = (error) => {
  if (error instanceof Error) return [`${error.name}: ${error.message}`, String(error.stack || '').slice(-4000)];
  let text;
  try { text = typeof error === 'string' ? error : JSON.stringify(error); } catch { text = String(error); }
  return [`抛出了不是 Error 的值：${text}`, ''];
};

const readStdin = async () => {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
};

// 接入代码按哪种模块加载：'esm'、'cjs'，或 null（判断不了，两种都试）
const moduleKind = (entry, root) => {
  const ext = path.extname(entry).toLowerCase();
  if (ext === '.mjs') return 'esm';
  if (ext === '.cjs') return 'cjs';
  let dir = path.dirname(entry);
  for (;;) {
    const file = path.join(dir, 'package.json');
    if (fs.existsSync(file)) {
      try {
        const type = JSON.parse(fs.readFileSync(file, 'utf8')).type;
        if (type === 'module') return 'esm';
        if (type === 'commonjs') return 'cjs';
      } catch { /* 读不了就当没有 */ }
      return null; // 最近的 package.json 没写 type
    }
    const up = path.dirname(dir);
    if (up === dir || path.relative(root, dir) === '' || path.relative(root, dir).startsWith('..')) return null;
    dir = up;
  }
};

const esmError = (error) => error && (error.code === 'ERR_REQUIRE_ESM' || error.code === 'ERR_REQUIRE_ASYNC_MODULE'
  || (error instanceof SyntaxError && /import statement|export|import\.meta|top-level await/i.test(error.message)));

const loadModule = async (entry, root) => {
  const asEsm = () => import(pathToFileURL(entry).href);
  const asCjs = () => createRequire(path.join(root, 'noop.js'))(entry);
  const kind = moduleKind(entry, root);
  if (kind === 'esm') return asEsm();
  if (kind === 'cjs') return asCjs();
  try {
    return asCjs();
  } catch (error) {
    if (!esmError(error)) throw error;
    return asEsm();
  }
};

const pickRun = (mod) => {
  if (!mod) return null;
  if (typeof mod.run === 'function') return mod.run;
  const dflt = mod.default;
  if (typeof dflt === 'function') return dflt; // export default function run(input)
  if (dflt && typeof dflt.run === 'function') return dflt.run.bind(dflt); // CJS 经 import() 进来，或 export default { run }
  if (typeof mod === 'function') return mod; // module.exports = function (input) {…}
  return null;
};

const toJson = (value) => {
  if (value === undefined) return null;
  if (typeof value === 'function' || typeof value === 'symbol') throw new TypeError(`返回值不能转成 JSON（返回的是 ${typeof value}）`);
  const text = JSON.stringify(value, (key, v) => {
    if (typeof v === 'number' && !Number.isFinite(v)) throw new TypeError(`返回值里有 ${v}，JSON 表示不了`);
    if (typeof v === 'bigint') throw new TypeError('返回值里有 BigInt，JSON 表示不了');
    return v;
  });
  return text === undefined ? null : JSON.parse(text);
};

const main = async () => {
  let req;
  try {
    req = JSON.parse((await readStdin()) || '{}');
  } catch (error) {
    say({ t: 'load_error', error: `驱动收到的不是 JSON：${error.message}`, trace: '' });
    return finish();
  }
  const root = path.resolve(req.root || process.cwd());
  const entry = path.resolve(root, String(req.entry || ''));
  let run;
  try {
    if (!fs.existsSync(entry) || !fs.statSync(entry).isFile()) throw new Error(`找不到接入代码 ${req.entry}（在 ${root} 下）`);
    run = pickRun(await loadModule(entry, root));
    if (typeof run !== 'function') throw new TypeError(`接入代码 ${req.entry} 没有导出 run(input)`);
  } catch (error) {
    const [message, trace] = describe(error);
    say({ t: 'load_error', error: message, trace });
    return finish();
  }
  say({ t: 'ready', protocol: 1 });
  for (const c of req.cases || []) {
    const started = process.hrtime.bigint();
    const ms = () => Math.round(Number(process.hrtime.bigint() - started) / 1e3) / 1e3;
    let got;
    try {
      got = await run(structuredClone(c.input === undefined ? null : c.input));
    } catch (error) {
      const [message, trace] = describe(error);
      say({ t: 'case', n: c.n, ok: false, error: message, trace, ms: ms() });
      continue;
    }
    const took = ms();
    try {
      say({ t: 'case', n: c.n, ok: true, output: toJson(got), ms: took });
    } catch (error) {
      say({ t: 'case', n: c.n, ok: false, error: `返回值不能转成 JSON：${error.message}`, trace: '', ms: took });
    }
  }
  say({ t: 'done' });
  finish();
};

main().catch((error) => {
  const [message, trace] = describe(error);
  say({ t: 'load_error', error: message, trace });
  finish();
});
