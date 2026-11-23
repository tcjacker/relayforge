"""Real HTTP / Git / exec reload with a deterministic executor and isolated files."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import urlopen

import seed


class ReloadTests(unittest.TestCase):
    def test_reload_waits_for_post_response_and_keeps_original_arguments(self):
        server = SimpleNamespace(boot='old', pending_posts=1)
        with patch.object(seed.engine, 'git', return_value='new'), \
             patch.object(seed.os, 'execv') as execute, patch.object(seed.sys, 'argv', ['seed.py', '--port', '9876']):
            self.assertFalse(seed.restart_if_idle(server))
            execute.assert_not_called()
            server.pending_posts = 0
            self.assertTrue(seed.restart_if_idle(server))
            args = execute.call_args.args[1]
            self.assertEqual(args[-2:], ['--port', '9876'])
            self.assertIn('-B', args)
        with patch.object(seed.engine, 'git', return_value='old'), patch.object(seed.os, 'execv') as execute:
            self.assertFalse(seed.restart_if_idle(server))
            execute.assert_not_called()

    def test_active_execution_finishes_then_new_version_continues_same_service(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(Path(seed.__file__), root / 'seed.py')
            (root / 'seed_engine.py').write_text('''
from pathlib import Path
import subprocess,json,os,time
ROOT=Path(__file__).parent
RUNTIME=ROOT/'.seed'
def git(cwd,*args):
    return subprocess.check_output(['git',*args],cwd=cwd,text=True).strip()
def advance(task,rows,boot):
    if task=='first':
        (ROOT/'.probe-running').touch()
        while not (ROOT/'.probe-release').exists(): time.sleep(.02)
        (ROOT/'.probe-first-done').write_text(boot)
    else:
        (ROOT/'.probe-next-done').write_text(json.dumps({'base':boot,'pid':os.getpid(),
            'environment':os.environ.get('SEED_RELOAD_TEST_ENV')}))
''')
            (root / 'seed_store.py').write_text('''
from pathlib import Path
ROOT=Path(__file__).parent
def init(): pass
def stage_get(): return None
def stage_recover(): pass
def journal():
    if not (ROOT/'.probe-first-done').exists():
        return {'first':[{'seq':1,'state':'queued','data':{}}]}
    if not (ROOT/'.probe-next-done').exists():
        return {'next':[{'seq':1,'state':'queued','data':{}}]}
    return {}
''')
            (root / 'seed_stage.py').write_text("def select_task(jobs,stage): return next(iter(jobs.items()),None)\n")
            (root / 'seed_stage_web.py').write_text('')
            # Preserve the original baseline's minimal domain/UI contracts. No web
            # adapter is copied: coordinator/reload must not depend on it.
            (root / 'seed_project.py').write_text('class Project:\n    def recover_publications(self): pass\n')
            (root / 'seed_ui.py').write_text('''
import os
def revision(jobs,stage,current,boot=None):
    return str(os.getpid())+':'+str(boot)+':'+os.environ['SEED_RELOAD_TEST_ENV']
''')
            (root / '.gitignore').write_text('.seed/\n.probe*\n__pycache__/\n')
            (root / 'version.txt').write_text('one')
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
            git('init', '-b', 'main')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@local', 'commit', '-m', 'one')
            old = git('rev-parse', 'HEAD')
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            process = subprocess.Popen([sys.executable, '-B', str(root / 'seed.py'), '--port', str(port)],
                cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env={**os.environ, 'SEED_RELOAD_TEST_ENV': 'preserved'})
            def wait_for(check):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail('test service exited unexpectedly:\n' + process.stdout.read().decode())
                    try:
                        value = check()
                        if value:
                            return value
                    except (OSError, ValueError):
                        pass
                    time.sleep(.03)
                self.fail('timed out waiting for the test service')
            def loaded_version():
                with urlopen(f'http://127.0.0.1:{port}/api/revision', timeout=.3) as response:
                    return json.load(response)
            try:
                wait_for(lambda: (root / '.probe-running').exists())
                self.assertEqual(wait_for(loaded_version), f'{process.pid}:{old}:preserved')
                (root / 'version.txt').write_text('two')
                git('add', 'version.txt')
                git('-c', 'user.name=Test', '-c', 'user.email=test@local', 'commit', '-m', 'two')
                new = git('rev-parse', 'HEAD')
                # The running execution keeps its original version until it finishes.
                self.assertEqual(loaded_version(), f'{process.pid}:{old}:preserved')
                self.assertFalse((root / '.probe-next-done').exists())
                (root / '.probe-release').touch()
                result = wait_for(lambda: json.loads((root / '.probe-next-done').read_text()))
                self.assertEqual((root / '.probe-first-done').read_text(), old)
                self.assertEqual(result, {'base': new, 'pid': process.pid, 'environment': 'preserved'})
                self.assertEqual(wait_for(loaded_version), f'{process.pid}:{new}:preserved')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdout.close()
