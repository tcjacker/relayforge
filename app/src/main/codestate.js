// 代码的状态：人工验收的服务启动时，工作目录里的代码是哪一份；之后代码有没有变。
// 状态 = HEAD 提交编号 + 未提交改动的指纹。指纹用 git 自带命令算：git status --porcelain 的列表，加上这些文件内容的 git 哈希（git hash-object）；
// 被 gitignore 的不算，.relayforge/（事实由程序提交，不是被验收的代码）和 .worktrees/ 不算。
// 文件太多（超过 MAX_FILES）或太大（合起来超过 MAX_BYTES）时不读内容，退化成「状态列表 + 每个文件的修改时间和大小」。
// 不是 git 目录、git 出错：状态是 null，认为代码没变（宁可不提示，也不乱提示）。
const { execFile } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const MAX_FILES = Number(process.env.RELAYFORGE_CODE_MAX_FILES || 2000);
const MAX_BYTES = Number(process.env.RELAYFORGE_CODE_MAX_BYTES || 64 * 1024 * 1024);
const STAT_CAP = 20000; // 退化时最多看这么多个文件的修改时间
const EXCLUDE = ['--', '.', ':(exclude).relayforge', ':(exclude).worktrees'];

function git(cwd, args, input) {
  return new Promise((done) => {
    const child = execFile('git', ['--no-optional-locks', ...args], { cwd, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, timeout: 20000, env: { ...process.env, GIT_OPTIONAL_LOCKS: '0' } },
      (error, stdout) => done(error ? null : stdout));
    if (input != null) { child.stdin.on('error', () => {}); child.stdin.end(input); }
  });
}

// git status --porcelain -z：「XY 路径\0」，改名和复制后面还跟一个原路径
function parseStatus(text) {
  const parts = String(text || '').split('\0');
  const entries = [];
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (p.length < 4) continue;
    const xy = p.slice(0, 2), file = p.slice(3);
    if (/[RC]/.test(xy)) i++;
    entries.push({ xy, file });
  }
  return entries;
}

async function stateOf(place) {
  if (!place || !fs.existsSync(place)) return null;
  const head = await git(place, ['rev-parse', 'HEAD']);
  const status = await git(place, ['status', '--porcelain=v1', '-z', '--untracked-files=all', ...EXCLUDE]);
  if (status === null) return null;
  const entries = parseStatus(status);
  const files = [...new Set(entries.filter((e) => !/D/.test(e.xy)).map((e) => e.file))];
  const sums = [];
  let degraded = files.length > MAX_FILES;
  const stats = new Map();
  if (!degraded) {
    let total = 0;
    for (const f of files) {
      try { const st = fs.lstatSync(path.join(place, f)); stats.set(f, st); total += st.size; } catch (_) { stats.set(f, null); }
      if (total > MAX_BYTES) { degraded = true; break; }
    }
  }
  if (degraded) {
    for (const f of files.slice(0, STAT_CAP)) {
      try { const st = fs.lstatSync(path.join(place, f)); sums.push(`${f}\0${st.size}\0${Math.floor(st.mtimeMs)}`); } catch (_) { sums.push(`${f}\0gone`); }
    }
  } else {
    const plain = [];
    for (const f of files) {
      const st = stats.get(f);
      if (!st) sums.push(`${f}\0gone`);
      else if (st.isSymbolicLink()) { let t = ''; try { t = fs.readlinkSync(path.join(place, f)); } catch (_) { /* 链接没了 */ } sums.push(`${f}\0link\0${t}`); }
      else if (!st.isFile()) sums.push(`${f}\0notfile`);
      else if (/["\\\n\r]/.test(f)) sums.push(`${f}\0${st.size}\0${Math.floor(st.mtimeMs)}`); // 名字里有引号、换行：git 的按行输入读不准，用修改时间
      else plain.push(f);
    }
    if (plain.length) {
      const out = await git(place, ['hash-object', '--no-filters', '--stdin-paths'], plain.join('\n') + '\n');
      const hashes = out === null ? [] : out.split('\n').filter(Boolean);
      if (hashes.length !== plain.length) degraded = true;
      plain.forEach((f, i) => sums.push(`${f}\0${hashes[i] || '?'}`));
    }
  }
  const fp = crypto.createHash('sha1').update(entries.map((e) => e.xy + ' ' + e.file).sort().join('\n') + '\n--\n' + sums.sort().join('\n')).digest('hex').slice(0, 16);
  const full = (head || '').trim();
  return { head: full, short: full.slice(0, 7), dirty: entries.length > 0, fp, degraded };
}

// 现在的代码和启动时的比，有没有变。HEAD 动了，但只是 .relayforge/ 的事实提交（git diff 排除以后没有差别），不算变
async function changed(start, place) {
  if (!start) return { changed: false, now: null };
  const now = await stateOf(place);
  if (!now) return { changed: false, now: null };
  if (start.fp !== now.fp) return { changed: true, now };
  if (start.head === now.head) return { changed: false, now };
  if (!start.head || !now.head) return { changed: true, now };
  const diff = await git(place, ['diff', '--name-only', start.head, now.head, ...EXCLUDE]);
  return { changed: diff === null ? true : diff.trim() !== '', now };
}

module.exports = { stateOf, changed, parseStatus, MAX_FILES };
