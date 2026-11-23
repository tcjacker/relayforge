"""拦下时告诉会话该怎么做（core/guard.py）：执行前的钩子认出一定会被拦的调用，说明该用哪条命令；rf.py 自己的用户关口也带上同一份说明。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent / 'backend'
sys.path.insert(0, str(BACKEND))
import rf  # noqa: E402
from relayforge.core import guard, trust  # noqa: E402


def call(tool, **given):
    return {'hook_event_name': 'PreToolUse', 'session_id': 'sess-1', 'tool_name': tool, 'tool_input': given}


class GuardBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.project = self.home / 'project'
        self.facts = self.project / '.relayforge'
        (self.facts / 'requirements').mkdir(parents=True)
        (self.facts / 'decisions').mkdir()
        self.drafts = self.home / 'drafts'
        self.drafts.mkdir()


class GuardTests(GuardBase):
    def test_every_user_command_has_a_hint(self):
        self.assertEqual(set(guard.HINTS), set(trust.USER_COMMANDS) | set(trust.APP_ONLY))
        self.assertTrue(all(guard.HINTS.values()))

    def test_user_commands_are_stopped_with_what_to_do_instead(self):
        target, reason = guard.guard_decision(call('Bash', command='cd x && python3 -B /a/.relayforge/bin/rf.py req done R-1'))
        self.assertEqual(target, 'rf:req done')
        self.assertIn('确认完成', reason)
        self.assertIn('issue handback', guard.guard_decision(call('Bash', command='python3 rf.py issue confirm I-001'))[1])
        self.assertEqual(guard.guard_decision(call('Bash', command='python3 rf.py summarize R-1'))[0], 'rf:summarize')
        for fine in ('grep -rn "rf.py req done" .', 'python3 rf.py note say "x"', 'python3 rf.py req brief R-1', 'git status'):
            self.assertIsNone(guard.guard_decision(call('Bash', command=fine)), fine)

    def test_edits_get_the_command_for_that_kind_of_record(self):
        facts = [str(self.facts)]
        _, reason = guard.guard_decision(call('Edit', file_path=str(self.facts / 'requirements' / 'R-1.md')), facts=facts)
        self.assertIn('note say', reason)
        self.assertIn('note check', reason)
        self.assertIn('note decision', guard.guard_decision(call('Write', file_path=str(self.facts / 'decisions' / 'D-1.md')), facts=facts)[1])
        self.assertIn('FORMAT.md', guard.guard_decision(call('Edit', file_path=str(self.facts / 'context.md')), facts=facts)[1])
        self.assertIsNone(guard.guard_decision(call('Edit', file_path=str(self.facts / 'requirements' / 'R-1.md'))))  # 这个会话没锁记录：交给权限规则

    def test_read_only_sessions_and_testers(self):
        kw = {'facts': [str(self.facts)], 'project': str(self.project), 'drafts': str(self.drafts)}
        self.assertIsNone(guard.guard_decision(call('Write', file_path=str(self.drafts / 'design.html')), kind='intake', **kw))
        _, reason = guard.guard_decision(call('Edit', file_path=str(self.project / 'app.py')), kind='intake', **kw)
        self.assertIn('立项中的需求', reason)
        self.assertIn(str(self.drafts), reason)
        self.assertIn('issue finding', guard.guard_decision(call('Edit', file_path=str(self.project / 'app.py')), kind='probe', **kw)[1])
        product = str(self.project / 'src' / 'main')
        self.assertIn('req verdict problems', guard.guard_decision(call('Edit', file_path=product + '/A.java'), product=[product], kind='test')[1])
        self.assertIsNone(guard.guard_decision(call('Edit', file_path=str(self.project / 'tests' / 't.py')), product=[product], kind='test'))
        self.assertIsNone(guard.guard_decision(call('Edit', file_path=str(self.home / 'elsewhere.py')), **kw))

    def run_hook(self, data, *flags):
        env = {**os.environ, 'RELAYFORGE_HOME': str(self.home / 'rfhome')}
        out = subprocess.run([sys.executable, str(BACKEND / 'rf.py'), 'hook', 'guard', *flags], input=json.dumps(data),
                             capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(out.returncode, 0)
        return json.loads(out.stdout)['hookSpecificOutput'] if out.stdout.strip() else None

    def test_the_hook_says_stop_from_the_second_time_on(self):
        edit = call('Edit', file_path=str(self.facts / 'requirements' / 'R-1.md'))
        first = self.run_hook(edit, '--facts', str(self.facts))
        self.assertEqual((first['hookEventName'], first['permissionDecision']), ('PreToolUse', 'deny'))
        self.assertNotIn('这已经是第', first['permissionDecisionReason'])
        second = self.run_hook(edit, '--facts', str(self.facts))
        self.assertIn('这已经是第 2 次被拦下了', second['permissionDecisionReason'])
        self.assertIsNone(self.run_hook(call('Bash', command='ls')))
        self.assertIsNone(self.run_hook({'hook_event_name': 'Stop'}))
        out = subprocess.run([sys.executable, str(BACKEND / 'rf.py'), 'hook', 'guard'], input='不是 JSON', capture_output=True, text=True, timeout=30)
        self.assertEqual((out.returncode, out.stdout), (0, ''))  # 出了错也不挡会话

    def test_the_hook_does_not_load_the_whole_program(self):
        probe = ("import runpy, sys; sys.argv = ['rf.py', 'hook', 'guard']; sys.stdin = open('/dev/null')\n"
                 "try:\n    runpy.run_path(%r, run_name='__main__')\nexcept SystemExit:\n    pass\n"
                 "print('relayforge.cli' in sys.modules)") % str(BACKEND / 'rf.py')
        out = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), 'False')

    def test_rf_py_itself_refuses_with_the_same_hint(self):
        with mock.patch.object(trust, 'app_trusted', return_value=False), mock.patch.object(trust, 'human_terminal', return_value=False), \
                mock.patch.object(trust, 'ai_session_reason', return_value='Codex'):
            with self.assertRaises(ValueError) as caught:
                trust.gate_command('req', 'done')
        self.assertIn('确认完成：这个操作只能由你在应用里做', str(caught.exception))
        self.assertIn('如果你是 AI 的会话：完成由用户试过以后在应用里点「确认完成」', str(caught.exception))


if __name__ == '__main__':
    unittest.main()


class CodexHookTests(GuardBase):
    PATCH = '*** Begin Patch\n*** Update File: {0}\n@@\n-a\n+b\n*** Add File: notes/new.md\n+x\n*** Update File: a.py\n*** Move to: b.py\n*** End Patch\n'

    def test_patch_files_are_read_from_the_whole_patch(self):
        files = guard.patch_files(self.PATCH.format('/abs/x.md'), '/repo')
        self.assertEqual(files, ['/abs/x.md', '/repo/notes/new.md', '/repo/a.py', '/repo/b.py'])

    def test_apply_patch_is_checked_file_by_file(self):
        spec = {'facts': [str(self.facts)]}
        data = {'tool_name': 'apply_patch', 'cwd': str(self.project), 'tool_input': {'command': self.PATCH.format('.relayforge/requirements/R-1.md')}}
        target, reason = guard.codex_pre(data, spec)
        self.assertTrue(target.startswith('Edit:'))
        self.assertIn('note say', reason)
        data['tool_input']['command'] = self.PATCH.format('src/app.py')
        self.assertIsNone(guard.codex_pre(data, spec))
        self.assertEqual(guard.codex_pre({'tool_name': 'Bash', 'tool_input': {'command': 'python3 rf.py req done R-1'}}, {})[0], 'rf:req done')

    def codex(self, data, sid='rf-session', **env):
        environ = {**os.environ, 'RELAYFORGE_HOME': str(self.home / 'rfhome'), **env}
        environ.pop('RELAYFORGE_SESSION_ID', None)
        if sid:
            environ['RELAYFORGE_SESSION_ID'] = sid
        out = subprocess.run([sys.executable, str(BACKEND / 'rf.py'), 'hook', 'codex'], input=json.dumps(data), capture_output=True, text=True, env=environ, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_the_codex_hook_uses_the_guard_from_the_environment(self):
        data = {'hook_event_name': 'PreToolUse', 'session_id': 'native', 'cwd': str(self.project), 'tool_name': 'apply_patch',
                'tool_input': {'command': self.PATCH.format('.relayforge/requirements/R-1.md')}}
        first = json.loads(self.codex(data, RELAYFORGE_GUARD=json.dumps({'facts': [str(self.facts)]})))['hookSpecificOutput']
        self.assertEqual(first['permissionDecision'], 'deny')
        again = json.loads(self.codex(data, RELAYFORGE_GUARD=json.dumps({'facts': [str(self.facts)]})))['hookSpecificOutput']
        self.assertIn('这已经是第 2 次被拦下了', again['permissionDecisionReason'])  # 按 RelayForge 的会话编号计数
        self.assertEqual(self.codex(data), '')  # 没有给 RELAYFORGE_GUARD：这个会话的文件不归这里管
        self.assertEqual(self.codex(data, sid=None, RELAYFORGE_GUARD=json.dumps({'facts': [str(self.facts)]})), '')  # 不是 RelayForge 开的会话

    def test_the_codex_hook_reports_status_under_the_relayforge_session(self):
        events = self.home / 'events.jsonl'
        self.codex({'hook_event_name': 'UserPromptSubmit', 'session_id': 'native', 'cwd': str(self.home), 'prompt': '做一下'}, RELAYFORGE_EVENTS=str(events))
        self.codex({'hook_event_name': 'PermissionRequest', 'session_id': 'native', 'cwd': str(self.home), 'tool_name': 'Bash'}, RELAYFORGE_EVENTS=str(events))
        self.codex({'hook_event_name': 'Stop', 'session_id': 'native', 'cwd': str(self.home), 'stop_hook_active': False, 'last_assistant_message': '做完了'},
                   RELAYFORGE_EVENTS=str(events))
        self.codex({'hook_event_name': 'Interrupt', 'session_id': 'native'}, RELAYFORGE_EVENTS=str(events))
        lines = [json.loads(x) for x in events.read_text(encoding='utf-8').splitlines()]
        self.assertEqual({x['session_id'] for x in lines}, {'rf-session'})
        self.assertEqual([(x['status'], x.get('kind')) for x in lines], [('running', None), ('waiting', 'approve'), ('waiting', 'reply'), ('idle', None)])
        self.codex({'hook_event_name': 'Stop', 'session_id': 'native'}, sid=None, RELAYFORGE_EVENTS=str(events))
        self.assertEqual(len(events.read_text(encoding='utf-8').splitlines()), 4)  # 不是 RelayForge 开的会话：不记
