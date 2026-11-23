// Codex 自己分配会话编号；应用编号写入开场白，用它找到对应的记录并恢复。
const fs = require('fs');
const path = require('path');
const os = require('os');
const { RF_HOME } = require('./config');
const file = path.join(RF_HOME, 'codex-sessions.json');
const home = process.env.CODEX_HOME || path.join(os.homedir(), '.codex');
let records;
function all() {
  if (!records) { try { records = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (error) { if (error.code !== 'ENOENT') throw error; records = {}; } }
  return records;
}
function persist() {
  fs.mkdirSync(RF_HOME, { recursive: true });
  fs.writeFileSync(file + '.tmp', JSON.stringify(all(), null, 2) + '\n', { mode: 0o600 });
  fs.renameSync(file + '.tmp', file);
}
function register(id, root, title, project, chat) {
  all()[id] = { root, title, project, chat, started: Date.now() };
  persist();
}
// app-server 直接返回线程和聊天记录。文件尚未落盘时，find 仍可按开场白里的标识查找。
function bindThread(id, thread) {
  const record = all()[id];
  if (!record || !thread.path) return;
  Object.assign(record, { nativeId: thread.id, transcript: thread.path });
  persist();
}
function files(dir) {
  try { return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => entry.isDirectory() ? files(path.join(dir, entry.name)) : entry.name.endsWith('.jsonl') ? [path.join(dir, entry.name)] : []); } catch (_) { return []; }
}

function readCandidate(file, root, marker) {
  let fd;
  try {
    fd = fs.openSync(file, 'r');
    const chunk = Buffer.alloc(64 * 1024);
    let pending = Buffer.alloc(0);
    let nativeId;
    for (let size; (size = fs.readSync(fd, chunk, 0, chunk.length, null)) > 0;) {
      pending = Buffer.concat([pending, chunk.subarray(0, size)]);
      if (!nativeId) {
        const end = pending.indexOf(10);
        if (end < 0) continue;
        const header = JSON.parse(pending.subarray(0, end).toString('utf8'));
        const meta = header?.type === 'session_meta' && header.payload;
        if (!meta || typeof meta.id !== 'string' || !meta.id || typeof meta.cwd !== 'string' ||
            path.resolve(meta.cwd) !== path.resolve(root)) return null;
        // 审查线程和子代理的记录可能也带着原对话的开场白。
        if (meta.thread_source === 'guardian_review' || meta.source?.subagent) return null;
        nativeId = meta.id;
        if (!marker) return nativeId;
        pending = pending.subarray(end + 1);
      }
      // 开场白可能在一大段启动上下文之后，标识也可能跨过两个读取块。
      if (pending.includes(marker)) return nativeId;
      pending = pending.subarray(Math.max(0, pending.length - marker.length + 1));
    }
  } catch (error) {
    if (!(error instanceof SyntaxError) && error.code !== 'ENOENT') throw error;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
  return null;
}

function find(id) {
  const record = all()[id];
  if (!record) return null;
  if (record.transcript && readCandidate(record.transcript, record.root) === record.nativeId) return record.transcript;
  const hadMapping = !!(record.transcript || record.nativeId);
  const marker = Buffer.from('RelayForge 会话标识：' + id);
  for (const candidate of files(path.join(home, 'sessions')).reverse()) {
    const stat = fs.statSync(candidate, { throwIfNoEntry: false });
    if (!stat || stat.mtimeMs < record.started - 2000) continue;
    const nativeId = readCandidate(candidate, record.root, marker);
    if (!nativeId) continue;
    record.nativeId = nativeId;
    record.transcript = candidate;
    persist();
    return candidate;
  }
  if (hadMapping) {
    delete record.nativeId;
    delete record.transcript;
    persist();
  }
  return null;
}
function nativeId(id) { find(id); return all()[id] && all()[id].nativeId; }
function has(id) { return Object.hasOwn(all(), id); }
function chats(project) {
  return Object.entries(all()).filter(([, r]) => r.chat && r.project === project).map(([id, r]) => ({
    session_id: id, title: r.title || 'Codex 聊天', first: 'Codex', updated: new Date(r.started).toLocaleString('sv-SE').slice(0, 16), transcript: r.transcript || '',
  }));
}
module.exports = { register, bindThread, find, nativeId, has, chats };
