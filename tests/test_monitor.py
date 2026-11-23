"""会话监控（rf.py monitor）：从会话记录里由程序算出规矩做到了没有、工具用了哪些、在哪里打转。"""
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from relayforge.sessions import monitor as monitor_module  # noqa: E402
from tests.test_history import HistoryBase, _j  # noqa: E402

DEV = 'cccc1111-2222-3333-4444-555566667777'
CODEX = 'dddd1111-2222-3333-4444-555566667777'


def claude_dev(place, req_file):
    """A development session: reads the requirement, edits code before any plan, notes, commits; then goes round in circles."""
    n = iter(range(1, 100))

    def use(name, data, ts):
        i = next(n)
        return i, {'type': 'assistant', 'timestamp': ts, 'cwd': str(place), 'message': {'id': f'm{i}', 'role': 'assistant',
                   'content': [{'type': 'tool_use', 'id': f't{i}', 'name': name, 'input': data}]}}

    def result(i, text, ts, error=False):
        return {'type': 'user', 'timestamp': ts, 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': f't{i}', 'content': text, 'is_error': error}]}}

    def say(text, ts, role='user'):
        content = text if role == 'user' else [{'type': 'text', 'text': text}]
        return {'type': role, 'timestamp': ts, 'message': {'role': role, 'content': content}}

    lines = [say('开始做导出', '2026-11-18T01:00:00Z')]
    for name, data, out, error in [
        ('Read', {'file_path': f'{place}/{req_file}'}, '需求', False),
        ('Edit', {'file_path': f'{place}/export.py', 'old_string': 'a', 'new_string': 'b'}, 'ok', False),
        ('Bash', {'command': 'python3 /x/.relayforge/bin/rf.py note doing "导出做了一半"'}, 'ok', False),
        ('Bash', {'command': 'grep -rn "rf.py req done" .'}, '', False),  # 只是查文字，不算运行了 req done
        ('Bash', {'command': 'git commit -qm "导出"'}, '', False),
        ('Edit', {'file_path': f'{place}/.relayforge/requirements/{Path(req_file).name}', 'old_string': 'a', 'new_string': 'b'},
         '<tool_use_error>File is in a directory that is denied by your permission settings.</tool_use_error>', True),
        ('Edit', {'file_path': f'{place}/.relayforge/requirements/{Path(req_file).name}', 'old_string': 'a', 'new_string': 'c'},
         '<tool_use_error>File is in a directory that is denied by your permission settings.</tool_use_error>', True),
    ]:
        i, line = use(name, data, '2026-11-18T01:01:00Z')
        lines += [line, result(i, out, '2026-11-18T01:01:05Z', error)]
    for _ in range(3):
        i, line = use('Bash', {'command': 'npm test'}, '2026-11-18T01:02:00Z')
        lines += [line, result(i, 'Exit code 1\nfailed', '2026-11-18T01:02:10Z', True)]
    i, line = use('Bash', {'command': 'npm run e2e'}, '2026-11-18T01:03:00Z')
    lines += [line, result(i, 'ok', '2026-11-18T01:20:00Z')]  # 等这条命令等了 17 分钟
    lines += [say('好了', '2026-11-18T01:21:00Z', 'assistant'),
              say('This session is being continued from a previous conversation that ran out of context.', '2026-11-18T05:00:00Z'),
              say('[Request interrupted by user]', '2026-11-18T05:00:01Z'),
              say('再看一下', '2026-11-18T06:00:00Z'), say('看过了', '2026-11-18T06:00:30Z', 'assistant')]
    return _j(lines)


def codex_dev(place):
    """A Codex session: a command blocked by the sandbox, an rf.py command, a turn the user broke off."""
    def item(ts, payload, kind='response_item'):
        return {'timestamp': ts, 'type': kind, 'payload': payload}
    return _j([
        item('2026-11-19T01:00:00Z', {'cwd': str(place)}, 'session_meta'),
        item('2026-11-19T01:00:00Z', {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '<environment_context>x</environment_context>'}]}),
        item('2026-11-19T01:00:01Z', {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '合并一下'}]}),
        item('2026-11-19T01:00:02Z', {'type': 'function_call', 'name': 'exec_command', 'call_id': 'c1', 'arguments': json.dumps({'cmd': 'git merge main'})}),
        item('2026-11-19T01:00:03Z', {'type': 'function_call_output', 'call_id': 'c1', 'output': json.dumps({'exit_code': 128, 'output': 'fatal: Operation not permitted'})}),
        item('2026-11-19T01:00:04Z', {'type': 'function_call', 'name': 'exec_command', 'call_id': 'c2',
                                      'arguments': json.dumps({'cmd': 'python3 -B .relayforge/bin/rf.py note plan "先合并"'})}),
        item('2026-11-19T01:00:05Z', {'type': 'function_call_output', 'call_id': 'c2', 'output': json.dumps({'exit_code': 0, 'output': 'ok'})}),
        item('2026-11-19T01:00:06Z', {'type': 'turn_aborted'}, 'event_msg'),
    ])


class MonitorBase(HistoryBase):
    def setUp(self):
        super().setUp()
        self.made = self.requirement('监控', sessions=(DEV, CODEX), finish=False)
        place = Path(self.made['worktree']).resolve()
        self.say(place, DEV, subagent=False).write_text(claude_dev(place, self.made['file']), encoding='utf-8')
        folder = self.claude.parent / 'codex' / 'sessions' / '2026' / '11' / '19'
        folder.mkdir(parents=True)
        (folder / f'rollout-2026-11-19T01-00-00-{CODEX}.jsonl').write_text(codex_dev(place), encoding='utf-8')
        self.tag = rf.stable_tag(self.root, DEV)

    def mine(self, out, sid):
        return next(s for s in out['sessions'] if s['uuid'] == sid)


class MonitorTests(MonitorBase):

    def test_rules_tools_and_loops_of_a_development_session(self):
        out = rf.monitor(self.root, kinds=['develop'])
        dev = self.mine(out, DEV)
        self.assertEqual(dev['kind'], 'develop')
        self.assertEqual(dev['rules'], {'read': True, 'plan': False, 'note': True, 'commit': True, 'no-done': True, 'no-hand': True})
        self.assertEqual(dev['turns'], 2)  # 续接的说明和打断都不算一轮
        self.assertEqual(dev['interrupts'], 1)
        self.assertEqual(dev['status'], {'ok': 6, 'blocked': 2, 'failed': 3})
        kinds = [l['kind'] for l in dev['loops']]
        self.assertEqual(kinds, ['blocked-retry', 'repeat-fail', 'stall', 'interrupted'])
        stall = dev['loops'][2]
        self.assertEqual((stall['turn'], stall['times']), (1, 17 * 60))
        self.assertIn('npm run e2e', stall['text'])
        self.assertEqual(len(dev['stalls']), 1)  # 回复完以后到续接、到下一轮之间的几个小时是在等用户，不算停住
        why = next(g for g in out['guided'] if g['command'] == 'why')
        self.assertEqual((why['used'], why['applies']), (0, 1))  # Codex 那个会话没改代码
        rf_cmds = {t['name']: t for t in out['tools']['rf']}
        self.assertNotIn('req done', rf_cmds)
        self.assertEqual(rf_cmds['note doing']['calls'], 1)
        self.assertEqual(rf_cmds['note plan']['sessions'], 1)
        plan = next(r for r in out['rules'] if r['key'] == 'plan')
        self.assertEqual((plan['applies'], plan['kept'], plan['broke']), (1, 0, [{'tag': self.tag, 'owner': self.made['id']}]))

    def test_codex_sessions_count_sandbox_blocks_and_aborted_turns(self):
        out = rf.monitor(self.root, session=CODEX)
        codex = self.mine(out, CODEX)
        self.assertEqual((codex['turns'], codex['interrupts'], codex['status']), (1, 1, {'blocked': 1, 'ok': 1}))
        self.assertEqual(codex['blocked'][0]['detail'], 'git merge main')
        self.assertEqual(codex['rules']['plan'], None)  # 一行代码都没改：写不写计划不看

    def test_text_report_and_cli(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rf.main(['monitor', '--root', str(self.root)])
        text = buf.getvalue()
        self.assertEqual(code, 0)
        for part in ('会话监控：2 个会话（开发 2）', '✗ 开发：动手前用 note plan 写计划　0/1', 'rf.py why：0/1', '从来没用过',
                     f'{self.tag}（开发，{self.made["id"]}）【1】被规则拦下后又试了 1 次：Edit', '同一步失败了 3 次：跑 npm test',
                     '等「跑 npm run e2e」的结果等了 17 分钟', '你打断了 1 次'):
            self.assertIn(part, text)
        self.assertNotIn('找不到', text)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rf.main(['monitor', '--root', str(self.root), '--kind', 'test', '--json'])
        self.assertEqual(json.loads(buf.getvalue())['sessions'], [])
        with self.assertRaises(ValueError):
            rf.monitor(self.root, since='昨天')
        self.assertEqual(rf.monitor(self.root, since='2026-11-19')['sessions'][0]['uuid'], CODEX)

    def test_a_session_still_going_is_not_held_to_what_it_owes_at_the_end(self):
        with mock.patch.object(monitor_module, 'RECENT', 10 ** 10):
            dev = self.mine(rf.monitor(self.root, session=DEV), DEV)
        self.assertTrue(dev['live'])
        self.assertIsNone(dev['rules']['commit'])
        self.assertTrue(dev['rules']['note'])


class RecordTests(MonitorBase):
    def lines(self):
        place = rf.monitor_dir(self.root)
        return [json.loads(x) for f in sorted(place.glob('events-*.jsonl')) for x in f.read_text(encoding='utf-8').splitlines()]

    def test_each_finished_turn_is_written_once_with_its_approvals(self):
        out = rf.monitor_record(self.root, DEV, 'develop', self.made['id'], 'claude',
                                waits=[{'at': '2026-11-18T01:05:00Z', 'seconds': 42}, {'at': '2026-11-18T06:00:10Z', 'seconds': 5}])
        self.assertEqual(out['written'], 2)
        first, second = self.lines()
        self.assertEqual((first['session'], first['kind'], first['owner'], first['agent'], first['turn'], first['done']),
                         (self.tag, 'develop', self.made['id'], 'claude', 1, True))
        self.assertEqual(first['said'], '开始做导出')
        self.assertEqual(first['approvals'], [{'at': '2026-11-18T01:05:00Z', 'seconds': 42}])
        self.assertEqual(second['approvals'], [{'at': '2026-11-18T06:00:10Z', 'seconds': 5}])
        self.assertEqual(first['calls_total'], 11)
        self.assertEqual([c['status'] for c in first['calls']].count('blocked'), 2)
        self.assertEqual(first['calls'][2]['rf'], ['note doing'])
        self.assertEqual(first['stalls'][0]['waiting'], True)
        self.assertEqual(second['interrupts'], 0)
        self.assertEqual(first['interrupts'], 1)  # 续接说明之后的打断还算在第一轮
        self.assertEqual(rf.monitor_record(self.root, DEV, 'develop')['written'], 0)  # 再记一次不重复
        self.assertEqual(len(self.lines()), 2)
        guide = (rf.monitor_dir(self.root) / 'README.md').read_text(encoding='utf-8')
        self.assertIn('busy_seconds', guide)
        self.assertNotIn('SECRET', json.dumps(self.lines(), ensure_ascii=False))

    def test_an_unfinished_last_turn_waits_until_it_ends_or_the_session_closes(self):
        place = Path(self.made['worktree']).resolve()
        path = self.claude / 'projects'
        transcript = next(path.rglob(f'{DEV}.jsonl'))
        transcript.write_text(transcript.read_text(encoding='utf-8') + _j([
            {'type': 'user', 'timestamp': '2026-11-18T07:00:00Z', 'message': {'role': 'user', 'content': '跑一下 TOKEN=sk-abc 的测试'}},
            {'type': 'assistant', 'timestamp': '2026-11-18T07:00:05Z', 'cwd': str(place), 'message': {'id': 'mx', 'role': 'assistant',
             'content': [{'type': 'tool_use', 'id': 'tx', 'name': 'Bash', 'input': {'command': 'npm test'}}]}}]), encoding='utf-8')
        self.assertEqual(rf.monitor_record(self.root, DEV, 'develop', transcript=str(transcript))['written'], 2)
        self.assertEqual(rf.monitor_record(self.root, DEV, 'develop', waits=[{'at': '2026-11-18T07:00:06Z', 'seconds': 9}])['pending_waits'], 1)
        out = rf.monitor_record(self.root, DEV, 'develop', closed=True)
        self.assertEqual(out['written'], 1)
        last = self.lines()[-1]
        self.assertEqual((last['turn'], last['done'], last['calls'][0]['status']), (3, False, 'pending'))
        self.assertEqual(last['approvals'], [{'at': '2026-11-18T07:00:06Z', 'seconds': 9}])
        self.assertIn('TOKEN=***', last['said'])

    def test_cli_record(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rf.main(['monitor', 'record', '--root', str(self.root), '--session', CODEX, '--kind', 'develop', '--agent', 'codex', '--json'])
        self.assertEqual((code, json.loads(buf.getvalue())['written']), (0, 1))
        self.assertEqual(self.lines()[0]['agent'], 'codex')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(rf.main(['monitor', 'record', '--root', str(self.root), '--session', CODEX, '--waits', '不是 JSON']), 1)
