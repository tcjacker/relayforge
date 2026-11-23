// 进程记录的签名：应用启动时照着 RF_HOME/run/ 下的记录杀上次没退干净的进程（session-procs.js、services.js 的 sweep）。
// 记录只是普通文件，写得了它就能让应用替自己杀掉任意进程（启动时间也挡不住：沙箱里读得到别的进程的启动时间）。
// 会话的沙箱本来就写不了 run/（claude-settings.js 的 sandbox），这里再加一层：应用写记录时用只有它读得到的钥匙签名，
// sweep 只认签名对得上的。钥匙在 RF_HOME/keys/run.key（600，目录 700），会话的沙箱读不了也写不了这个目录。
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { RF_HOME } = require('./config');

const FILE = path.join(RF_HOME, 'keys', 'run.key');
let cached = null;

function key() {
  if (cached) return cached;
  try { cached = fs.readFileSync(FILE); if (cached.length >= 32) return cached; } catch (_) { /* 还没有：建一把 */ }
  fs.mkdirSync(path.dirname(FILE), { recursive: true, mode: 0o700 });
  const fresh = crypto.randomBytes(32);
  try { fs.writeFileSync(FILE, fresh, { mode: 0o600, flag: 'wx' }); cached = fresh; } // wx：别的应用刚建了就用它的
  catch (_) { cached = fs.readFileSync(FILE); }
  return cached;
}

// 签名盖住记录里除了 sig 以外的所有字段（按键排序）
const body = (record) => JSON.stringify(Object.keys(record).filter((k) => k !== 'sig').sort().map((k) => [k, record[k]]));
const mac = (record) => crypto.createHmac('sha256', key()).update(body(record)).digest('hex');

function sign(record) { return { ...record, sig: mac(record) }; }

function verify(record) {
  if (!record || typeof record !== 'object' || typeof record.sig !== 'string') return false;
  let expected;
  try { expected = Buffer.from(mac(record), 'hex'); } catch (_) { return false; }
  const given = Buffer.from(record.sig, 'hex');
  return given.length === expected.length && crypto.timingSafeEqual(given, expected);
}

module.exports = { sign, verify, FILE };
