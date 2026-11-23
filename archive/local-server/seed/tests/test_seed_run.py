import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import seed
import seed_run


class RunAcceptance(unittest.TestCase):
    def test_public_progress_arrives_before_process_finishes(self):
        with TemporaryDirectory() as tmp:
            records = []
            started = time.monotonic()
            script = ('import json,time; '
                      'print(json.dumps({"type":"item.started","item":{"type":"command_execution",'
                      '"command":"SECRET_ARGUMENT"}}),flush=True); time.sleep(2.5)')
            record = seed_run.execute([sys.executable, '-c', script], tmp, '', Path(tmp) / 'logs',
                                      'Worker', timeout=5,
                                      on_progress=lambda r: records.append((time.monotonic()-started, r)))
            live = [(t,r) for t,r in records if r['activity'] == '命令执行开始' and 'outcome' not in r]
            self.assertTrue(live)
            self.assertLess(live[0][0], 2.5)
            self.assertNotIn('SECRET_ARGUMENT', json.dumps(records))
            self.assertIn('SECRET_ARGUMENT', Path(record['log_path']).read_text())
            task = 'example'
            rows = [{'state':'queued','seq':1,'recorded_at':'now','data':{'goal':'test'}},
                    {'state':'working','seq':2,'recorded_at':'now','data':{'progress':live[0][1]}}]
            with patch.object(seed.engine, 'git', return_value='base'):
                page = seed.page({task: rows}, 'base')
            self.assertIn('最近活动', page)
            self.assertIn(record['run_id'], page)
            self.assertNotIn('SECRET_ARGUMENT', page)

    def test_timeout_retains_log_and_stops_descendant(self):
        with TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'leaked'
            child = f'import time;from pathlib import Path;time.sleep(1);Path({str(marker)!r}).touch()'
            script = ('import subprocess,sys,time,json; '
                      f'subprocess.Popen([sys.executable,"-c",{child!r}]); '
                      'print(json.dumps({"type":"turn.started"}),flush=True); time.sleep(30)')
            with self.assertRaises(seed_run.RunError) as caught:
                seed_run.execute([sys.executable, '-c', script], tmp, '', Path(tmp) / 'logs',
                                 'Worker', timeout=.3)
            record = caught.exception.record
            self.assertEqual(record['outcome'], 'interrupted')
            self.assertEqual(record['activity'], '模型开始处理')
            self.assertTrue(Path(record['log_path']).exists())
            time.sleep(1.1)
            self.assertFalse(marker.exists())

    def test_nonzero_exit_is_interrupted(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(seed_run.RunError) as caught:
                seed_run.execute([sys.executable, '-c', 'raise SystemExit(7)'], tmp, '', tmp,
                                 '主 Agent', timeout=2)
            self.assertIn('7', str(caught.exception))
            self.assertEqual(caught.exception.record['outcome'], 'interrupted')
