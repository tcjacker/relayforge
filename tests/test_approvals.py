"""无界面运行的会话要你批准时（core/approvals.py、status.hook_event）：钩子把请求交给应用，等你在应用里点批准或拒绝。"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from relayforge.core import approvals  # noqa: E402
from relayforge.project.status import hook_event  # noqa: E402


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / 'approvals'
        env = mock.patch.dict(os.environ, {'RELAYFORGE_APPROVALS': str(self.folder), 'RELAYFORGE_HEADLESS': '1', 'RELAYFORGE_SESSION_ID': 'sid-1',
                                           'RELAYFORGE_EVENTS': str(Path(self.tmp.name) / 'events.jsonl'), 'RELAYFORGE_HOME': str(Path(self.tmp.name) / 'home')})
        env.start()
        self.addCleanup(env.stop)

    def answer_when_asked(self, decision):
        def go():
            for _ in range(200):
                found = list(self.folder.glob('*.json')) if self.folder.exists() else []
                if found:
                    self.seen = json.loads(found[0].read_text(encoding='utf-8'))
                    (self.folder / (self.seen['id'] + '.decision')).write_text(json.dumps(decision), encoding='utf-8')
                    return
                time.sleep(0.05)
        t = threading.Thread(target=go)
        t.start()
        return t

    def hook(self, data):
        buf = io.StringIO()
        with redirect_stdout(buf):
            hook_event({'cwd': self.tmp.name, 'session_id': 'claude-sid', **data})
        return buf.getvalue().strip()

    def test_the_hook_waits_for_the_user_and_passes_the_answer_on(self):
        t = self.answer_when_asked({'behavior': 'allow'})
        out = json.loads(self.hook({'hook_event_name': 'PermissionRequest', 'tool_name': 'Bash',
                                    'tool_input': {'command': 'npm install x', 'description': '装依赖'}}))
        t.join()
        self.assertEqual(out, {'hookSpecificOutput': {'hookEventName': 'PermissionRequest', 'decision': {'behavior': 'allow'}}})
        self.assertEqual((self.seen['session'], self.seen['tool'], self.seen['input']['command'], self.seen['description']), ('sid-1', 'Bash', 'npm install x', '装依赖'))
        self.assertEqual(list(self.folder.iterdir()), [])  # 回答完了，请求和回答都收掉
        self.assertIn('"kind": "approve"', (Path(self.tmp.name) / 'events.jsonl').read_text(encoding='utf-8'))  # 先报了「在等你批准」

    def test_a_refusal_carries_the_reason(self):
        t = self.answer_when_asked({'behavior': 'deny', 'message': '别装新依赖'})
        out = json.loads(self.hook({'hook_event_name': 'PermissionRequest', 'tool_name': 'Bash', 'tool_input': {'command': 'npm install x'}}))
        t.join()
        self.assertEqual(out['hookSpecificOutput']['decision'], {'behavior': 'deny', 'message': '别装新依赖'})

    def test_waiting_too_long_says_no(self):
        got = approvals.wait_for_decision({'tool_name': 'Bash', 'tool_input': {'command': 'x'}}, wait=0.2, poll=0.05)
        self.assertEqual(got['behavior'], 'deny')
        self.assertIn('没有人在应用里批准', got['message'])

    def test_an_interactive_session_is_left_to_the_terminal(self):
        with mock.patch.dict(os.environ, {'RELAYFORGE_HEADLESS': ''}):
            self.assertEqual(self.hook({'hook_event_name': 'PermissionRequest', 'tool_name': 'Bash', 'tool_input': {'command': 'x'}}), '')
        self.assertFalse(self.folder.exists())


if __name__ == '__main__':
    unittest.main()
