"""是不是用户在应用里点的：应用自己调用 rf.py 时带一个只有应用知道的标记，没有这个标记的一律按 AI 的会话处理。

  应用启动时生成一个随机标记，只放在它自己调用 rf.py 的那个子进程的环境变量 RELAYFORGE_APP_TOKEN 里（不放进它开的会话的环境），
  并在 $RELAYFORGE_HOME/apps/<应用主进程的 pid>.json（600）里只留这个标记的 SHA-256。rf.py 认可的条件是两样都对得上：
    1. 环境变量里的标记，散列后和登记文件里的一样；
    2. 登记文件属于 rf.py 的父进程（应用直接启动它）。父进程是一个 python 外壳（比如版本管理工具的 shim）时，再往上看一层。
  AI 的会话就算读到了登记文件（只有散列）、或者从别的进程那里拿到了标记，它启动的 rf.py 的父进程是它自己的 shell，不是应用，所以不算。
  但登记文件只是一个普通文件：会话和你是同一个系统用户，能给自己的 shell 写一份登记、自选一个标记（还能用 RELAYFORGE_HOME
  把登记目录指到别处），上面两条就都对得上了。真正挡住它的是系统的沙箱：应用开的 Claude Code 会话和 Codex 会话里的命令
  都在 macOS 的沙箱（Seatbelt）里运行，沙箱里启动的进程一直在沙箱里，跳不出来；应用自己不在沙箱里。所以还有第 3 条：
    3. 这个 rf.py 不在沙箱里（sandboxed()）。在沙箱里就一定不是应用直接启动的，登记再像也不算。

用到它的地方（见 FORMAT.md 的「测试环境」）：运行测试集和准备步骤时带不带保密值、准备步骤的「同意执行」、「允许 AI 的会话带上保密值」的开关、取明文（env reveal）。

只由用户触发的命令（USER_COMMANDS，见 FORMAT.md 的「只由用户触发的命令」）：rf.py 在执行前自己校验（user_gate），不只靠会话的权限规则。
  - 有应用的标记：照常执行；
  - 没有标记、但是人在终端里直接运行（标准输入和输出都是终端，并且不在任何 AI 会话里，见 ai_session_reason）：允许，
    变更记录里这一行注明「用户在终端里执行」；
  - 其余（AI 的会话里，或者在脚本、管道里）一律拒绝：「这个操作只能由你在应用里做」。
有几条只认应用的标记、终端也不行（APP_ONLY）：同意执行命令、带保密值、取明文这类，要你在应用里看过实际的内容再点。
"""

import contextvars
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .common import relayforge_home
from .guard import hint_for

TOKEN_ENV = 'RELAYFORGE_APP_TOKEN'


def apps_dir():
    return relayforge_home() / 'apps'


def _proc_parent(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
        return int(stat[stat.rindex(')') + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(['ps', '-o', 'ppid=', '-p', str(pid)], capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _proc_name(pid):
    try:
        return Path(f'/proc/{pid}/comm').read_text().strip()
    except OSError:
        pass
    try:
        return os.path.basename(subprocess.run(['ps', '-o', 'comm=', '-p', str(pid)], capture_output=True, text=True, timeout=5).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return ''


def _registered(pid, digest):
    try:
        data = json.loads((apps_dir() / f'{int(pid)}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(data, dict) and hmac.compare_digest(str(data.get('token_sha256') or ''), digest)


def parent_pid():
    """The process that started this rf.py. A module-level function on purpose: tests replace it with a Python mock (an object in the
    test's own process), never through the environment or a file, so nothing a session can set from outside changes who the parent is."""
    return os.getppid()


def sandboxed():
    """True when this process runs inside a macOS sandbox (Seatbelt), the one Claude Code and Codex put session commands in.
    A module-level function so tests can replace it with a Python mock, like parent_pid. Elsewhere than macOS: False (no check)."""
    if sys.platform != 'darwin':
        return False
    try:
        import ctypes
        check = ctypes.CDLL('/usr/lib/libSystem.dylib').sandbox_check
        check.restype = ctypes.c_int
        check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        return check(os.getpid(), None, 0) != 0  # 不问具体操作：只问这个进程有没有沙箱
    except (OSError, AttributeError):
        return False


def app_trusted():
    """True only when this rf.py was started by the RelayForge app itself (see the module docstring)."""
    token = os.environ.get(TOKEN_ENV) or ''
    if len(token) < 16:
        return False
    if sandboxed():
        return False
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    pid = parent_pid()
    for _ in range(2):
        if not pid or pid <= 1:
            return False
        if _registered(pid, digest):
            return True
        if not _proc_name(pid).lower().startswith('python'):
            return False
        pid = _proc_parent(pid)
    return False


def require_app(what):
    if not app_trusted():
        raise ValueError(f'{what}：这个操作只能由你在应用里做，AI 的会话不能代替你做（没有应用的标记）。')


def register(pid, token):
    """For tests and the app's own use: register `token` for process `pid`."""
    d = apps_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = d / f'{int(pid)}.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))  # keep what else is registered for this process (the session socket, identity.py)
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    data.update(pid=int(pid), token_sha256=hashlib.sha256(token.encode('utf-8')).hexdigest())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    return path


def scrubbed_environ(extra=None):
    """The environment for commands RelayForge starts on the user's behalf (drivers, preparation steps): never the app's marker."""
    env = dict(os.environ, **{str(k): str(v) for k, v in (extra or {}).items()})
    env.pop(TOKEN_ENV, None)
    return env


# ---------- 是不是在 AI 的会话里 ----------

# Claude Code、Codex 和 RelayForge 自己开会话时放进环境的变量；值非空就算在会话里（应用自己调用 rf.py 时把 CLAUDE_CODE_SESSION_ID 置空）
AI_ENV_VARS = ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_CHILD_SESSION', 'CLAUDE_PROJECT_DIR',
               'CODEX_THREAD_ID', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE',
               'RELAYFORGE_SESSION_ID', 'RELAYFORGE_SESSION_SIG')
# 上层进程里出现这些程序就算在会话里：claude、codex 本身，Node 跑的 Claude Code / Codex，RelayForge 开会话用的 pty_bridge.py
AI_PROGRAMS = {'claude', 'codex', 'claude-code', 'codex-cli'}
AI_ARGS = re.compile(r'(@anthropic-ai/claude-code|claude-code/cli\.(?:m?js)|@openai/codex|pty_bridge\.py)')
CHAIN_DEPTH = 40


def _proc_cmdline(pid):
    try:
        raw = Path(f'/proc/{pid}/cmdline').read_bytes()
        if raw:
            return [x.decode('utf-8', 'replace') for x in raw.split(b'\0') if x]
    except OSError:
        pass
    return None


def process_chain(start=None, depth=CHAIN_DEPTH):
    """[(pid, command line)] from `start` (default: this process) up through its ancestors, as far as they can be read.
    Linux reads /proc; elsewhere (macOS) one `ps` call lists every process and the chain is walked in it."""
    pid = int(start or os.getpid())
    out = []
    if Path('/proc/self/stat').exists():
        while pid and pid > 0 and len(out) < depth:
            cmd = _proc_cmdline(pid)
            out.append((pid, ' '.join(cmd) if cmd else _proc_name(pid)))
            parent = _proc_parent(pid)
            if not parent or parent == pid:
                break
            pid = parent
        return out
    try:
        text = subprocess.run(['ps', '-axo', 'pid=,ppid=,command='], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        text = ''
    if not text.strip():
        return _sysctl_chain(pid, depth) or [(pid, '')]
    table = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else '')
    while pid and pid > 0 and len(out) < depth:
        parent, cmd = table.get(pid, (None, ''))
        out.append((pid, cmd))
        if not parent or parent == pid:
            break
        pid = parent
    return out


def _kinfo(pid):
    """(parent pid, short name) of a process from the kernel (macOS sysctl kern.proc.pid), or None."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 4)(1, 14, 1, int(pid))  # CTL_KERN, KERN_PROC, KERN_PROC_PID
        buf = ctypes.create_string_buffer(_KINFO_SIZE)
        size = ctypes.c_size_t(_KINFO_SIZE)
        if libc.sysctl(mib, 4, buf, ctypes.byref(size), None, 0) != 0 or size.value < _KINFO_SIZE:
            return None
        raw = buf.raw
    except (OSError, AttributeError, ValueError):
        return None
    ppid = int.from_bytes(raw[_PPID_AT:_PPID_AT + 4], sys.byteorder, signed=True)
    name = raw[_COMM_AT:_COMM_AT + 17].split(b'\0', 1)[0].decode('utf-8', 'replace')
    return ppid, name


# struct kinfo_proc on 64-bit macOS: 648 bytes; kp_proc.p_comm at 243, kp_eproc.e_ppid at 560
_KINFO_SIZE, _COMM_AT, _PPID_AT = 648, 243, 560


def _sysctl_chain(pid, depth):
    """The chain read from the kernel when `ps` cannot run: Codex's sandbox (seatbelt) forbids running setuid programs such as ps,
    but lets a process read kern.proc.pid.* of others. Only the short program name is known here, not the full command line."""
    if sys.platform != 'darwin':
        return []
    out = []
    while pid and pid > 0 and len(out) < depth:
        got = _kinfo(pid)
        if not got:
            break
        parent, name = got
        out.append((pid, name))
        if not parent or parent == pid:
            break
        pid = parent
    return out


def _ai_command(cmd):
    cmd = str(cmd or '')
    if AI_ARGS.search(cmd):
        return True
    first = cmd.split(None, 1)[0] if cmd.strip() else ''
    return os.path.basename(first).lower() in AI_PROGRAMS


def ai_session_reason(environ=None, chain=None):
    """Why this process counts as running inside an AI session (Claude Code, Codex, a session RelayForge opened), or None.
    Looks at the session variables in the environment and at every ancestor process. The one place that decides it."""
    env = os.environ if environ is None else environ
    for name in AI_ENV_VARS:
        if str(env.get(name) or '').strip():
            return f'环境里有会话的变量 {name}'
    for pid, cmd in (process_chain() if chain is None else chain):
        if _ai_command(cmd):
            return f'上层进程 {pid} 是 AI 的会话（{str(cmd)[:80]}）'
    if chain is None and sandboxed():  # 改环境变量、脱离上层进程都摆脱不了沙箱
        return '在 AI 的会话的沙箱里运行'
    return None


def in_ai_session(environ=None, chain=None):
    return ai_session_reason(environ, chain) is not None


def _isatty(stream):
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def human_terminal(environ=None, chain=None, stdin=None, stdout=None):
    """True when a person typed this command in a terminal: standard input and output are both a terminal, and nothing says
    it runs inside an AI session. Scripts, pipes, git hooks and AI sessions are not."""
    if not (_isatty(sys.stdin if stdin is None else stdin) and _isatty(sys.stdout if stdout is None else stdout)):
        return False
    return not in_ai_session(environ, chain)


# ---------- 只由用户触发的命令 ----------

# (命令, 动作)：只由用户触发。有应用的标记照常执行；人在终端里直接运行也允许（变更记录注明）；AI 的会话里一律拒绝。
USER_COMMANDS = {
    ('req', 'start'): '确认立项', ('req', 'done'): '确认完成', ('req', 'drop'): '放弃需求', ('req', 'set'): '手动改需求的状态', ('req', 'depend'): '设置前置需求', ('req', 'keep'): '保留已取消需求的分支', ('req', 'restore'): '恢复已取消的需求', ('req', 'choose'): '改开发会话或测试会话的 CLI 和模型',
    ('req', 'to-test'): '交给测试', ('req', 'skip-test'): '跳过测试', ('req', 'accept'): '人工验收的逐条通过或不对',
    ('req', 'case-ok'): '同意用例的改动', ('req', 'case-no'): '不同意用例的改动', ('req', 'case-save'): '以你的身份改测试用例',
    ('issue', 'confirm'): '确认问题修好了', ('issue', 'unconfirm'): '撤回确认', ('issue', 'reject'): '退回修复（还有问题）', ('issue', 'probe'): '交给 AI 排查',
    ('issue', 'fix'): '开始修复', ('issue', 'set'): '改问题的设置', ('issue', 'reply'): '回复问题', ('issue', 'to-req'): '问题转为需求', ('issue', 'from-req'): '需求转为问题',
    ('issue', 'review'): '让 AI 复查待处理问题', ('issue', 'review-discard'): '关掉复查结果',
    ('test', 'approve'): '同意测试数据的改动', ('test', 'reject'): '不同意测试数据的改动', ('test', 'stale'): '把测试集标成过期', ('test', 'revive'): '恢复过期的测试集',
    ('test', 'move'): '改测试集的挂靠', ('test', 'save'): '以你的身份保存测试数据', ('test', 'seen'): '把 AI 新加的数据标为看过了', ('test', 'config'): '改测试集的设置',
    ('env', 'new'): '新建测试环境', ('env', 'copy'): '复制测试环境', ('env', 'rename'): '给测试环境改名', ('env', 'rm'): '删除测试环境', ('env', 'save'): '保存测试环境',
    ('env', 'use'): '选用测试环境', ('env', 'user'): '选当前测试用户', ('env', 'adopt'): '采纳 AI 的建议', ('env', 'ignore'): '忽略 AI 的建议',
    ('publish', 'run'): '发布（推送到远端）', ('publish', 'pull'): '拉取远端的新提交',
    ('branch', 'refresh'): '刷新远端分支', ('branch', 'switch'): '切换项目主目录的分支',
    ('map', 'trunk'): '把用户旅程设为主干或取消',
    ('fresh', 'adopt'): '采纳功能点核对的建议', ('fresh', 'ignore'): '忽略功能点核对的建议', ('fresh', 'done'): '把功能点标成核对完',
    ('data', 'import'): '从导出的包恢复项目记录', ('data', 'migrate'): '把旧项目的记录迁到本机',
    ('summarize', None): '给完成的需求写总结', ('archive', None): '补存会话记录',
}
# 只认应用的标记（终端也不行）：要你在应用里看过实际的命令或内容再点，或者是应用自己的记账
APP_ONLY = {
    ('issue', 'review-apply'): '确认批量复查所选结果',
    ('env', 'approve'): '同意执行准备步骤和启动命令', ('env', 'ai-secrets'): '允许 AI 的会话带上保密值', ('env', 'reveal'): '取保密值的明文',
    ('env', 'launch'): '启动验收用的服务', ('env', 'accept-clean'): '清掉验收的临时目录和项目副本',
    ('test', 'approve-build'): '同意执行测试集的构建命令',
    ('req', 'test-kicked'): '记下测试会话开始了第几轮', ('req', 'test-told'): '记下第几轮的结论交出去了',
    ('split', None): '确认拆分需求',
    ('subagent', 'decision'): '读取立项对话中的 subagent 授权',
}
# 刻意不校验的（见 FORMAT.md）：rf-confirm 技能在你明确说了以后由 AI 执行的 confirm / reject（确认、否决待确认的条目）
UNCHECKED_ON_PURPOSE = {('confirm', None): 'rf-confirm 技能：你明确表态以后由 AI 执行', ('reject', None): 'rf-confirm 技能：你明确表态以后由 AI 执行'}

_ACTOR = contextvars.ContextVar('relayforge_actor', default=None)
TERMINAL_NOTE = '（用户在终端里执行）'


def command_key(cmd, action=None):
    """The key of a command in USER_COMMANDS / APP_ONLY, or None when it is not one of them."""
    for key in ((cmd, action), (cmd, None)):
        if key in USER_COMMANDS or key in APP_ONLY:
            return key
    return None


def user_gate(what, app_only=False, environ=None, chain=None, stdin=None, stdout=None, key=None):
    """'app' or 'terminal' when the user may do `what` here; else raises ValueError with the reason. See the module docstring.
    key: the command's key in USER_COMMANDS / APP_ONLY; the refusal then also says what a session should do instead (guard.HINTS)."""
    if app_trusted():
        return 'app'
    if not app_only and human_terminal(environ, chain, stdin, stdout):
        return 'terminal'
    why = ai_session_reason(environ, chain)
    tail = (f'（这条命令是在 AI 的会话里运行的：{why}。）' if why else
            '（没有应用的标记' + ('' if app_only else '，也不是你在终端里直接敲的') + '。）')
    hint = hint_for(key)
    raise ValueError(f'{what}：这个操作只能由你在应用里做，AI 的会话不能代替你做。{tail}' + (f'\n如果你是 AI 的会话：{hint}' if hint else ''))


def gate_command(cmd, action=None):
    """Check a command line before it runs: returns (actor or None, key). None for commands that are not the user's."""
    key = command_key(cmd, action)
    if key is None:
        return None, None
    if key in APP_ONLY:
        return user_gate(APP_ONLY[key], app_only=True, key=key), key
    return user_gate(USER_COMMANDS[key], key=key), key


def current_actor():
    """'app' or 'terminal' while a user command checked by gate_command runs (set by the command line), else None."""
    return _ACTOR.get()


def set_actor(actor):
    return _ACTOR.set(actor)


def reset_actor(handle):
    _ACTOR.reset(handle)


def user_trusted(trusted=None):
    """For the functions behind the user's commands: `trusted` when the caller says (tests, the app's own chains of calls),
    else whether the command line checked it (current_actor), else whether the app started this process."""
    if trusted is not None:
        return bool(trusted)
    return current_actor() in ('app', 'terminal') or app_trusted()


def terminal_note(line):
    """A change-log line written while the user runs a command in a terminal says so (before the source in 〔〕 at the end)."""
    if current_actor() != 'terminal' or TERMINAL_NOTE in line:
        return line
    m = re.search(r'(〔[^〕]*〕)$', line)
    return line[:m.start()] + TERMINAL_NOTE + m.group(1) if m else line + TERMINAL_NOTE
