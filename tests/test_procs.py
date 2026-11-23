"""收拾整棵进程树（core/procs.py）：超时、被叫停时，连同自己开了新会话的孙子进程一起停，不留孤儿。"""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from relayforge.core import procs  # noqa: E402

BACKEND = str(Path(__file__).resolve().parent.parent / 'backend')

# 一个会躲的孙子：自己开新会话（像 Claude Code 的 Bash、准备步骤里的服务），把 pid 写下来，然后一直睡
HIDER = '''
import os, subprocess, sys, time
kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
open(sys.argv[1], "w").write(str(kid.pid))
time.sleep(120)
'''


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # 已经死了、还没被收尸的进程也算没了
    out = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith('Z')


def wait_gone(pid, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return False


def wait_file(path, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError(f'{path} 没有写出来')


@unittest.skipUnless(os.name == 'posix', '只在 macOS 和 Linux 上收拾进程树')
class TreeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name).resolve()
        self.pidfile = self.dir / 'kid.pid'
        self.script = self.dir / 'hider.py'
        self.script.write_text(HIDER, encoding='utf-8')

    def tearDown(self):
        if self.pidfile.exists() and self.pidfile.read_text().strip():
            try:
                os.kill(int(self.pidfile.read_text()), signal.SIGKILL)
            except OSError:
                pass

    def test_a_timeout_takes_down_a_grandchild_in_its_own_session(self):
        # shell=True 的命令（构建、准备步骤）：sh → python → 自己开会话的孙子
        code, _, err = procs.run(f'{sys.executable} {self.script} {self.pidfile}; exit $?', shell=True, timeout=3)
        self.assertIsNone(code, err)
        kid = wait_file(self.pidfile)
        self.assertTrue(wait_gone(kid), '孙子进程还活着')

    def test_a_normal_run_gives_the_output_and_code(self):
        code, out, err = procs.run([sys.executable, '-c', 'import sys; print("hi"); sys.exit(3)'], timeout=30, text=True)
        self.assertEqual((code, out.strip()), (3, 'hi'))
        code, out, _ = procs.run([sys.executable, '-c', 'import sys; print(sys.stdin.read().upper())'], input='abc', text=True, timeout=30)
        self.assertEqual((code, out.strip()), (0, 'ABC'))

    def test_rf_asked_to_stop_takes_what_it_waits_for_down_with_it(self):
        # rf.py 收到应用的 SIGTERM：stop_on_signals 先收拾正在等的命令（连同孙子），再按 SIGTERM 退出
        waiter = self.dir / 'waiter.py'
        waiter.write_text(f'''
import sys
sys.path.insert(0, {BACKEND!r})
from relayforge.core import procs
procs.stop_on_signals()
procs.run([sys.executable, {str(self.script)!r}, {str(self.pidfile)!r}], timeout=120)
''', encoding='utf-8')
        rf = subprocess.Popen([sys.executable, str(waiter)])
        kid = wait_file(self.pidfile)
        rf.send_signal(signal.SIGTERM)
        self.assertEqual(rf.wait(timeout=20), -signal.SIGTERM)
        self.assertTrue(wait_gone(kid), '孙子进程还活着')

    def test_kill_tree_finds_children_whose_parent_already_left_the_group(self):
        top = subprocess.Popen([sys.executable, str(self.script), str(self.pidfile)], start_new_session=True)
        kid = wait_file(self.pidfile)
        self.assertIn(kid, procs.descendants(top.pid))
        procs.kill_tree(top.pid)
        top.wait(timeout=10)
        self.assertTrue(wait_gone(kid), '孙子进程还活着')

    def test_a_limit_keeps_only_that_much_of_endless_output(self):
        # 失控的模型一直打印：输出先进临时文件，只读回前 limit 个字节，内存不跟着涨
        code, out, err = procs.run([sys.executable, '-c', 'import sys\nfor _ in range(4000): sys.stdout.write("x" * 4096)\nprint("end", file=sys.stderr)'],
                                   limit=1000, text=True, timeout=60)
        self.assertEqual((code, len(out), out[:3], err.strip()), (0, 1000, 'xxx', 'end'))
        code, out, _ = procs.run([sys.executable, '-c', 'import sys; print(sys.stdin.read())'], input='问一句', limit=100, text=True, timeout=30)
        self.assertEqual((code, out.strip()), (0, '问一句'))

    def test_it_never_kills_itself_or_init(self):
        procs.kill_tree(os.getpid())
        procs.kill_tree(1)
        self.assertTrue(alive(os.getpid()))


if __name__ == '__main__':
    unittest.main()
