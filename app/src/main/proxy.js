// 会话经应用代写记录（见 backend/relayforge/core/proxy.py）：数据外置以后记录在项目目录之外，Codex 的沙箱写不进去；
// 会话里的 rf.py 把命令经 unix socket 交给应用（session-token.js 的 run），这里核对命令、在会话的工作目录里执行、交回输出。
// 执行的 rf.py 环境里带着这个会话的令牌、没有应用的标记：身份还是这个会话，「只由用户触发」的命令照样被拒绝。
const { spawn } = require('child_process');
const fs = require('fs');

// 和 backend/relayforge/core/proxy.py 的 PROXIED 保持一致：只有记录类的写命令，不含会执行别的程序的命令（运行测试集、准备步骤）
const PROXIED = {
  note: null,
  req: ['attach', 'classify', 'steps', 'verdict', 'retest', 'case-add', 'case-edit', 'case-rm'],
  issue: ['new', 'attach', 'handback', 'finding'],
  fresh: ['propose'], // 功能点的核对会话交回建议（backend/relayforge/facts/fresh.py）
  design: ['save', 'skip'],
  test: ['new', 'add', 'edit', 'rm', 'analyze'],
  // ai-values 是读：会话的沙箱读不了保密的值，你允许了「AI 的会话带上保密值」时，运行前经应用取（backend/relayforge/envs/values.py）
  env: ['propose', 'ai-values'],
};
const LIMIT = 4 << 20;
const TIMEOUT = 170 * 1000;
const SCRUB = ['CLAUDE_CODE_CHILD_SESSION', 'CLAUDE_CODE_SESSION_ID', 'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_PROJECT_DIR',
  'CODEX_THREAD_ID', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE', 'RELAYFORGE_APP_TOKEN', 'RELAYFORGE_SESSION_SIG',
  'RELAYFORGE_PROXY_CODEX_TRANSCRIPT'];

function allowed(argv) {
  if (!Array.isArray(argv) || !argv.length || argv.some((a) => typeof a !== 'string')) return false;
  if (!Object.prototype.hasOwnProperty.call(PROXIED, argv[0])) return false;
  const actions = PROXIED[argv[0]];
  return actions === null || actions.includes(argv[1]);
}

// --root 由应用定（会话的工作目录），会话给的不算
function withoutRoot(argv) {
  const out = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--root') { i++; continue; }
    if (argv[i].startsWith('--root=')) continue;
    out.push(argv[i]);
  }
  return out;
}

// python、rf.py 由调用方给（应用里是 config 的 PYTHON 和自带的 rf.py）；测试里可以换
function runner({ python, script, env = process.env, codex }) {
  return ({ token, claims, argv, stdin, place, started, ended }) => {
    if (!allowed(argv)) return { ok: false, error: `这条命令不能交给应用代写：${(argv || []).slice(0, 2).join(' ')}` };
    if (!place || !fs.existsSync(place)) return { ok: false, error: '找不到这个会话的工作目录' };
    const childEnv = { ...env };
    for (const name of SCRUB) delete childEnv[name];
    Object.assign(childEnv, { RELAYFORGE_SESSION_SIG: token, RELAYFORGE_SESSION_ID: String(claims.sid || ''), RELAYFORGE_PROXIED: '1' });
    // 用令牌里的会话编号查应用自己的登记。空值表示这是 Codex 会话，但它的对话文件还没找到。
    if (codex?.has(claims.sid)) childEnv.RELAYFORGE_PROXY_CODEX_TRANSCRIPT = codex.find(claims.sid) || '';
    return new Promise((resolve) => {
      let child;
      try { child = spawn(python, [script, ...withoutRoot(argv)], { cwd: place, env: childEnv, stdio: ['pipe', 'pipe', 'pipe'] }); }
      catch (error) { resolve({ ok: false, error: String(error.message || error) }); return; }
      if (child.pid) started(child.pid); // 同一轮事件里记下：它问身份时一定已经算数
      let out = '', err = '';
      const take = (which) => (chunk) => { if (which === 'out' && out.length < LIMIT) out += chunk; if (which === 'err' && err.length < LIMIT) err += chunk; };
      child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
      child.stdout.on('data', take('out')); child.stderr.on('data', take('err'));
      const timer = setTimeout(() => { if (child.pid) require('./proc-tree').killTree(child.pid); else child.kill('SIGKILL'); }, TIMEOUT); // 连同后代一起停
      child.on('error', (error) => { clearTimeout(timer); if (child.pid) ended(child.pid); resolve({ ok: false, error: String(error.message || error) }); });
      child.on('close', (code) => { clearTimeout(timer); if (child.pid) ended(child.pid); resolve({ ok: true, code: code == null ? 1 : code, stdout: out, stderr: err }); });
      child.stdin.on('error', () => {});
      child.stdin.end(typeof stdin === 'string' ? stdin : undefined);
    });
  };
}

module.exports = { PROXIED, allowed, withoutRoot, runner };
