"""Run independent fix landings against the same real Git repository."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


BACKEND = Path(__file__).resolve().parents[1] / 'backend'
LAND_FIX = """
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

from relayforge.issues import fixes as issue_fixes
from relayforge.requirements import record as requirements

home, gates = map(Path, sys.argv[1:3])
branch = sys.argv[3]
ident = {'first': 'F-001', 'second': 'F-002'}[branch]
dump, run_git = json.dump, requirements.run_git

def wait_for(name):
    deadline = time.monotonic() + 15
    while not (gates / name).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(name)
        time.sleep(0.01)

def pause_before_writing_holder(*args, **kwargs):
    (gates / 'writing-holder').touch()
    wait_for('write-holder')
    return dump(*args, **kwargs)

def observe_merge(place, *args, **kwargs):
    if args[:2] == ('merge', '--no-ff'):
        (gates / 'second-at-git').touch()
        wait_for('second-merge')
    return run_git(place, *args, **kwargs)

try:
    with patch.object(requirements.json, 'dump', pause_before_writing_holder if branch == 'first' else dump), \\
         patch.object(requirements, 'run_git', observe_merge if branch == 'second' else run_git):
        issue_fixes.land_fix(home, {'id': ident, 'worktree': str(gates / branch), 'branch': branch, 'issues': ['I-001']})
    print('landed')
except requirements.MergeBusy as error:
    print('busy', error.holder or 'unknown')
except requirements.NeedsSync:
    print('needs sync')
except ValueError as error:
    print('failed', error)
"""


class MergeLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.gates = Path(temporary.name)
        self.home = self.gates / 'repo'
        self.git(self.gates, 'init', '-q', '-b', 'main', str(self.home))
        self.git(self.home, 'config', 'user.name', 'Test')
        self.git(self.home, 'config', 'user.email', 'test@example.com')
        self.git(self.home, 'config', 'core.hooksPath', str(self.home / '.git' / 'hooks'))
        self.git(self.home, 'commit', '-qm', 'base', '--allow-empty')
        for branch in ('first', 'second'):
            place = self.gates / branch
            self.git(self.home, 'worktree', 'add', '-qb', branch, str(place))
            (place / f'{branch}.txt').write_text(branch, encoding='utf-8')
            self.git(place, 'add', '.')
            self.git(place, 'commit', '-qm', branch)

    def git(self, place, *args):
        return subprocess.run(['git', '-C', str(place), *args], check=True, capture_output=True, text=True).stdout.strip()

    def land(self, branch):
        env = dict(os.environ, PYTHONPATH=str(BACKEND))
        process = subprocess.Popen([sys.executable, '-c', LAND_FIX, str(self.home), str(self.gates), branch],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, process)
        return process

    def stop(self, process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=15)

    def wait_for(self, name, process=None):
        deadline = time.monotonic() + 15
        while not (self.gates / name).exists():
            if process is not None and process.poll() is not None:
                return False
            if time.monotonic() >= deadline:
                self.fail(f'Timed out waiting for {name}')
            time.sleep(0.01)
        return True

    def result(self, process):
        stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, stderr)
        return stdout.strip()

    def test_second_fix_waits_even_before_first_fix_publishes_its_holder(self):
        hook = self.home / '.git' / 'hooks' / 'pre-merge-commit'
        hook.write_text(
            '#!/bin/sh\n'
            'touch ../first-at-hook\n'
            'for i in $(seq 1 750); do\n'
            '    test -f ../finish-first && exit 0\n'
            '    sleep 0.02\n'
            'done\n'
            'exit 1\n',
            encoding='utf-8',
        )
        hook.chmod(0o755)
        first = self.land('first')
        try:
            self.assertTrue(self.wait_for('writing-holder', first))
            second = self.land('second')
            entered_git = self.wait_for('second-at-git', second)
            (self.gates / 'write-holder').touch()
            self.assertTrue(self.wait_for('first-at-hook', first))
            (self.gates / 'second-merge').touch()
            self.assertEqual(self.result(second), 'busy unknown')
            self.assertFalse(entered_git)
            self.assertEqual(self.result(self.land('second')), 'busy F-001')
        finally:
            (self.gates / 'finish-first').touch()
            (self.gates / 'write-holder').touch()
            (self.gates / 'second-merge').touch()
            first_result = self.result(first)

        self.assertEqual(first_result, 'landed')
        # main moved on without a conflict: the program merges it into the second branch itself, then lands
        self.assertEqual(self.result(self.land('second')), 'landed')
        self.assertEqual(self.git(self.home, 'ls-tree', '--name-only', 'HEAD').splitlines(),
                         ['first.txt', 'second.txt'])

    def test_process_exit_releases_lock_without_waiting_for_pid_cleanup(self):
        first = self.land('first')
        self.assertTrue(self.wait_for('writing-holder', first))
        first.kill()
        first.communicate(timeout=15)
        (self.gates / 'second-merge').touch()
        self.assertEqual(self.result(self.land('second')), 'landed')
