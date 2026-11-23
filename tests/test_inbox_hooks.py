"""会话收件箱的钩子一侧（core/inbox.py、project/status.hook_event）：这一轮说完时由 Stop 钩子把排给这个会话的话交过去，
唤醒句由 UserPromptSubmit 钩子把正文作为补充的上下文交过去；每条只交一次，最后一句在问用户时不交。"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent / 'backend'
sys.path.insert(0, str(BACKEND))
from relayforge.core import inbox, model  # noqa: E402
from relayforge.requirements.record import clarity, gate  # noqa: E402
from relayforge.project.status import hook_event  # noqa: E402


def message(mid, session, text, state='queued', label='测试'):
    return {'id': mid, 'to': 'R-1', 'from': 'testing', 'label': label, 'text': text, 'state': state, 'session': session}


class InboxHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / 'inbox.json'
        self.file.write_text(json.dumps([
            message('m-aaaaaa', 'sid-1', '测试交回了 2 条问题'),
            message('m-bbbbbb', 'sid-1', '再看一下合计行', label='问题'),
            message('m-cccccc', 'sid-2', '别的会话的'),
            message('m-dddddd', 'sid-1', '已经送过的', state='sent'),
        ], ensure_ascii=False), encoding='utf-8')
        self.events = Path(self.tmp.name) / 'events.jsonl'
        env = mock.patch.dict(os.environ, {'RELAYFORGE_INBOX': str(self.file), 'RELAYFORGE_EVENTS': str(self.events),
                                           'RELAYFORGE_HOME': str(Path(self.tmp.name) / 'home')})
        env.start()
        self.addCleanup(env.stop)

    def run_hook(self, data):
        buf = io.StringIO()
        with redirect_stdout(buf):
            hook_event({'cwd': self.tmp.name, **data})
        out = buf.getvalue().strip()
        return json.loads(out) if out else None

    def delivered(self):
        path = Path(str(self.file) + '.delivered')
        return [line.split('\t')[:2] for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def test_stop_hands_over_what_waits_for_this_session_once(self):
        out = self.run_hook({'hook_event_name': 'Stop', 'session_id': 'sid-1', 'last_assistant_message': '改好了。'})
        self.assertEqual(out['decision'], 'block')
        self.assertEqual(out['reason'], '〔RelayForge 程序 · 测试 · m-aaaaaa〕\n测试交回了 2 条问题\n\n〔RelayForge 程序 · 问题 · m-bbbbbb〕\n再看一下合计行')
        self.assertEqual(self.delivered(), [['m-aaaaaa', 'stop'], ['m-bbbbbb', 'stop']])
        self.assertFalse(self.events.exists())  # 拦下了：这一轮没说完，不报「回复完了」
        self.assertIsNone(self.run_hook({'hook_event_name': 'Stop', 'session_id': 'sid-1', 'last_assistant_message': '处理完了。', 'stop_hook_active': True}))
        self.assertIn('"kind": "reply"', self.events.read_text(encoding='utf-8'))  # 没有新的：照常说完

    def test_a_question_to_the_user_is_not_answered_by_the_program(self):
        self.assertIsNone(self.run_hook({'hook_event_name': 'Stop', 'session_id': 'sid-1', 'last_assistant_message': '要我接着改吗？'}))
        self.assertEqual(self.delivered(), [])
        self.assertIn('"kind": "answer"', self.events.read_text(encoding='utf-8'))

    def test_the_wake_line_brings_the_text_in_as_context(self):
        prompt = inbox.WAKE + '，正文在这条消息附带的上下文里，请按它处理。'
        out = self.run_hook({'hook_event_name': 'UserPromptSubmit', 'session_id': 'sid-1', 'prompt': prompt})
        context = out['hookSpecificOutput']['additionalContext']
        self.assertEqual(out['hookSpecificOutput']['hookEventName'], 'UserPromptSubmit')
        self.assertIn('m-aaaaaa', context)
        self.assertIn('再看一下合计行', context)
        self.assertNotIn('别的会话的', context)
        self.assertEqual([d[1] for d in self.delivered()], ['prompt', 'prompt'])
        again = self.run_hook({'hook_event_name': 'UserPromptSubmit', 'session_id': 'sid-1', 'prompt': prompt})
        self.assertIn('已经交过了', again['hookSpecificOutput']['additionalContext'])
        self.assertIsNone(self.run_hook({'hook_event_name': 'UserPromptSubmit', 'session_id': 'sid-1', 'prompt': '普通的一句话'}))

    def test_requirement_copy_uses_app_language_and_keeps_workflow_keys(self):
        for language, expected in [('en', 'Acceptance criteria'), ('zh-CN', '验收条件')]:
            with self.subTest(language=language), mock.patch.dict(os.environ, {'RELAYFORGE_LANGUAGE': language}):
                level, steps = clarity({})
                self.assertEqual(level, 'idea')
                self.assertEqual([step['key'] for step in steps], ['idea', 'direction', 'design', 'acceptance', 'cases'])
                self.assertEqual(steps[3]['missing'], [expected])
                self.assertFalse(gate({})['ok'])

    def test_next_turn_reads_the_changed_language_and_keeps_inbox_context(self):
        home = Path(os.environ['RELAYFORGE_HOME']).resolve()
        home.mkdir()
        data = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'sid-1', 'prompt': 'Continue'}
        with mock.patch.dict(os.environ, {'RELAYFORGE_LANGUAGE': 'en'}):
            self.assertIn('Use English', self.run_hook(data)['hookSpecificOutput']['additionalContext'])
            (home / 'settings.json').write_text('{"language":"zh-CN"}', encoding='utf-8')
            data['prompt'] = inbox.WAKE
            context = self.run_hook(data)['hookSpecificOutput']['additionalContext']
            self.assertIn('使用简体中文', context)
            self.assertIn('m-aaaaaa', context)

    def test_background_models_use_the_language_without_changing_the_schema(self):
        root = Path(self.tmp.name).resolve()
        for engine, language, expected in [('claude', 'zh-CN', '使用简体中文'), ('codex', 'en', 'Use English')]:
            with self.subTest(engine=engine), mock.patch.dict(os.environ, {'RELAYFORGE_LANGUAGE': language}), \
                    mock.patch.object(model, 'extractor_command', return_value=[engine]), \
                    mock.patch.object(model.procs, 'run', return_value=(0, '{"status":"ok"}', '')) as run:
                self.assertEqual(model.run_model(root, 'Return JSON with a status field.', 'Project facts'), {'status': 'ok'})
                args, kwargs = run.call_args
                prompt = kwargs['input'] if engine == 'codex' else args[0][-1]
                self.assertIn('Return JSON with a status field.', prompt)
                self.assertIn(expected, prompt)

    def test_without_an_inbox_nothing_happens(self):
        with mock.patch.dict(os.environ, {'RELAYFORGE_INBOX': ''}):
            self.assertEqual(inbox.inbox_deliver('sid-1', 'stop'), '')
        self.assertEqual(inbox.inbox_deliver('', 'stop'), '')
        self.file.write_text('不是 JSON', encoding='utf-8')
        self.assertEqual(inbox.inbox_pending('sid-1'), [])

    def test_codex_sessions_get_it_under_the_relayforge_session(self):
        env = {**os.environ, 'RELAYFORGE_SESSION_ID': 'sid-1'}
        out = subprocess.run([sys.executable, str(BACKEND / 'rf.py'), 'hook', 'codex'], input=json.dumps(
            {'hook_event_name': 'Stop', 'session_id': 'codex-native', 'cwd': self.tmp.name, 'last_assistant_message': '好了'}),
            capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('测试交回了 2 条问题', json.loads(out.stdout)['reason'])


if __name__ == '__main__':
    unittest.main()
