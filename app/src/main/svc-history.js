// 服务的启动记录：每个服务一份（按项目、按需求或主线、按服务），按时间追加，存本机（和日志文件放一起，不进仓库）。
//   位置：<日志文件所在目录>/services-history/<服务名（URL 编码）>.jsonl，一行一条 JSON：
//   { t 毫秒时间, action 动作, reason 原因, head 短提交编号, dirty 有没有未提交的改动, code 退出码, signal, ms 从启动到可用的毫秒 }
//   只留最近 MAX 条；记录里只有动作、原因、提交编号、退出码、用时，不含保密值（原因文字再打一遍码）。
const fs = require('fs');
const path = require('path');

const MAX = 200;
const ACTIONS = ['启动', '停止', '重启', '自动重启', '自己退出', '启动失败', '应用退出时停掉', '清理遗留进程'];
const cache = new Map(); // 文件 -> 记录（最近 MAX 条）；最多记 FILES 个文件，按最近用过的留（每个需求、每个服务一个文件，开久了会越攒越多）
const FILES = 50;

const dirOf = (logFile) => (logFile ? path.join(path.dirname(logFile), 'services-history') : null);
const fileOf = (logFile, name) => (logFile ? path.join(dirOf(logFile), encodeURIComponent(String(name)) + '.jsonl') : null);

function load(file) {
  if (!file) return [];
  if (cache.has(file)) { const rows = cache.get(file); cache.delete(file); cache.set(file, rows); return rows; } // 挪到最后：最近用过
  let rows = [];
  try {
    rows = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map((l) => { try { return JSON.parse(l); } catch (_) { return null; } }).filter((r) => r && r.action);
  } catch (_) { /* 还没有记录 */ }
  if (rows.length > MAX) rows = rows.slice(-MAX);
  cache.set(file, rows);
  while (cache.size > FILES) cache.delete(cache.keys().next().value);
  return rows;
}

// 追加一条。rec：{ action, reason, head, dirty, code, signal, ms }；scrub 把保密值打码
function append(logFile, name, rec, scrub) {
  const file = fileOf(logFile, name);
  if (!file) return null;
  const row = { t: Date.now(), action: ACTIONS.includes(rec.action) ? rec.action : String(rec.action || ''), reason: scrub ? scrub(rec.reason || '') : String(rec.reason || '') };
  if (rec.head) row.head = String(rec.head).slice(0, 7);
  if (rec.dirty != null) row.dirty = !!rec.dirty;
  if (rec.code != null) row.code = rec.code;
  if (rec.signal) row.signal = String(rec.signal);
  if (rec.ms != null) row.ms = Math.round(rec.ms);
  const rows = load(file);
  rows.push(row);
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    if (rows.length > MAX) { rows.splice(0, rows.length - MAX); fs.writeFileSync(file, rows.map((r) => JSON.stringify(r)).join('\n') + '\n', { mode: 0o600 }); }
    else fs.appendFileSync(file, JSON.stringify(row) + '\n', { mode: 0o600 });
  } catch (_) { /* 记不下不挡运行 */ }
  return row;
}

function recent(logFile, name, n = 50) { return load(fileOf(logFile, name)).slice(-n); }

// 这个目录下所有服务的最近记录：{ 服务名: [...] }
function all(logFile, n = 50) {
  const out = {};
  const dir = dirOf(logFile);
  let names = [];
  try { names = fs.readdirSync(dir).filter((x) => x.endsWith('.jsonl')); } catch (_) { return out; }
  for (const f of names) {
    let name; try { name = decodeURIComponent(f.slice(0, -6)); } catch (_) { continue; }
    out[name] = load(path.join(dir, f)).slice(-n);
  }
  return out;
}

module.exports = { MAX, ACTIONS, append, recent, all, fileOf };
