"""演示模板集成检查：数据完整、真正隔离的工作树、重复启动不覆盖。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DemoProjectTests(unittest.TestCase):
    def test_builds_all_ui_states_and_preserves_existing_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            target = home / 'demo'
            env = {**os.environ, 'RELAYFORGE_HOME': str(home / 'registry')}
            command = [sys.executable, '-B', str(ROOT / 'test-project/build_demo.py'), '--target', str(target), '--backend', str(ROOT / 'backend/rf.py')]
            built = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
            self.assertTrue(json.loads(built.stdout)['created'])
            manifest = json.loads((target / '.relayforge/demo.json').read_text())
            self.assertEqual(len(manifest['requirements']), 6)
            self.assertEqual(len(manifest['sessions']), 9)
            self.assertTrue((target / '.git').is_dir())
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'backend/rf.py'), 'req', 'list', '--root', str(target), '--json'], check=True, capture_output=True, text=True, env=env)
            reqs = json.loads(result.stdout)
            self.assertEqual({r['state'] for r in reqs}, {'draft', 'open', 'done', 'dropped'})
            self.assertEqual(sum(bool(r['worktree']) for r in reqs), 4)
            self.assertTrue(any(r['state'] == 'open' and r['done'] == r['steps'] and r['steps'] for r in reqs))
            self.assertTrue(any(r['state'] == 'draft' and r['level'] == 'acceptance' and r['designs'] for r in reqs))
            self.assertTrue(any(r['state'] == 'done' and r['designs'][0]['status'] == 'current' for r in reqs))
            for folder, count in [('modules', 5), ('features', 10), ('journeys', 5), ('documents', 3), ('decisions', 2)]:
                self.assertEqual(len(list((target / '.relayforge' / folder).glob('*.md'))), count)
            self.assertTrue((target / '.relayforge/site/index.html').is_file())
            self.assertIn('FlowNote', (target / 'src/index.html').read_text())
            edited = target / 'my-demo-notes.txt'
            edited.write_text('Keep my changes')
            rerun = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
            self.assertFalse(json.loads(rerun.stdout)['created'])
            self.assertEqual(edited.read_text(), 'Keep my changes')

    def test_refuses_to_overwrite_an_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            sentinel = target / 'keep.txt'
            sentinel.write_text('original')
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'test-project/build_demo.py'), '--target', str(target), '--backend', str(ROOT / 'backend/rf.py')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('不会覆盖', result.stderr)
            self.assertEqual(sentinel.read_text(), 'original')
