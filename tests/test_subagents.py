import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from relayforge.requirements.subagents import intake_decisions  # noqa: E402


class SubagentDecisionTests(unittest.TestCase):
    def test_only_explicit_user_messages_decide(self):
        entries = [
            {'type': 'assistant', 'message': {'content': '这个需求可以用 subagent，请用 Claude Code。'}},
            {'type': 'user', 'message': {'content': '这个需求可以用 subagent 吗？'}},
            {'type': 'user', 'message': {'content': '不要说「这个需求可以用 subagent」。'}},
            {'type': 'user', 'isSidechain': True, 'message': {'content': '这个需求可以用 subagent。'}},
            {'type': 'user', 'message': {'content': '这个需求可以用 subagent，请用 Claude Code。'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': '这个需求可以用 subagent，沿用设置。'}]}},
            {'type': 'user', 'message': {'content': '撤回 subagent 授权。'}},
            {'type': 'user', 'message': {'content': 'This requirement can use subagents?'}},
            {'type': 'user', 'message': {'content': 'This requirement can use subagents, use Codex.'}},
            {'type': 'user', 'message': {'content': 'Revoke subagent authorization.'}},
        ]
        with tempfile.TemporaryDirectory() as temp:
            transcript = Path(temp).resolve() / 'intake.jsonl'
            transcript.write_text('\n'.join(map(json.dumps, entries)), encoding='utf-8')
            self.assertEqual(intake_decisions(transcript), [
                {'number': 4, 'authorized': True, 'cli': 'claude'},
                {'number': 5, 'authorized': True, 'cli': ''},
                {'number': 6, 'authorized': False},
                {'number': 8, 'authorized': True, 'cli': 'codex'},
                {'number': 9, 'authorized': False},
            ])
