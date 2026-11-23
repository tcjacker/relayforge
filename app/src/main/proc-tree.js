// 收拾整棵进程树（和 backend/relayforge/core/procs.py 同一个做法）：应用叫停 rf.py、关会话、取消登录时，连同所有后代一起停。
// 只杀直接子进程不够：rf.py 被 SIGKILL 时它启动的驱动、构建、准备步骤会留下；Claude Code 的 Bash 工具、准备步骤自己开新会话，
// 不在我们的进程组里；父进程一死，孩子挂到 launchd 下面就找不回来了。所以先按父子关系找出整棵树、全部冻住（SIGSTOP），
// 再找一遍补上漏网的，最后一起 SIGKILL，每个后代所在的进程组也一并杀掉（不杀应用自己那一组）。
const { execFileSync } = require('child_process');
const fs = require('fs');

function table() {
  const out = new Map();
  try {
    if (fs.existsSync('/proc/self/stat')) {
      for (const name of fs.readdirSync('/proc')) {
        if (!/^\d+$/.test(name)) continue;
        try {
          const stat = fs.readFileSync(`/proc/${name}/stat`, 'utf8');
          out.set(Number(name), Number(stat.slice(stat.lastIndexOf(')') + 2).split(' ')[1]));
        } catch (_) { /* 刚退出 */ }
      }
      return out;
    }
    const text = execFileSync('ps', ['-axo', 'pid=,ppid='], { encoding: 'utf8', timeout: 10000 });
    for (const line of text.split('\n')) {
      const [pid, ppid] = line.trim().split(/\s+/).map(Number);
      if (pid && Number.isInteger(ppid)) out.set(pid, ppid);
    }
  } catch (_) { /* 读不了进程表：只能杀直接的那一个 */ }
  return out;
}

// pid 下面所有的进程（孩子、孙子……），父亲在前
function descendants(pid, tab = table()) {
  const kids = new Map();
  for (const [child, parent] of tab) { if (!kids.has(parent)) kids.set(parent, []); kids.get(parent).push(child); }
  const out = [];
  const queue = [pid];
  while (queue.length) for (const child of kids.get(queue.shift()) || []) if (child !== pid && !out.includes(child)) { out.push(child); queue.push(child); }
  return out;
}

const send = (pids, sig) => { for (const p of pids) { try { process.kill(p, sig); } catch (_) { /* 已经没了 */ } } };

// 停掉 pid 和它下面的一切
function killTree(pid, rounds = 3) {
  pid = Number(pid);
  if (!pid || pid <= 1 || pid === process.pid) return [];
  const seen = [];
  for (let i = 0; i < rounds; i++) {
    const fresh = [pid, ...descendants(pid)].filter((p) => !seen.includes(p));
    if (!fresh.length) break;
    seen.push(...fresh);
    if (process.platform !== 'win32') send(fresh, 'SIGSTOP');
  }
  send(seen, 'SIGKILL');
  if (process.platform !== 'win32') {
    // 各自开了新进程组的也一起：组号就是组长的 pid，杀 -组号；应用自己那一组不在 seen 里（应用不是谁的后代）
    for (const p of seen) { try { process.kill(-p, 'SIGKILL'); } catch (_) { /* 不是组长 */ } }
    send(seen, 'SIGCONT');
  }
  return seen;
}

module.exports = { killTree, descendants, table };
