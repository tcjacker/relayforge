"""收拾整棵进程树：超时、被打断时，连同所有后代一起停掉，不留孤儿进程。

只杀直接子进程不够，进程组也不够：
  - shell=True 的命令（构建、准备步骤）超时时只杀 sh，mvn、gradle、java 继续跑；
  - Claude Code 的 Bash 工具、准备步骤自己开的新会话（start_new_session）不在我们的进程组里，killpg 收不到；
  - 父进程先死，孩子就挂到 launchd（1）下面，按父子关系再也找不回来。
所以这里先按父子关系把整棵树找出来、全部冻住（SIGSTOP，不让它们再生孩子、也不让死了的父亲把孩子交给 launchd），
再找一遍补上漏网的，最后一起 SIGKILL。每个后代所在的进程组也一并杀掉（不杀我们自己所在的那一组）。

run() 代替 subprocess.run / Popen + communicate：子进程自成一个会话；超时、或者等待时被打断（Ctrl-C、rf.py 收到 SIGTERM），
整棵树一起停。rf.py 自己被 SIGKILL 时这里来不及做什么，那由启动它的应用按整棵树收拾（app/src/main/proc-tree.js）。
"""

import os
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

__all__ = ['descendants', 'kill_tree', 'run', 'kill_all_running', 'stop_on_signals']

_running = set()  # 正在等的子进程的 pid：rf.py 收到 SIGTERM 时一起收拾
_lock = threading.Lock()


def _table():
    """{pid: parent pid} of every process we can see, or {} when it cannot be read."""
    if Path('/proc/self/stat').exists():
        out = {}
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit():
                try:
                    stat = (entry / 'stat').read_text()
                    out[int(entry.name)] = int(stat[stat.rindex(')') + 2:].split()[1])
                except (OSError, ValueError, IndexError):
                    continue
        return out
    try:
        text = subprocess.run(['ps', '-axo', 'pid=,ppid='], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            out[int(parts[0])] = int(parts[1])
    return out


def descendants(pid, table=None):
    """Every process below `pid` (children, grandchildren …), parents before children."""
    table = _table() if table is None else table
    kids = {}
    for child, parent in table.items():
        kids.setdefault(parent, []).append(child)
    out, queue = [], [int(pid)]
    while queue:
        for child in kids.get(queue.pop(0), []):
            if child not in out and child != pid:
                out.append(child)
                queue.append(child)
    return out


def _signal(pids, sig):
    for p in pids:
        try:
            os.kill(p, sig)
        except OSError:
            pass


def kill_tree(pid, rounds=3):
    """Stop `pid` and everything below it, including what left our process group or started a session of its own."""
    pid = int(pid)
    if pid <= 1 or pid == os.getpid():
        return
    ours = os.getpgrp() if hasattr(os, 'getpgrp') else None
    seen = []
    stop = getattr(signal, 'SIGSTOP', None)
    for _ in range(rounds):  # 冻住一批再找一遍：冻住之前刚生出来的孩子下一轮补上
        fresh = [p for p in [pid, *descendants(pid)] if p not in seen]
        if not fresh:
            break
        seen += fresh
        if stop:
            _signal(fresh, stop)
    groups = set()
    if hasattr(os, 'getpgid'):
        for p in seen:
            try:
                g = os.getpgid(p)
            except OSError:
                continue
            if g > 1 and g != ours:
                groups.add(g)
    kill = getattr(signal, 'SIGKILL', signal.SIGTERM)
    _signal(seen, kill)
    for g in groups:
        try:
            os.killpg(g, kill)
        except OSError:
            pass
    cont = getattr(signal, 'SIGCONT', None)
    if cont:  # SIGKILL 对冻住的进程也有效；这里只是保险
        _signal(seen, cont)


def run(command, timeout=None, input=None, limit=None, **kwargs):
    """Like subprocess.run(capture_output=True): (returncode, stdout, stderr), returncode None when it ran out of time.
    The command runs in a session of its own; on a timeout or an interruption its whole tree is stopped.
    limit: keep at most this many bytes of each output. The output goes to a temporary file rather than into memory, so a
    command that prints without end (a model gone wrong) cannot fill the memory; only the first `limit` bytes are read back."""
    if limit is not None:
        text = bool(kwargs.pop('text', False))
        with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
            data = input.encode('utf-8') if text and isinstance(input, str) else input
            code, _, _ = run(command, timeout, data, stdout=out_file, stderr=err_file, **kwargs)
            out, err = (_head(f, limit) for f in (out_file, err_file))
        if text:
            out, err = out.decode('utf-8', errors='replace'), err.decode('utf-8', errors='replace')
        return code, out, err
    kwargs.setdefault('stdout', subprocess.PIPE)
    kwargs.setdefault('stderr', subprocess.PIPE)
    if input is not None:
        kwargs['stdin'] = subprocess.PIPE
    else:
        kwargs.setdefault('stdin', subprocess.DEVNULL)
    if os.name == 'posix':
        kwargs['start_new_session'] = True
    proc = subprocess.Popen(command, **kwargs)
    with _lock:
        _running.add(proc.pid)
    try:
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
            out, err = _drain(proc)
            return None, out, err
        except BaseException:
            kill_tree(proc.pid)
            _drain(proc)
            raise
        return proc.returncode, out, err
    finally:
        with _lock:
            _running.discard(proc.pid)


def _head(handle, limit):
    handle.seek(0)
    return handle.read(limit)


def _drain(proc):
    """What the killed process left in its pipes; never waits long (a grandchild that escaped could hold them open)."""
    try:
        return proc.communicate(timeout=5)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        empty = '' if getattr(proc, 'text_mode', False) or getattr(proc, 'universal_newlines', False) else b''
        return empty, empty


def kill_all_running():
    with _lock:
        pids = list(_running)
    for p in pids:
        kill_tree(p)


def stop_on_signals():
    """rf.py asked to stop (SIGTERM from the app, SIGHUP when its terminal closes): take the commands it is waiting for down
    with it, then exit as the signal would have."""
    def handler(signum, _frame):
        kill_all_running()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    for name in ('SIGTERM', 'SIGHUP'):
        sig = getattr(signal, name, None)
        if sig is None or threading.current_thread() is not threading.main_thread():
            continue
        try:
            if signal.getsignal(sig) in (signal.SIG_DFL, None):
                signal.signal(sig, handler)
        except (OSError, ValueError):
            pass
